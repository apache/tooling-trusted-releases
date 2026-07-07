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

import sqlalchemy
import sqlmodel

import atr.cycles as cycles
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.repoint as repoint
import atr.storage as storage


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
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            moved_to=str(dest_committee_key),
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
            await self._assert_fk_integrity()
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, rehomed_from=source, rehomed_to=target)

    async def _rehome_rows(self, source: str, target: str, source_release_keys: list[str]) -> list[str]:
        via = sql.validate_instrumented_attribute
        # A sub-project of the source hangs under the target once its parent is gone
        await self.__data.execute(
            sqlmodel.update(sql.Project)
            .where(via(sql.Project.super_project_key) == source)
            .values(super_project_key=target)
        )
        # Each release moves under the target project; its cycle is resolved later, against
        # the target's own version scheme rather than assumed to be the default
        releases = await self.__data.release(project_key=source).all()
        moved_keys = [f"{target}-{release.version}" for release in releases]
        for release in releases:
            await self.__data.execute(
                sqlmodel.update(sql.Release)
                .where(via(sql.Release.key) == release.key)
                .values(key=f"{target}-{release.version}", project_key=target)
            )
        # Release-key children rewrite their prefix (Release itself moved above); project-key
        # children point at the target. Both scoped to the source's own rows
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
        # Re-resolve cycle membership for the moved releases against the target's scheme,
        # creating any cycle the target lacks. The raw updates bypassed the ORM, so expire
        # first to re-read the moved rows fresh
        self.__data.expire_all()
        target_project = await self.__data.project(key=target).get()
        if target_project is None:
            raise storage.AccessError("The rehome target could not be reloaded.", status=500)
        await cycles.reassign_release_cycles(self.__data, target_project)
        # A lifecycle event belongs in the same cycle as its release. version_key already
        # points at the moved release key, so align each event with the cycle just resolved
        for moved_key in moved_keys:
            moved_release = await self.__data.get(sql.Release, moved_key)
            if moved_release is None:
                raise storage.AccessError("A moved release could not be reloaded.", status=500)
            await self.__data.execute(
                sqlmodel.update(sql.LifecycleEvent)
                .where(via(sql.LifecycleEvent.version_key) == moved_key)
                .values(cycle_key=moved_release.cycle_key)
            )
        # Project-level events with no release can't take a release's cycle, so the ones left
        # on a source cycle fall back to the target default, which always exists
        await self.__data.execute(
            sqlmodel.update(sql.LifecycleEvent)
            .where(via(sql.LifecycleEvent.cycle_key).in_(source_cycle_keys))
            .values(cycle_key=f"{target}-default")
        )

    async def _drop_project(self, source: str) -> None:
        via = sql.validate_instrumented_attribute
        # The source's own cycles and project row go; a rehome target keeps its cycles
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
            via = sql.validate_instrumented_attribute
            release_keys = await self._release_keys(key)
            cycle_keys = await self._cycle_keys(key)

            # A sub-project survives with its super pointer cleared; we delete this project,
            # not the ones that hang beneath it
            await self.__data.execute(
                sqlmodel.update(sql.Project)
                .where(via(sql.Project.super_project_key) == key)
                .values(super_project_key=None)
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
            await self._assert_fk_integrity()
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(asf_uid=self.__asf_uid, deleted_project=key)

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
        # Defer foreign key checks to commit, so we can rewrite parent primary keys
        # and their children in any order within this one transaction
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
        # Columns that hold a release key; scoped to this project's own release keys, so a
        # sibling project whose key shares this prefix is never swept in
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
        # A catalogued project carries only project, cycle, release, artifact and lifecycle
        # rows. A live ATR project also has revisions and release file-state, whose keys this
        # page does not yet re-point, so refuse those rather than corrupt them
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

    async def _assert_fk_integrity(self) -> None:
        # Flush pending ORM work so the check sees the full picture, then refuse to commit if
        # a correction has left any dangling reference behind
        await self.__data.flush()
        result = await self.__data.execute(sqlalchemy.text("PRAGMA foreign_key_check"))
        violations = result.fetchall()
        if violations:
            tables = ", ".join(sorted({str(row[0]) for row in violations}))
            raise storage.AccessError(
                f"Correction aborted: it would leave dangling references in {tables}.", status=500
            )
