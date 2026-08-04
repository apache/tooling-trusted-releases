# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

# Removing this will cause circular imports
from __future__ import annotations

from typing import Final

import sqlalchemy
import sqlmodel

import atr.catalog_site as catalog_site
import atr.cycles as cycles
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.catalogue_diff as catalogue_diff
import atr.shared.catalogue_import as catalogue_import
import atr.shared.catalogue_rows as catalogue_rows
import atr.shared.repoint as repoint
import atr.storage as storage

# SQLite allows 999 bound variables by default, and an artifact key is three of them
_DELETE_CHUNK: Final[int] = 300


class FoundationAdmin:
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session):
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def move_project(self, project_key: safe.ProjectKey, dest_committee_key: safe.CommitteeKey) -> None:
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            project = await self.__data.project(key=str(project_key)).get()
            if project is None:
                raise storage.AccessError(f"Project '{project_key}' not found.", status=404)
            await self._ensure_catalog_only(str(project_key))
            dest = await self.__data.committee(key=str(dest_committee_key)).get()
            if dest is None:
                raise storage.AccessError(f"No destination committee '{dest_committee_key}'.", status=404)
            project.committee_key = str(dest_committee_key)
            project.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            # The project changes committee, so both the old and new indexes go stale
            await catalog_site.queue_full_regeneration(self.__data, self.__asf_uid)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            moved_to=str(dest_committee_key),
        )

    async def move_release(self, release_key: safe.ReleaseKey, target_project_key: safe.ProjectKey) -> None:
        old_key = str(release_key)
        to_project = str(target_project_key)
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            release = await self.__data.get(sql.Release, old_key)
            if release is None:
                raise storage.AccessError(f"Release '{old_key}' not found.", status=404)
            # Capture the source project and version before the re-point expires the row
            from_project = release.project_key
            version = release.version
            await self._ensure_release_catalog_only(old_key)
            if await self.__data.project(key=to_project).get() is None:
                raise storage.AccessError(f"Target project '{to_project}' not found.", status=404)
            if from_project == to_project:
                raise storage.AccessError("The release is already in that project.", status=400)
            new_key = f"{to_project}-{version}"
            if await self.__data.get(sql.Release, new_key) is not None:
                raise storage.AccessError(f"Target already has release {new_key}.", status=409)
            await self.__data.execute(sqlalchemy.text("PRAGMA defer_foreign_keys=ON"))
            await self._repoint_release_rows(old_key, from_project, version, to_project)
            # The release leaves one project's subtree and joins another, so both regenerate
            await catalog_site.queue_regeneration(self.__data, self.__asf_uid, from_project)
            await catalog_site.queue_regeneration(self.__data, self.__asf_uid, to_project)
            await self._assert_fk_integrity()
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            moved_release=old_key,
            moved_to_project=to_project,
        )

    async def rename_project(self, old_key: safe.ProjectKey, new_key: safe.ProjectKey, new_name: str | None) -> None:
        old = str(old_key)
        new = str(new_key)
        if old == new:
            raise storage.AccessError("The new key matches the current key.", status=400)
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            project = await self.__data.project(key=old).get()
            if project is None:
                raise storage.AccessError(f"Project '{old}' not found.", status=404)
            await self._ensure_catalog_only(old)
            if await self.__data.project(key=new).get() is not None:
                raise storage.AccessError(f"A project '{new}' already exists; use rehome to merge.", status=409)
            await self._repoint(old, new)
            if new_name is not None:
                renamed = await self.__data.project(key=new).get()
                if renamed is None:
                    raise storage.AccessError("The renamed project could not be reloaded.", status=500)
                renamed.name = new_name
            # Same committee and the project stays put, so its subtree regenerates in place
            await catalog_site.queue_regeneration(self.__data, self.__asf_uid, new)
            await self._assert_fk_integrity()
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            renamed_from=old,
            renamed_to=new,
        )

    async def rehome_project(self, source_key: safe.ProjectKey, target_key: safe.ProjectKey) -> None:
        source = str(source_key)
        target = str(target_key)
        if source == target:
            raise storage.AccessError("Source and target are the same project.", status=400)
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            if await self.__data.project(key=source).get() is None:
                raise storage.AccessError(f"Project '{source}' not found.", status=404)
            if await self.__data.project(key=target).get() is None:
                raise storage.AccessError(f"Target project '{target}' not found.", status=404)
            await self._ensure_catalog_only(source)
            await self._ensure_catalog_only(target)
            collisions = await self._collisions(source, target)
            if collisions:
                listed = ", ".join(f"{c.kind} {c.key}" for c in collisions)
                raise storage.AccessError(f"Rehome halted on collision: {listed}", status=409)

            await self.__data.execute(sqlalchemy.text("PRAGMA defer_foreign_keys=ON"))
            source_release_keys = await self._release_keys(source)
            source_cycle_keys = await self._cycle_keys(source)
            moved_keys = await self._rehome_rows(source, target, source_release_keys)
            await self._rehome_cycles(target, moved_keys, source_cycle_keys)
            await self._drop_project(source)
            # The source project is gone and its committee index with it, so rebuild the whole site
            await catalog_site.queue_full_regeneration(self.__data, self.__asf_uid)
            await self._assert_fk_integrity()
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, rehomed_from=source, rehomed_to=target)

    async def _rehome_rows(self, source: str, target: str, source_release_keys: list[str]) -> list[str]:
        via = sql.validate_instrumented_attribute
        # Sub-projects of the source reparent onto the target
        await self.__data.execute(
            sqlmodel.update(sql.Project)
            .where(via(sql.Project.super_project_key) == source)
            .values(super_project_key=target)
        )
        # Each release moves under the target project; its cycle is resolved later
        releases = await self.__data.release(project_key=source).all()
        moved_keys = [f"{target}-{release.version}" for release in releases]
        for release in releases:
            await self.__data.execute(
                sqlmodel.update(sql.Release)
                .where(via(sql.Release.key) == release.key)
                .values(key=f"{target}-{release.version}", project_key=target)
            )
        # Release-key children rewrite their prefix; project-key children point at the target
        release_child_refs = [(model, attr) for model, attr in repoint.RELEASE_KEY_REFS if model is not sql.Release]
        await self._rewrite_prefix(release_child_refs, source_release_keys, source, target)
        for model, attr in repoint.PROJECT_KEY_REFS:
            if model in (sql.Project, sql.ProjectCycle, sql.Release):
                continue
            column = via(getattr(model, attr))
            await self.__data.execute(sqlmodel.update(model).where(column == source).values(**{attr: target}))
        return moved_keys

    async def _rehome_cycles(self, target: str, moved_keys: list[str], source_cycle_keys: list[str]) -> None:
        via = sql.validate_instrumented_attribute
        # Re-resolve cycle membership for the moved releases against the target's scheme
        self.__data.expire_all()
        target_project = await self.__data.project(key=target).get()
        if target_project is None:
            raise storage.AccessError("The rehome target could not be reloaded.", status=500)
        await cycles.reassign_release_cycles(self.__data, target_project)
        # Align each moved release's lifecycle events with its resolved cycle
        for moved_key in moved_keys:
            moved_release = await self.__data.get(sql.Release, moved_key)
            if moved_release is None:
                raise storage.AccessError("A moved release could not be reloaded.", status=500)
            await self.__data.execute(
                sqlmodel.update(sql.LifecycleEvent)
                .where(via(sql.LifecycleEvent.version_key) == moved_key)
                .values(cycle_key=moved_release.cycle_key)
            )
        # Release-less events left on a source cycle fall back to the target default
        await self.__data.execute(
            sqlmodel.update(sql.LifecycleEvent)
            .where(via(sql.LifecycleEvent.cycle_key).in_(source_cycle_keys))
            .values(cycle_key=f"{target}-default")
        )

    async def _drop_project(self, source: str) -> None:
        via = sql.validate_instrumented_attribute
        await self.__data.execute(sqlmodel.delete(sql.ProjectCycle).where(via(sql.ProjectCycle.project_key) == source))
        await self.__data.execute(sqlmodel.delete(sql.Project).where(via(sql.Project.key) == source))

    async def delete_project(self, project_key: safe.ProjectKey) -> None:
        key = str(project_key)
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            if await self.__data.project(key=key).get() is None:
                raise storage.AccessError(f"Project '{key}' not found.", status=404)
            await self._ensure_catalog_only(key)
            await self.__data.execute(sqlalchemy.text("PRAGMA defer_foreign_keys=ON"))
            await self._delete_project_rows(key)
            # The committee index can't regenerate through a project that no longer exists
            await catalog_site.queue_full_regeneration(self.__data, self.__asf_uid)
            await self._assert_fk_integrity()
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, deleted_project=key)

    async def _delete_project_rows(self, key: str) -> None:
        via = sql.validate_instrumented_attribute
        release_keys = await self._release_keys(key)
        cycle_keys = await self._cycle_keys(key)

        # Sub-projects survive with their super pointer cleared
        await self.__data.execute(
            sqlmodel.update(sql.Project).where(via(sql.Project.super_project_key) == key).values(super_project_key=None)
        )
        for model, attr in repoint.RELEASE_KEY_REFS:
            column = via(getattr(model, attr))
            await self.__data.execute(sqlmodel.delete(model).where(column.in_(release_keys)))
        for model, attr in repoint.CYCLE_KEY_REFS:
            column = via(getattr(model, attr))
            await self.__data.execute(sqlmodel.delete(model).where(column.in_(cycle_keys)))
        for model, attr in repoint.PROJECT_KEY_REFS:
            if (model, attr) == (sql.Project, "super_project_key"):
                continue
            column = via(getattr(model, attr))
            await self.__data.execute(sqlmodel.delete(model).where(column == key))
        await self.__data.execute(sqlmodel.delete(sql.Project).where(via(sql.Project.key) == key))

    async def _delete_release_rows(self, release_keys: list[str]) -> None:
        if not release_keys:
            return
        via = sql.validate_instrumented_attribute
        for model, attr in repoint.RELEASE_KEY_REFS:
            if model is sql.Release:
                continue
            column = via(getattr(model, attr))
            await self.__data.execute(sqlmodel.delete(model).where(column.in_(release_keys)))
        await self.__data.execute(sqlmodel.delete(sql.Release).where(via(sql.Release.key).in_(release_keys)))

    async def _delete_artifact_rows(self, pks: list[tuple[str, str, str]]) -> None:
        if not pks:
            return
        via = sql.validate_instrumented_attribute
        identity = sqlalchemy.tuple_(
            via(sql.Artifact.project_key), via(sql.Artifact.version), via(sql.Artifact.artifact_path)
        )
        # Chunked to stay inside SQLite's bound-variable limit
        for start in range(0, len(pks), _DELETE_CHUNK):
            chunk = pks[start : start + _DELETE_CHUNK]
            await self.__data.execute(sqlmodel.delete(sql.Artifact).where(identity.in_(chunk)))

    async def _resolve_super_projects(self, project_keys: list[str]) -> None:
        # A sub-project hangs under the longest project key in its committee that prefixes its own,
        # which is the rule the project creation path applies. The CSV does not carry the column
        if not project_keys:
            return
        self.__data.expire_all()
        candidates_by_committee: dict[str, list[str]] = {}
        for project_key in project_keys:
            project = await self.__data.project(key=project_key).get()
            if project is None:
                raise storage.AccessError(f"Project '{project_key}' could not be reloaded.", status=500)
            committee_key = project.committee_key
            if committee_key is None:
                continue
            if committee_key not in candidates_by_committee:
                siblings = await self.__data.project(committee_key=committee_key).all()
                candidates_by_committee[committee_key] = sorted(
                    (str(sibling.key) for sibling in siblings), key=len, reverse=True
                )
            project.super_project_key = next(
                (
                    key
                    for key in candidates_by_committee[committee_key]
                    if (key != project_key) and project_key.startswith(f"{key}-")
                ),
                None,
            )

    async def _reassign_cycles_for_added_releases(self, diff: catalogue_diff.CatalogueDiff) -> None:
        # An added release lands in the project's default cycle, so resolve it against the version
        # scheme, which also creates any cycle the project lacks
        project_keys = sorted({str(add.project_key) for add in diff.adds if isinstance(add, catalogue_rows.ReleaseRow)})
        if not project_keys:
            return
        await self.__data.flush()
        self.__data.expire_all()
        for project_key in project_keys:
            project = await self.__data.project(key=project_key).get()
            if project is None:
                raise storage.AccessError(f"Project '{project_key}' could not be reloaded.", status=500)
            await cycles.reassign_release_cycles(self.__data, project)

    async def _apply_deletions(self, diff: catalogue_diff.CatalogueDiff) -> list[str]:
        # Children first: a release cascades its artifacts, a project needs its releases gone. Each
        # level goes in one statement per table, the way _delete_project_rows already does it
        await self._delete_artifact_rows(diff.artifact_deletions)
        await self._delete_release_rows(diff.release_deletions)
        orphaned = await self._surviving_sub_projects(diff.project_deletions)
        for project_key in diff.project_deletions:
            await self._delete_project_rows(project_key)
        return orphaned

    async def _surviving_sub_projects(self, project_keys: list[str]) -> list[str]:
        # Deleting a project clears the super pointer of every project beneath it, including the
        # ones that are not being deleted, so they have to be given a new one afterwards
        if not project_keys:
            return []
        via = sql.validate_instrumented_attribute
        rows = (
            await self.__data.execute(
                sqlmodel.select(via(sql.Project.key)).where(via(sql.Project.super_project_key).in_(project_keys))
            )
        ).all()
        return [row[0] for row in rows if row[0] not in set(project_keys)]

    async def import_catalogue_csvs(
        self,
        rows_by_table: dict[str, list[dict[str, str]]],
        committee_key: str,
        mode: catalogue_diff.Mode,
        previewed: str | None = None,
    ) -> catalogue_diff.CatalogueDiff:
        # Recompute the diff against a snapshot taken inside this transaction, under the write lock
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            snapshot = await catalogue_import.build_snapshot(self.__data, committee_key)
            diff = catalogue_diff.classify(rows_by_table, snapshot, committee_key, mode)
            if (previewed is not None) and (catalogue_diff.fingerprint(diff) != previewed):
                # The catalogue moved since the preview, so this would write something the admin
                # has not seen. The watcher catalogues releases while the page is open
                raise catalogue_import.StaleError(committee_key)
            await self._apply_diff(diff)
            # A committee-wide import moves projects and releases around, so a full rebuild - but
            # only when it changed something, since a conflict-only or no-op import leaves the site as-is
            if any(diff.counts[change] for change in ("add", "release_repoint", "artifact_repoint", "delete")):
                await catalog_site.queue_full_regeneration(self.__data, self.__asf_uid)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self._audit_import(diff, committee_key, mode)
        return diff

    async def _apply_diff(self, diff: catalogue_diff.CatalogueDiff) -> None:
        await self.__data.execute(sqlalchemy.text("PRAGMA defer_foreign_keys=ON"))
        # Deletions run first, so the committee is cleared before the files are re-added and a row
        # can reuse a key it has just released. A deleted project leaves its surviving sub-projects
        # with no super pointer, and they hang under whatever the files put back in its place
        orphaned = await self._apply_deletions(diff)
        # Adds in FK order: projects, then releases, then artifacts
        for add in diff.adds:
            if isinstance(add, catalogue_rows.ProjectRow):
                self.__data.add(add.to_sql())
        await self.__data.flush()
        added = [str(add.key) for add in diff.adds if isinstance(add, catalogue_rows.ProjectRow)]
        await self._resolve_super_projects(added + orphaned)
        for add in diff.adds:
            if isinstance(add, catalogue_rows.ReleaseRow):
                self.__data.add(add.to_sql())
        await self.__data.flush()
        for add in diff.adds:
            if isinstance(add, catalogue_rows.ArtifactRow):
                self.__data.add(add.to_sql())
        await self._reassign_cycles_for_added_releases(diff)
        # Artifacts repoint first: a repointed release takes its artifacts by project and version,
        # so an artifact the file sends elsewhere has to leave before the release takes it along
        for artifact_repoint in diff.artifact_repoints:
            await self._repoint_one_artifact(artifact_repoint)
        for release_repoint in diff.release_repoints:
            await self._repoint_one_release(release_repoint)
        await self._assert_fk_integrity()

    def _audit_import(self, diff: catalogue_diff.CatalogueDiff, committee_key: str, mode: catalogue_diff.Mode) -> None:
        counts = diff.counts
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=committee_key,
            mode=str(mode),
            added=counts["add"],
            releases_repointed=counts["release_repoint"],
            artifacts_repointed=counts["artifact_repoint"],
            deleted=counts["delete"],
            conflicts=counts["conflict"],
        )

    async def _repoint_one_release(self, release_repoint: catalogue_diff.ReleaseRepoint) -> None:
        await self._repoint_release_rows(
            release_repoint.key,
            release_repoint.from_project,
            str(release_repoint.row.version),
            release_repoint.to_project,
        )

    async def _repoint_release_rows(self, old_key: str, from_project: str, version: str, to_project: str) -> None:
        via = sql.validate_instrumented_attribute
        new_key = f"{to_project}-{version}"
        await self.__data.execute(
            sqlmodel.update(sql.Artifact)
            .where(via(sql.Artifact.project_key) == from_project)
            .where(via(sql.Artifact.version) == version)
            .values(release_key=new_key, project_key=to_project)
        )
        await self.__data.execute(
            sqlmodel.update(sql.LifecycleEvent)
            .where(via(sql.LifecycleEvent.version_key) == old_key)
            .values(version_key=new_key, project_key=to_project)
        )
        # Other release-key children re-point to the new key
        for model, attr in repoint.RELEASE_KEY_REFS:
            if model in (sql.Release, sql.Artifact, sql.LifecycleEvent):
                continue
            column = via(getattr(model, attr))
            await self.__data.execute(sqlmodel.update(model).where(column == old_key).values(**{attr: new_key}))
        await self.__data.execute(
            sqlmodel.update(sql.Release)
            .where(via(sql.Release.key) == old_key)
            .values(key=new_key, project_key=to_project)
        )
        # Resolve the repointed release's cycle against the target's scheme, then align its events
        self.__data.expire_all()
        target_project = await self.__data.project(key=to_project).get()
        if target_project is None:
            raise storage.AccessError("The repoint target could not be reloaded.", status=500)
        await cycles.reassign_release_cycles(self.__data, target_project)
        repointed_release = await self.__data.get(sql.Release, new_key)
        if repointed_release is None:
            raise storage.AccessError("The repointed release could not be reloaded.", status=500)
        await self.__data.execute(
            sqlmodel.update(sql.LifecycleEvent)
            .where(via(sql.LifecycleEvent.version_key) == new_key)
            .values(cycle_key=repointed_release.cycle_key)
        )

    async def _repoint_one_artifact(self, artifact_repoint: catalogue_diff.ArtifactRepoint) -> None:
        via = sql.validate_instrumented_attribute
        old_project, old_version, old_path = artifact_repoint.from_pk
        row = artifact_repoint.to_row
        await self.__data.execute(
            sqlmodel.update(sql.Artifact)
            .where(via(sql.Artifact.project_key) == old_project)
            .where(via(sql.Artifact.version) == old_version)
            .where(via(sql.Artifact.artifact_path) == old_path)
            .values(
                project_key=str(row.project_key),
                version=str(row.version),
                release_key=row.release_key,
            )
        )

    async def _collisions(self, source_key: str, target_key: str) -> list[repoint.Collision]:
        via = sql.validate_instrumented_attribute
        collisions: list[repoint.Collision] = []
        source_releases = await self.__data.release(project_key=source_key).all()
        for release in source_releases:
            candidate = f"{target_key}-{release.version}"
            if await self.__data.get(sql.Release, candidate) is not None:
                collisions.append(repoint.Collision(kind="release", key=candidate))
        source_artifacts = (
            (
                await self.__data.execute(
                    sqlmodel.select(sql.Artifact).where(via(sql.Artifact.project_key) == source_key)
                )
            )
            .scalars()
            .all()
        )
        for artifact in source_artifacts:
            existing = await self.__data.get(sql.Artifact, (target_key, artifact.version, artifact.artifact_path))
            if existing is not None:
                collisions.append(repoint.Collision(kind="artifact", key=artifact.artifact_path))
        return collisions

    async def _repoint(self, old_key: str, new_key: str) -> None:
        # Foreign key checks deferred to commit while parent and child keys are rewritten
        await self.__data.execute(sqlalchemy.text("PRAGMA defer_foreign_keys=ON"))
        via = sql.validate_instrumented_attribute
        release_keys = await self._release_keys(old_key)
        cycle_keys = await self._cycle_keys(old_key)

        # The project key itself
        await self.__data.execute(
            sqlmodel.update(sql.Project).where(via(sql.Project.key) == old_key).values(key=new_key)
        )
        # Columns that hold the project key verbatim
        for model, attr in repoint.PROJECT_KEY_REFS:
            column = via(getattr(model, attr))
            await self.__data.execute(sqlmodel.update(model).where(column == old_key).values(**{attr: new_key}))
        # Columns that hold a release key, scoped to this project's own keys (never a sibling
        # sharing the prefix)
        await self._rewrite_prefix(repoint.RELEASE_KEY_REFS, release_keys, old_key, new_key)
        # Columns that hold a cycle key
        await self._rewrite_prefix(repoint.CYCLE_KEY_REFS, cycle_keys, old_key, new_key)

    async def _rewrite_prefix(
        self, refs: list[tuple[type[sqlmodel.SQLModel], str]], keys: list[str], old_key: str, new_key: str
    ) -> None:
        if not keys:
            return
        via = sql.validate_instrumented_attribute
        # The suffix starts one past "{old_key}-", and SQLite substr is 1-based
        suffix_start = len(old_key) + 2
        for model, attr in refs:
            column = via(getattr(model, attr))
            rewritten = sqlalchemy.literal(f"{new_key}-").op("||")(sqlalchemy.func.substr(column, suffix_start))
            await self.__data.execute(sqlmodel.update(model).where(column.in_(keys)).values(**{attr: rewritten}))

    async def _release_keys(self, project_key: str) -> list[str]:
        via = sql.validate_instrumented_attribute
        result = await self.__data.execute(
            sqlmodel.select(via(sql.Release.key)).where(via(sql.Release.project_key) == project_key)
        )
        return list(result.scalars().all())

    async def _cycle_keys(self, project_key: str) -> list[str]:
        via = sql.validate_instrumented_attribute
        result = await self.__data.execute(
            sqlmodel.select(via(sql.ProjectCycle.cycle_key)).where(via(sql.ProjectCycle.project_key) == project_key)
        )
        return list(result.scalars().all())

    async def _ensure_catalog_only(self, project_key: str) -> None:
        # A live project also carries revisions and release file-state, whose keys this page
        # does not re-point
        via = sql.validate_instrumented_attribute
        release_keys = await self._release_keys(project_key)
        if not release_keys:
            return
        result = await self.__data.execute(
            sqlmodel.select(sqlalchemy.func.count())
            .select_from(sql.Revision)
            .where(via(sql.Revision.release_key).in_(release_keys))
        )
        if int(result.scalar() or 0) > 0:
            raise storage.AccessError(
                f"Project '{project_key}' has live release-workflow data (revisions); "
                "this page corrects catalogued projects only.",
                status=409,
            )

    async def _ensure_release_catalog_only(self, release_key: str) -> None:
        # A live release also carries revisions and file-state, whose keys this page does not
        # re-point, so we only move a release that has none
        via = sql.validate_instrumented_attribute
        result = await self.__data.execute(
            sqlmodel.select(sqlalchemy.func.count())
            .select_from(sql.Revision)
            .where(via(sql.Revision.release_key) == release_key)
        )
        if int(result.scalar() or 0) > 0:
            raise storage.AccessError(
                f"Release '{release_key}' has live release-workflow data (revisions); "
                "this page moves catalogued releases only.",
                status=409,
            )

    async def _assert_fk_integrity(self) -> None:
        # Flush pending ORM work, then check for dangling references before commit
        await self.__data.flush()
        result = await self.__data.execute(sqlalchemy.text("PRAGMA foreign_key_check"))
        violations = result.fetchall()
        if violations:
            tables = ", ".join(sorted({str(row[0]) for row in violations}))
            raise storage.AccessError(
                f"Correction aborted: it would leave dangling references in {tables}.", status=500
            )
