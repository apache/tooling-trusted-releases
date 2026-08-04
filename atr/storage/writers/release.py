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

import asyncio
import base64
import contextlib
import dataclasses
import datetime
import re
from typing import TYPE_CHECKING, Any, Final

import aiofiles.os
import sqlalchemy
import sqlalchemy.dialects.sqlite as sqlite
import sqlalchemy.engine as engine
import sqlmodel

import atr.analysis as analysis
import atr.catalog_site as catalog_site
import atr.config as config
import atr.constants as constants
import atr.construct as construct
import atr.cycles as cycles
import atr.db as db
import atr.db.interaction as interaction
import atr.hashes as hashes
import atr.log as log
import atr.models.api as api
import atr.models.args as args
import atr.models.attestable as attestable
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.paths as paths
import atr.shared.start as start
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.svn as svn
import atr.tasks.checks as checks
import atr.tasks.checks.signature as signature
import atr.util as util

if TYPE_CHECKING:
    import pathlib
    from collections.abc import AsyncIterator, Sequence

    import werkzeug.datastructures as datastructures

SPECIAL_SUFFIXES: Final[frozenset[str]] = frozenset({".asc", ".sha256", ".sha512"})

_ACTIVITY_BUMP_PHASES: Final[frozenset[sql.ReleasePhase]] = frozenset(
    {
        sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        sql.ReleasePhase.RELEASE_CANDIDATE,
        sql.ReleasePhase.RELEASE_PREVIEW,
    }
)
_SIGNATURE_CHECKER_KEY: Final[str] = checks.function_key(signature.check)

# Fallback cycle name when a catalogued version doesn't match the project's
# cycle_match, mirroring what cycle_name_for_version returns for simple projects
_CATALOGUE_DEFAULT_CYCLE: Final[str] = "default"


@dataclasses.dataclass(frozen=True)
class ArtifactInput:
    # One artifact row to catalogue, with its companions already paired by the
    # caller. The decomposition and pairing stay in the dist watcher; this is
    # just the typed handoff so catalogue_release never sees an untyped dict
    artifact_path: str
    classification: str
    # The file's directory under the dist root, as the watcher observed it
    download_path_suffix: str
    signature_path: str | None = None
    checksum_path: str | None = None
    sbom_path: str | None = None


async def _archive_release(
    data: db.Session,
    write_as: storage.WriteAs,
    asf_uid: str,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    release: sql.Release,
) -> str | None:
    # The archive core, shared by a committee member archiving by hand and the
    # system archiving a dist removal. Only an announced release can be
    # archived.
    if release.phase != sql.ReleasePhase.RELEASE:
        return f"Release {project_key!s} {version_key!s} is not in the release phase"

    archive_date = datetime.datetime.now(datetime.UTC)
    via = sql.validate_instrumented_attribute
    update_stmt = (
        sqlmodel.update(sql.Release)
        .where(via(sql.Release.key) == release.key)
        .where(via(sql.Release.is_archived).is_(False))
        .values(archived=archive_date, is_archived=True)
    )
    update_result = await data.execute_query(update_stmt)
    if getattr(update_result, "rowcount", 0) != 1:
        return f"Release {project_key!s} {version_key!s} is already archived"

    # TODO: SVN move to archive.apache.org goes here once SVN is wired up.

    data.add(
        sql.LifecycleEvent(
            project_key=release.project_key,
            cycle_key=release.cycle_key,
            version_key=release.key,
            event=sql.LifecycleEventType.ARCHIVE,
            effective=archive_date,
            published=archive_date,
        )
    )
    # Archiving flips a release from current to archived, so the catalog site
    # pages for its project need rewriting.
    await catalog_site.queue_regeneration(data, asf_uid, release.project_key)
    await data.commit()
    write_as.append_to_audit_log(
        asf_uid=asf_uid,
        project_key=str(project_key),
        version=str(version_key),
        archived=archive_date.isoformat(),
    )
    return None


def _assert_can_start(asf_uid: str, project: sql.Project) -> None:
    committee = project.committee
    if config.is_test_mode() and (committee is not None) and (committee.key == "test"):
        return
    display_name = project.display_name
    if committee is None:
        raise storage.AccessError(
            f"You must be a member or committer of the {display_name} committee to start a release draft.",
            status=403,
        )
    if (asf_uid in committee.committee_members) or (asf_uid in committee.committers):
        return
    raise storage.AccessError(
        f"You must be a member or committer of the {display_name} committee to start a release draft.",
        status=403,
    )


async def _assert_no_existing_release(
    data: db.Session, project: sql.Project, project_key: safe.ProjectKey, version: safe.VersionKey
) -> None:
    # TODO: Consider using Release.revision instead of ./latest
    release = await data.release(project_key=project.key, version=str(version)).get()
    if release is None:
        return
    match release.phase:
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            phase_desc = "A draft release (being composed)"
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            phase_desc = "A release candidate (being voted on)"
        case sql.ReleasePhase.RELEASE_PREVIEW:
            phase_desc = "A release preview (being finished)"
        case sql.ReleasePhase.RELEASE:
            phase_desc = "A finished release"
    raise storage.AccessError(f"{phase_desc} for {project_key!s} {version} already exists.", status=409)


async def _claim_release_archive_approval(
    data: db.Session,
    approval_request_id: int,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    committee_key: str,
) -> None:
    approval = await data.approval_request(id=approval_request_id).get()
    if (approval is None) or (approval.status != sql.ApprovalStatus.APPROVED):
        raise storage.AccessError("This approval request is not ready to complete.", status=409)
    if (
        (approval.action != sql.ApprovalAction.ARCHIVE_RELEASE)
        or (approval.project_key != str(project_key))
        or (approval.release_version != str(version_key))
    ):
        raise storage.AccessError("This approval request does not match the requested action.", status=409)
    if approval.committee_key != committee_key:
        raise storage.AccessError("This approval request was filed for a different committee.", status=409)
    approval.status = sql.ApprovalStatus.COMPLETED


async def _ensure_project_cycle(data: db.Session, project: sql.Project, version: safe.VersionKey) -> str:
    try:
        cycle_name = cycles.cycle_name_for_version(project, str(version))
    except ValueError as exc:
        raise storage.AccessError(str(exc)) from exc
    cycle_key = f"{project.key}-{cycle_name}"
    if not await data.project_cycle(cycle_key=cycle_key).get():
        data.add(
            sql.ProjectCycle(
                cycle_key=cycle_key,
                cycle=cycle_name,
                project_key=project.key,
                lts=False,
            )
        )
    return cycle_key


def _normalise_signature_field(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if (not stripped) or (stripped.lower() in {"unknown", "not available"}):
            return None
        return stripped
    if value is None:
        return None
    return str(value)


async def _signature_provenance_metadata_for(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    parent_revision: sql.Revision | None,
    signature_rel_path: pathlib.Path,
) -> dict[str, str] | None:
    if parent_revision is None:
        return None
    release_key = sql.release_key(str(project_key), str(version_key))
    parent_revision_number = parent_revision.safe_number
    parent_number = str(parent_revision_number)
    signature_rel_str = str(signature_rel_path)
    check_results = await interaction.check_results_for_revision(
        project_key,
        version_key,
        parent_revision_number,
        checker=_SIGNATURE_CHECKER_KEY,
        include_legacy_revision_results=True,
        rel_path=signature_rel_str,
    )
    if not check_results:
        log.info(
            "SHA512 generation proceeding without signature provenance"
            f" release={release_key} revision={parent_number} path={signature_rel_str}"
        )
        return None
    latest = check_results[0]
    latest_message = latest.message.strip() if isinstance(getattr(latest, "message", None), str) else None
    log.info(
        "SHA512 generation found signature check result"
        f" release={release_key} revision={parent_number} path={signature_rel_str}"
        f" result_revision={latest.revision_number}"
        f" status={latest.status.value} message={latest_message!r}"
    )
    if latest.status != sql.CheckResultStatus.NOTE:
        return None
    payload = latest.data if isinstance(latest.data, dict) else {}
    metadata = {"signature_path": signature_rel_str}
    for key in ("fingerprint", "key_id", "timestamp", "username"):
        if (value := _normalise_signature_field(payload.get(key))) is not None:
            metadata[key] = value
    return metadata


async def _start_release(
    data: db.Session,
    write: storage.Write,
    write_as: storage.WriteAsCommitteeParticipant,
    asf_uid: str,
    project_key: safe.ProjectKey,
    version: safe.VersionKey,
    auto_archive: bool,
    expedited: bool,
) -> tuple[sql.Release, sql.Project]:
    """Creates the initial release draft record and revision directory."""
    await data.begin_immediate()
    project = await data.project(key=str(project_key), status=sql.ProjectStatus.ACTIVE, _committee=True).get()
    if not project:
        raise storage.AccessError(f"Project {project_key} not found", status=404)

    _assert_can_start(asf_uid, project)
    if missing := start.missing_release_metadata(project):
        raise storage.AccessError(
            f"Project metadata incomplete, cannot start a release: {', '.join(missing)}", status=400
        )
    if expedited and (project.committee is not None) and project.committee.is_podling:
        raise storage.AccessError("Expedited releases are not available for podling projects.", status=400)
    await _assert_no_existing_release(data, project, project_key, version)
    _validate_version(project, version)
    cycle_key = await _ensure_project_cycle(data, project, version)

    now = datetime.datetime.now(datetime.UTC)
    release = sql.Release(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        project_key=project.key,
        project=project,
        version=str(version),
        cycle_key=cycle_key,
        archive_prior_release=auto_archive,
        expedited=expedited,
        created=now,
        activity_at=now,
    )
    write.ensure_release_writable(release)
    data.add(release)
    await data.commit()
    await data.refresh(release)

    description = "Creation of empty release candidate draft through web interface"
    await write_as.revision.create_revision_with_quarantine(
        project_key,
        version,
        asf_uid,
        allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        description=description,
    )
    write_as.append_to_audit_log(
        asf_uid=asf_uid,
        project_key=project.key,
        version=str(version),
        created=release.created.isoformat(),
    )
    return release, project


def _validate_version(project: sql.Project, version: safe.VersionKey) -> None:
    # TODO: We should check that it's bigger than the current version. We
    # have the packaging library as a dependency, but it's Python specific.
    if version_key_error := util.version_key_error(str(version)):
        raise storage.AccessError(f'Invalid version name "{version!s}": {version_key_error}', status=400)
    if project.version_pattern is None:
        return
    try:
        pattern = validation.compile_project_pattern(project.version_pattern)
    except ValueError as exc:
        raise storage.AccessError(f"The project's version pattern is invalid: {exc}", status=400) from exc
    if pattern.fullmatch(str(version)) is None:
        raise storage.AccessError(
            f'Version "{version!s}" does not match the project\'s version pattern',
            status=400,
        )


class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ) -> None:
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write.authorisation.asf_uid


class FoundationCommitter(GeneralPublic):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationCommitter, data: db.Session) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_key: str,
    ) -> None:
        super().__init__(write, write_as, data)
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def bump_activity(self, project_key: safe.ProjectKey, version_key: safe.VersionKey) -> sql.Release:
        release = await self.__data.release(
            project_key=str(project_key), version=str(version_key), _committee=True
        ).demand(storage.AccessError(f"Release '{project_key!s} {version_key!s}' not found.", status=404))
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        ineligible_phase = release.phase not in _ACTIVITY_BUMP_PHASES
        if ineligible_phase:
            raise storage.AccessError(
                f"Release phase {release.phase.value} is not eligible for activity reset", status=409
            )
        now = datetime.datetime.now(datetime.UTC)
        previous_activity_at = release.activity_at
        via = sql.validate_instrumented_attribute
        release_activity_at = via(sql.Release.activity_at)
        activity_at_value = sqlalchemy.case(
            (release_activity_at > now, release_activity_at),
            else_=now,
        )
        result = await self.__data.execute(
            sqlmodel.update(sql.Release)
            .where(
                via(sql.Release.key) == release.key,
                via(sql.Release.phase).in_(_ACTIVITY_BUMP_PHASES),
            )
            .values(activity_at=activity_at_value, inactivity_notice_key=None)
        )
        if getattr(result, "rowcount", 0) != 1:
            await self.__data.rollback()
            raise storage.AccessError("The release state has changed, please refresh and try again", status=409)
        await self.__data.refresh(release)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version=str(version_key),
            previous_activity_at=previous_activity_at.isoformat(),
            activity_at=release.activity_at.isoformat(),
        )
        return release

    async def delete(
        self,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
        phase: db.Opt[sql.ReleasePhase] = db.NOT_SET,
    ) -> str | None:
        """Handle the deletion of database records and filesystem data for a release."""
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            release = await self.__data.release(
                project_key=str(project_key), version=str(version), phase=phase, _committee=True
            ).demand(storage.AccessError(f"Release '{project_key!s} {version!s}' not found.", status=404))
            storage.ensure_project_active(release.project)
            self.__write.ensure_release_writable(release)
        except Exception:
            await self.__data.rollback()
            raise
        # Once a release has been announced it can only be archived, never deleted
        if release.phase == sql.ReleasePhase.RELEASE:
            await self.__data.rollback()
            return f"Release '{project_key!s} {version!s}' has been announced; it can only be archived, not deleted."
        return await self.__delete_body(release, project_key, version)

    async def __delete_body(
        self,
        release: sql.Release,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
    ) -> str | None:
        release_dirs = [
            paths.release_directory_base(release),
            paths.get_attestable_dir() / str(project_key) / str(version),
            paths.get_archives_dir() / str(project_key) / str(version),
            paths.get_quarantined_dir() / str(project_key) / str(version),
        ]

        # Delete from the database using bulk SQL DELETE for efficiency
        log.info(f"Deleting database records for release: {project_key!s} {version!s}")

        # Bulk delete tasks
        # These is no cascade, so we must delete explicitly
        via = sql.validate_instrumented_attribute
        task_delete_stmt = sqlmodel.delete(sql.Task).where(
            via(sql.Task.project_key) == release.project.key,
            via(sql.Task.version_key) == release.version,
        )
        task_result = await self.__data.execute(task_delete_stmt)
        task_count = task_result.rowcount if isinstance(task_result, engine.CursorResult) else 0
        log.debug(f"Deleted {util.plural(task_count, 'task')} for {project_key!s} {version!s}")

        release_key = release.key

        # These deletes would also be performed by database cascade
        # We do them here before the commit instead to be explicit
        rfs_delete_stmt = sqlmodel.delete(sql.ReleaseFileState).where(
            via(sql.ReleaseFileState.release_key) == release_key,
        )
        rfs_result = await self.__data.execute(rfs_delete_stmt)
        rfs_count = rfs_result.rowcount if isinstance(rfs_result, engine.CursorResult) else 0
        log.debug(f"Deleted {util.plural(rfs_count, 'file state row')} for {project_key!s} {version!s}")

        await self.__data.delete(release)
        log.info(f"Deleted release record: {project_key!s} {version!s}")

        # In test mode, delete the counter for test committee releases
        # This allows revision numbers to be reused in testing
        committee = release.project.committee
        is_test_release = config.is_test_mode() and (committee is not None) and (committee.key == "test")
        if is_test_release:
            counter_delete_stmt = sqlmodel.delete(sql.RevisionCounter).where(
                via(sql.RevisionCounter.release_key) == release_key
            )
            await self.__data.execute(counter_delete_stmt)
            vote_counter_delete_stmt = sqlmodel.delete(sql.VoteCounter).where(
                via(sql.VoteCounter.release_key) == release_key
            )
            await self.__data.execute(vote_counter_delete_stmt)
            log.info(f"Deleted revision and vote counters for test release: {release_key}")

        # Filesystem deletions are more likely to have permission errors than database deletions
        # Therefore we do filesystem deletions first
        error = await self.__delete_release_data_filesystem(release_dirs, project_key, version)

        await self.__data.commit()

        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version=str(version),
            error=error,
        )
        return error

    async def __delete_release_data_filesystem(
        self, release_dirs: Sequence[safe.StatePath], project_key: safe.ProjectKey, version: safe.VersionKey
    ) -> str | None:
        delete_errors: list[str] = []
        for release_dir in release_dirs:
            if await aiofiles.os.path.isdir(release_dir):
                try:
                    log.info(f"Deleting filesystem directory: {release_dir}")
                    await util.delete_immutable_directory(
                        release_dir, reason=f"user {self.__asf_uid} is deleting release {project_key!s} {version!s}"
                    )
                    log.info(f"Successfully deleted directory: {release_dir}")
                except Exception as e:
                    log.exception(f"Error deleting filesystem directory {release_dir}:")
                    delete_errors.append(f"{release_dir}: {e!s}")
            else:
                log.warning(f"Filesystem directory not found, skipping deletion: {release_dir}")
        if delete_errors:
            return (
                f"Database records for '{project_key!s} {version!s}' deleted,"
                f" but failed to delete filesystem directories: {', '.join(delete_errors)}"
            )
        return None

    async def delete_file(
        self, project_key: safe.ProjectKey, version: safe.VersionKey, rel_path_to_delete: pathlib.Path
    ) -> int:
        metadata_files_deleted = 0
        description = "File deletion through web interface"

        async def modify(path: safe.StatePath, _old_rev: sql.Revision | None) -> None:
            nonlocal metadata_files_deleted
            # Uses new_revision_number for logging only
            # Path to delete within the new revision directory
            path_in_new_revision = path / rel_path_to_delete

            # Make sure the requested path is relative to the actual path
            resolved = await asyncio.to_thread(path_in_new_revision.path.resolve)
            resolved.relative_to(await asyncio.to_thread(path.path.resolve))

            # Check that the file exists in the new revision
            if not await aiofiles.os.path.exists(path_in_new_revision):
                # This indicates a potential severe issue with hard linking or logic
                log.error(f"SEVERE ERROR! File {rel_path_to_delete} not found in new revision before deletion")
                raise storage.AccessError("File to delete was not found in the new revision", status=500)

            # Check whether the file is an artifact
            if analysis.is_artifact(path_in_new_revision.path):
                # If so, delete all associated metadata files in the new revision
                async for p in util.paths_recursive(path_in_new_revision.parent):
                    # Construct full path within the new revision
                    metadata_path_obj = path / p
                    if p.as_path().name.startswith(rel_path_to_delete.name + "."):
                        await aiofiles.os.remove(metadata_path_obj)
                        metadata_files_deleted += 1

            # Delete the file
            await aiofiles.os.remove(path_in_new_revision)

        await self.__write_as.revision.create_revision_with_quarantine(
            project_key,
            version,
            self.__asf_uid,
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            description=description,
            modify=modify,
        )
        return metadata_files_deleted

    async def generate_hash_file(
        self, project_key: safe.ProjectKey, version_key: safe.VersionKey, rel_path: pathlib.Path
    ) -> None:
        description = "Hash generation through web interface"

        async def modify(
            path: safe.StatePath, old_rev: sql.Revision | None
        ) -> dict[safe.RelPath, attestable.ProvenanceV2] | None:
            # Uses new_revision_number for logging only
            path_in_new_revision = path / rel_path

            # Make sure the requested path is relative to the actual path
            resolved = await asyncio.to_thread(path_in_new_revision.path.resolve)
            resolved.relative_to(await asyncio.to_thread(path.path.resolve))

            hash_path_rel_name = rel_path.name + ".sha512"
            hash_path_in_new_revision = path / rel_path.with_name(hash_path_rel_name)
            signature_rel_path = rel_path.with_name(rel_path.name + ".asc")
            signature_path_in_new_revision = path / signature_rel_path

            # Check that the source file exists in the new revision
            if not await aiofiles.os.path.exists(path_in_new_revision):
                log.error(f"Source file {rel_path} not found in new revision for hash generation.")
                raise storage.AccessError("Source file not found in the new revision.", status=500)

            # Check that the hash file does not already exist in the new revision
            if await aiofiles.os.path.exists(hash_path_in_new_revision):
                raise storage.AccessError("SHA512 file already exists", status=409)

            signature_metadata = None
            if await aiofiles.os.path.exists(signature_path_in_new_revision):
                signature_metadata = await _signature_provenance_metadata_for(
                    project_key=project_key,
                    version_key=version_key,
                    parent_revision=old_rev,
                    signature_rel_path=signature_rel_path,
                )

            hash_value, source_content_hash = await hashes.compute_sha512_and_content_hash(path_in_new_revision.path)

            async with aiofiles.open(hash_path_in_new_revision, "w") as f:
                await f.write(f"{hash_value}  {rel_path.name}\n")

            generated_rel = safe.RelPath(str(rel_path.parent / hash_path_rel_name))
            generator = attestable.GeneratorV2.SHA512_FROM_CONTENT
            metadata: dict[str, Any] = {
                "initiated_by": self.__asf_uid,
                "source_content_hashes": {str(rel_path): source_content_hash},
                "source_paths": [str(rel_path)],
            }
            if signature_metadata is not None:
                generator = attestable.GeneratorV2.SHA512_FROM_SIGNATURE
                metadata.update(signature_metadata)
            provenance = attestable.ProvenanceV2(generator=generator, metadata=metadata)
            return {generated_rel: provenance}

        await self.__write_as.revision.create_revision_with_quarantine(
            project_key,
            version_key,
            self.__asf_uid,
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            description=description,
            modify=modify,
        )

    async def import_from_svn(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        svn_url: safe.RelPath,
        svn_revision: str,
        target_subdirectory: safe.RelPath | None,
    ) -> sql.Task:
        release_key = sql.release_key(str(project_key), str(version_key))
        release = await self.__data.release(key=str(release_key)).demand(
            storage.AccessError(f"Release '{project_key!s} {version_key!s}' not found.", status=404)
        )
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        task_args = {
            "svn_url": svn_url,
            "revision": svn_revision,
            "target_subdirectory": str(target_subdirectory) if target_subdirectory else None,
            "project_key": project_key,
            "version_key": version_key,
            "asf_uid": self.__asf_uid,
        }
        svn_import_task = sql.Task(
            task_type=sql.TaskType.SVN_IMPORT_FILES,
            task_args=task_args,
            asf_uid=util.unwrap(self.__asf_uid),
            added=datetime.datetime.now(datetime.UTC),
            status=sql.TaskStatus.QUEUED,
            project_key=str(project_key),
            version_key=str(version_key),
        )
        self.__data.add(svn_import_task)
        await self.__data.commit()
        await self.__data.refresh(svn_import_task)
        return svn_import_task

    async def move_file(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        source_files_rel: list[safe.RelPath],
        target_dir_rel: safe.RelPath,
    ) -> tuple[str | None, list[str], list[str]]:
        description = "File move through web interface"
        moved_files_names: list[str] = []
        skipped_files_names: list[str] = []

        async def modify(path: safe.StatePath, _old_rev: sql.Revision | None) -> None:
            await self.__setup_revision(
                source_files_rel,
                target_dir_rel,
                path,
                moved_files_names,
                skipped_files_names,
            )

        try:
            await self.__write_as.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                self.__asf_uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                modify=modify,
            )
        except datatypes.FailedError as e:
            return str(e), moved_files_names, skipped_files_names
        return None, moved_files_names, skipped_files_names

    async def start_vote_no_commit(  # noqa: C901
        self,
        release_key: safe.ReleaseKey,
        expected_revision: safe.RevisionNumber | None,
        *,
        allowed_vote_modes: frozenset[sql.VoteMode],
        promote: bool,
        expected_podling_thread_id: str | None = None,
        acknowledged_concerns: frozenset[str] = frozenset(),
    ) -> tuple[sql.Release, int, sql.VoteMode, safe.RevisionNumber]:
        release_for_pre_checks = await self.__data.release(
            key=str(release_key), _project=True, _committee=True, _project_release_policy=True
        ).demand(storage.AccessError("Release candidate draft not found", status=404))
        storage.ensure_project_active(release_for_pre_checks.project)
        self.__write.ensure_release_writable(release_for_pre_checks)
        project_key = release_for_pre_checks.safe_project_key
        version_key = release_for_pre_checks.safe_version_key
        revision_number = release_for_pre_checks.safe_latest_revision_number
        if (expected_revision is not None) and (revision_number != expected_revision):
            raise storage.AccessError("A newer revision appeared, please refresh and try again.", status=409)
        revision_for_cas = expected_revision if (expected_revision is not None) else revision_number
        if promote:
            vote_mode = release_for_pre_checks.effective_vote_mode
        else:
            vote_mode = release_for_pre_checks.vote_mode
            if vote_mode is None:
                raise storage.AccessError("The release state has changed, please refresh and try again", status=409)
        if vote_mode not in allowed_vote_modes:
            raise storage.AccessError("This release's vote mode does not allow that action", status=409)

        # Check for ongoing tasks
        if promote:
            ongoing_tasks = await self.__tasks_ongoing(project_key, version_key, revision_number)
            if ongoing_tasks > 0:
                raise storage.AccessError("All checks must be completed before starting a vote", status=409)
            pending_quarantine = await interaction.pending_quarantine_count(
                release_for_pre_checks.key, caller_data=self.__data
            )
            if pending_quarantine > 0:
                raise storage.AccessError(
                    interaction.PENDING_QUARANTINE_VOTE_BLOCK_MESSAGE,
                    status=409,
                )

        # Verify that it's in the correct phase
        expected_phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
        if not promote:
            expected_phase = sql.ReleasePhase.RELEASE_CANDIDATE
        if release_for_pre_checks.phase != expected_phase:
            if promote:
                raise storage.AccessError("This release is not in the candidate draft phase", status=409)
            raise storage.AccessError("The release state has changed, please refresh and try again", status=409)

        if await interaction.has_blocker_checks(release_for_pre_checks, revision_number, caller_data=self.__data):
            raise storage.AccessError(
                "This release candidate draft has blockers. Please fix the blockers before starting a vote.",
                status=409,
            )

        # Check that there is at least one file in the draft
        if promote:
            file_count = await util.number_of_release_files(release_for_pre_checks)
            if file_count == 0:
                raise storage.AccessError("This candidate draft is empty, containing no files", status=400)

            required_groups = await self.__required_concern_groups(release_for_pre_checks)
            missing_groups = [g for g in required_groups if (g.checker not in acknowledged_concerns)]
            if missing_groups:
                raise storage.AccessError(
                    util.concern_acknowledgement_error(missing_groups),
                    status=409,
                )

        # Promote it to RELEASE_CANDIDATE
        via = sql.validate_instrumented_attribute
        vote_seq = await self.__vote_seq_allocate(release_for_pre_checks.key)
        now = datetime.datetime.now(datetime.UTC)
        release_activity_at = via(sql.Release.activity_at)
        activity_at_value = sqlalchemy.case(
            (release_activity_at > now, release_activity_at),
            else_=now,
        )
        notice_key_value = sqlalchemy.case(
            (release_activity_at > now, via(sql.Release.inactivity_notice_key)),
            else_=None,
        )
        values: dict[str, object] = {
            "vote_started": now,
            "vote_resolved": None,
            "current_vote_seq": vote_seq,
            "activity_at": activity_at_value,
            "inactivity_notice_key": notice_key_value,
        }
        if promote:
            values["phase"] = sql.ReleasePhase.RELEASE_CANDIDATE
            values["vote_mode"] = vote_mode
        stmt = sqlmodel.update(sql.Release).where(
            via(sql.Release.key) == release_for_pre_checks.key,
            via(sql.Release.phase) == expected_phase,
            sql.latest_revision_number_query() == str(revision_for_cas),
        )
        if not promote:
            if expected_podling_thread_id is None:
                stmt = stmt.where(via(sql.Release.podling_thread_id).is_(None))
            else:
                stmt = stmt.where(via(sql.Release.podling_thread_id) == expected_podling_thread_id)
        result = await self.__data.execute(stmt.values(**values))
        if not isinstance(result, engine.CursorResult):
            log.error(f"Expected cursor result, got {type(result)}")
            raise storage.AccessError("An error occurred while promoting the release candidate", status=500)
        if result.rowcount != 1:
            raise storage.AccessError("A newer revision appeared, please refresh and try again.", status=409)
        await self.__data.refresh(release_for_pre_checks, attribute_names=list(values))
        return release_for_pre_checks, vote_seq, vote_mode, revision_number

    async def __required_concern_groups(self, release: sql.Release) -> list[util.ConcernGroup]:
        # Avoid a filesystem walk when the DB has no concern rows at all
        check_results = await interaction.checks_for(release, caller_data=self.__data)
        if not any(cr.status == sql.CheckResultStatus.CONCERN for cr in check_results):
            return []
        read = storage.Read(self.__write.authorisation, self.__data)
        ragp = read.as_general_public()
        base_path = paths.release_directory(release)
        all_paths = sorted([path async for path in util.paths_recursive(base_path)])
        info = await ragp.releases.path_info(release, all_paths)
        return util.concern_groups(info)

    async def __vote_seq_allocate(self, release_key: str) -> int:
        upsert_stmt = (
            sqlite.insert(sql.VoteCounter)
            .values(release_key=release_key, last_allocated_number=1)
            .on_conflict_do_update(
                index_elements=["release_key"],
                set_={"last_allocated_number": sqlalchemy.text("last_allocated_number + 1")},
            )
            .returning(sqlalchemy.literal_column("last_allocated_number"))
        )
        result = await self.__data.execute(upsert_stmt)
        return int(result.scalar_one())

    async def start(
        self, project_key: safe.ProjectKey, version: safe.VersionKey, auto_archive: bool = False
    ) -> tuple[sql.Release, sql.Project]:
        return await _start_release(
            self.__data,
            self.__write,
            self.__write_as,
            self.__asf_uid,
            project_key,
            version,
            auto_archive,
            expedited=False,
        )

    async def upload_file(self, upload_args: api.ReleaseUploadArgs) -> sql.Revision | sql.Quarantined:
        file_bytes = base64.b64decode(upload_args.content, validate=True)
        validated_path = upload_args.relpath.as_path()
        description = f"Upload via API: {validated_path}"

        async def modify(path: safe.StatePath, _old_rev: sql.Revision | None) -> None:
            target_path = path / validated_path
            await aiofiles.os.makedirs(target_path.parent, exist_ok=True)
            async with self.__open_for_replace(target_path.path) as f:
                await f.write(file_bytes)

        result = await self.__write_as.revision.create_revision_with_quarantine(
            upload_args.project,
            upload_args.version,
            self.__asf_uid,
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            description=description,
            modify=modify,
            expected_revision=upload_args.expected_revision,
        )
        if isinstance(result, sql.Quarantined):
            return result
        async with db.session() as data:
            release_key = sql.release_key(upload_args.project, upload_args.version)
            return await data.revision(
                release_key=str(release_key),
                number=result.number,
            ).demand(storage.AccessError("Revision not found", status=404))

    async def upload_files(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        files: Sequence[datastructures.FileStorage],
    ) -> tuple[str | None, int, bool]:
        """Process and save the uploaded files into a new draft revision."""
        number_of_files = len(files)
        description = f"Upload of {util.plural(number_of_files, 'file')} through web interface"

        async def modify(path: safe.StatePath, _old_rev: sql.Revision | None) -> None:
            for file in files:
                if not file.filename:
                    raise storage.AccessError("No filename provided", status=400)
                # Validate the filename from multipart upload and construct the new path
                target_path = path / str(safe.RelPath(file.filename))
                await aiofiles.os.makedirs(target_path.parent, exist_ok=True)
                await self.__save_file(file, target_path.path)

        try:
            result = await self.__write_as.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                self.__asf_uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                modify=modify,
            )
        except datatypes.FailedError as e:
            return str(e), len(files), False
        return None, len(files), isinstance(result, sql.Quarantined)

    @contextlib.asynccontextmanager
    async def __open_for_replace(self, target_path: pathlib.Path) -> AsyncIterator[Any]:
        # Unlink first - the target could be a hardlink from a prior revision, locked 0o444.
        if await aiofiles.os.path.isfile(target_path):
            await aiofiles.os.remove(target_path)
        async with aiofiles.open(target_path, "wb") as f:
            yield f

    def __related_files(self, path: pathlib.Path) -> list[pathlib.Path]:
        base_path = path.with_suffix("") if (path.suffix in SPECIAL_SUFFIXES) else path
        parent_dir = base_path.parent
        name_without_ext = base_path.name
        return [
            parent_dir / name_without_ext,
            parent_dir / f"{name_without_ext}.asc",
            parent_dir / f"{name_without_ext}.sha256",
            parent_dir / f"{name_without_ext}.sha512",
        ]

    async def __save_file(self, file: datastructures.FileStorage, target_path: pathlib.Path) -> None:
        async with self.__open_for_replace(target_path) as f:
            while chunk := await asyncio.to_thread(file.stream.read, 8192):
                await f.write(chunk)

    async def __setup_revision(
        self,
        source_files_rel: list[safe.RelPath],
        target_dir_rel: safe.RelPath,
        interim_path: safe.StatePath,
        moved_files_names: list[str],
        skipped_files_names: list[str],
    ) -> None:
        target_path = interim_path / target_dir_rel
        try:
            resolved = await asyncio.to_thread(target_path.path.resolve)
            resolved.relative_to(await asyncio.to_thread(interim_path.path.resolve))
        except ValueError:
            # Path traversal detected
            raise datatypes.FailedError("Paths must be restricted to the release directory")

        if not await aiofiles.os.path.exists(target_path):
            for part in target_path.path.parts:
                # TODO: This .prefix check could include some existing directory segment
                # TODO: The second check probably isn't needed now since this came from a safe RelPath type.
                if util.is_disallowed_dotfile(part):
                    raise datatypes.FailedError("This segment is a disallowed dotfile")
                if ".." in part:
                    raise datatypes.FailedError("Segments must not contain '..'")

            try:
                await aiofiles.os.makedirs(target_path)
            except OSError:
                raise datatypes.FailedError("Failed to create target directory")
        elif not await aiofiles.os.path.isdir(target_path):
            raise datatypes.FailedError("Target path is not a directory")

        for source_file_rel in source_files_rel:
            await self.__setup_revision_item(
                source_file_rel, target_dir_rel, interim_path, moved_files_names, skipped_files_names, target_path
            )

    async def __setup_revision_item(
        self,
        source_file_rel: safe.RelPath,
        target_dir_rel: safe.RelPath,
        interim_path: safe.StatePath,
        moved_files_names: list[str],
        skipped_files_names: list[str],
        target_path: safe.StatePath,
    ) -> None:
        source_path = source_file_rel.as_path()
        target_dir_path = target_dir_rel.as_path()
        if source_path.parent == target_dir_path:
            skipped_files_names.append(source_path.name)
            return

        full_source_item_path: safe.StatePath = interim_path / source_path

        if await aiofiles.os.path.isdir(full_source_item_path):
            if (target_dir_rel == source_file_rel) or (interim_path / target_dir_path).path.resolve().is_relative_to(
                full_source_item_path.path.resolve()
            ):
                raise datatypes.FailedError("Cannot move a directory into itself or a subdirectory of itself")

            final_target_for_item = target_path / source_path.name
            if await aiofiles.os.path.exists(final_target_for_item):
                raise datatypes.FailedError("Target name already exists")

            await aiofiles.os.rename(full_source_item_path, final_target_for_item)
            moved_files_names.append(source_path.name)
        else:
            related_files = self.__related_files(source_path)
            bundle = [f for f in related_files if await aiofiles.os.path.exists(interim_path / f)]
            for f_check in bundle:
                if await aiofiles.os.path.isdir(interim_path / f_check):
                    raise datatypes.FailedError("A related 'file' is actually a directory")

            collisions = [f.name for f in bundle if await aiofiles.os.path.exists(target_path / f.name)]
            if collisions:
                raise datatypes.FailedError("A related file already exists in the target directory")

            for f in bundle:
                await aiofiles.os.rename(interim_path / f, target_path / f.name)
                if f == source_path:
                    moved_files_names.append(f.name)

    async def __tasks_ongoing(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: safe.RevisionNumber | None = None,
    ) -> int:
        tasks = sqlmodel.select(sqlalchemy.func.count()).select_from(sql.Task)
        query = tasks.where(
            sql.Task.project_key == str(project_key),
            sql.Task.version_key == str(version_key),
            sql.Task.revision_number
            == (sql.RELEASE_LATEST_REVISION_NUMBER if (revision_number is None) else str(revision_number)),
            sql.validate_instrumented_attribute(sql.Task.status).in_([sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE]),
        )
        result = await self.__data.execute(query)
        return result.scalar_one()


class ReleaseManager(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsReleaseManager,
        data: db.Session,
        committee_key: str,
    ) -> None:
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def promote_to_candidate(
        self,
        release_key: safe.ReleaseKey,
        expected_revision: safe.RevisionNumber,
        *,
        allowed_vote_modes: frozenset[sql.VoteMode],
        acknowledged_concerns: frozenset[str] = frozenset(),
    ) -> str | None:
        """Promote a release candidate draft to a new phase."""
        try:
            await self.__data.begin_immediate()
            release, vote_seq, vote_mode, revision_number = await self.start_vote_no_commit(
                release_key,
                expected_revision,
                allowed_vote_modes=allowed_vote_modes,
                promote=True,
                acknowledged_concerns=acknowledged_concerns,
            )
            await self.__data.commit()
        except storage.AccessError as e:
            await self.__data.rollback()
            return str(e)
        except Exception:
            await self.__data.rollback()
            raise

        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            release_key=release.key,
            revision_number=str(revision_number),
            vote_seq=vote_seq,
            vote_mode=vote_mode.value,
        )
        return None

    async def publish_to_svn(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        expected_revision: safe.RevisionNumber,
        download_path_suffix: safe.RelPath | None,
        *,
        publisher_asf_uid: str | None = None,
    ) -> sql.Task:
        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
            _project=True,
            _committee=True,
        ).demand(
            storage.AccessError(
                f"Release preview {project_key!s} {version_key!s} does not exist",
                status=404,
            )
        )
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        committee = release.project.committee
        if committee is None:
            raise storage.AccessError("Release has no committee - Invalid state", status=500)
        task_asf_uid = publisher_asf_uid or self.__asf_uid
        try:
            util.svn_publish_target()
            util.svn_publish_internal_url(committee, download_path_suffix)
        except ValueError as exc:
            raise storage.AccessError(f"SVN publish URL is not acceptable: {exc}", status=409) from exc
        await self.__data.begin_immediate()
        try:
            via = sql.validate_instrumented_attribute
            latest_stmt = (
                sqlmodel.select(sql.Revision.number)
                .where(via(sql.Revision.release_key) == release.key)
                .order_by(via(sql.Revision.seq).desc())
                .limit(1)
            )
            latest_revision_number = (await self.__data.execute(latest_stmt)).scalar_one_or_none()
            if latest_revision_number is None:
                raise storage.AccessError("Release has no revisions - Invalid state", status=500)
            if latest_revision_number != str(expected_revision):
                raise storage.AccessError("A newer revision appeared, please refresh and try again.", status=409)
            existing_in_flight = await interaction.release_in_flight_svn_publish_task(
                project_key, version_key, expected_revision, caller_data=self.__data
            )
            if existing_in_flight is not None:
                raise storage.AccessError(
                    "An SVN publish task is already queued or running for this revision", status=409
                )
            existing_completed_any_target = await interaction.release_completed_svn_publish_task_for_revision(
                project_key, version_key, expected_revision, caller_data=self.__data
            )
            if existing_completed_any_target is not None:
                raise storage.AccessError("An SVN publish task already completed for this revision", status=409)
            task = sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.SVN_PUBLISH,
                task_args=args.SvnPublish(
                    asf_uid=task_asf_uid,
                    project_key=project_key,
                    version_key=version_key,
                    revision_number=expected_revision,
                    download_path_suffix=download_path_suffix,
                ).model_dump(),
                asf_uid=task_asf_uid,
                project_key=str(project_key),
                version_key=str(version_key),
                revision_number=str(expected_revision),
            )
            self.__data.add(task)
            await self.__data.commit()
        except storage.AccessError:
            await self.__data.rollback()
            raise
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=task_asf_uid,
            project_key=str(project_key),
            version_key=str(version_key),
            revision_number=str(expected_revision),
        )
        return task

    async def publish_to_svn_execute(self, task_args: args.SvnPublish) -> results.SvnPublish:
        release = await self.__data.release(
            project_key=str(task_args.project_key),
            version=str(task_args.version_key),
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
            _project=True,
            _committee=True,
        ).demand(datatypes.FailedError("Release preview not found for publish"))
        storage.ensure_project_active(release.project)
        committee = release.project.committee
        if committee is None:
            raise datatypes.FailedError("Release has no committee - Invalid state")
        latest_revision = release.safe_latest_revision_number
        if latest_revision != task_args.revision_number:
            raise datatypes.FailedError("A newer revision appeared after queuing this publish task")
        try:
            internal_url = util.svn_publish_internal_url(committee, task_args.download_path_suffix)
        except ValueError as exc:
            raise datatypes.FailedError(f"SVN publish URL is not acceptable: {exc}") from exc
        preview_path = paths.release_directory(release)
        log_message = (
            f"Publish {task_args.project_key!s}-{task_args.version_key!s}\n\n"
            f"Committee: {committee.key}\n"
            f"Project: {task_args.project_key!s}\n"
            f"Version: {task_args.version_key!s}\n"
            f"Revision: {task_args.revision_number!s}\n"
            "Tool: ATR\n"
            f"Released by {task_args.asf_uid} via ATR"
        )

        try:
            revision = await svn.publish_release(preview_path.path, internal_url, task_args.asf_uid, log_message)
        except svn.CommandExecutionError as exc:
            log.exception("SVN publish failed")
            if "E160020" not in exc.output:
                raise datatypes.FailedError(svn.error_message(exc)) from exc
            healed = await self.__already_published_result(
                internal_url,
                task_args.asf_uid,
                log_message,
            )
            if healed is not None:
                return healed
            message = "Release file already exists in SVN"
            if (match := re.search(r"path '([^']+)'", exc.output)) is not None:
                message = f"{message}: {match.group(1)}"
                if svn_publish_url := config.get().SVN_PUBLISH_URL:
                    message = message.replace(svn_publish_url, "")
            raise datatypes.FailedError(message) from exc
        if revision is None:
            raise datatypes.FailedError("SVN publish did not return a Committed revision line")
        return results.SvnPublish(
            kind="svn_publish",
            svn_revision=revision,
            message=f"Published to SVN as r{revision}",
        )

    async def __already_published_result(
        self,
        internal_url: str,
        asf_uid: str,
        log_message: str,
    ) -> results.SvnPublish | None:
        try:
            info = await svn.SvnInfo.from_url(internal_url)
            matches = await svn.publish_revision_matches(info, asf_uid, log_message)
        except Exception:
            return None
        if not matches:
            return None
        revision = info.last_changed_rev_number
        return results.SvnPublish(
            kind="svn_publish",
            svn_revision=revision,
            message=f"Already published to SVN as r{revision}",
        )


class CommitteeMember(ReleaseManager):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_key: str,
    ) -> None:
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def archive(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
    ) -> None:
        """Archive a published release that no CAP vote covers.

        Only a release which is not the latest in its cycle may be archived this
        way. The write lock is taken before that is decided, so a release can't
        become the latest, or gain an archival vote, in between.
        """
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            project = await self.__data.project(key=str(project_key), _committee=True, _releases=True).get()
            if project is None:
                raise storage.AccessError(f"Project '{project_key}' not found.", status=404)
            release = await self.__data.release(
                project_key=str(project_key),
                version=str(version_key),
                _committee=True,
            ).get()
            if release is None:
                raise storage.AccessError(f"Release {project_key!s} {version_key!s} not found", status=404)
            await self.__assert_archivable_without_vote(project, release, project_key, version_key)
            error = await _archive_release(
                self.__data, self.__write_as, self.__asf_uid, project_key, version_key, release
            )
            if error is not None:
                raise storage.AccessError(error, status=409)
        except Exception:
            await self.__data.rollback()
            raise

    async def __assert_archivable_without_vote(
        self,
        project: sql.Project,
        release: sql.Release,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
    ) -> None:
        if release.phase != sql.ReleasePhase.RELEASE:
            raise storage.AccessError(
                f"Release {project_key!s} {version_key!s} is not in the release phase", status=409
            )
        if release.is_archived:
            raise storage.AccessError(f"Release {project_key!s} {version_key!s} is already archived.", status=409)
        active = [
            r
            for r in project.releases_including_embargoed
            if (r.phase == sql.ReleasePhase.RELEASE) and (not r.is_archived)
        ]
        latest = cycles.latest_release_in_cycle(project, release.version, active)
        if (latest is None) or (latest.key == release.key):
            raise storage.AccessError(
                f"Release {project_key!s} {version_key!s} is the latest in its cycle,"
                " so archiving it requires a CAP approval vote.",
                status=409,
            )
        approval = await self.__data.approval_request(
            project_key=str(project_key),
            status_in=[sql.ApprovalStatus.PENDING, sql.ApprovalStatus.APPROVED],
            release_version=str(version_key),
        ).get()
        if approval is not None:
            raise storage.AccessError("A CAP approval request for this release is already in progress.", status=409)

    async def start_expedited(
        self, project_key: safe.ProjectKey, version: safe.VersionKey, auto_archive: bool = False
    ) -> tuple[sql.Release, sql.Project]:
        return await _start_release(
            self.__data,
            self.__write,
            self.__write_as,
            self.__asf_uid,
            project_key,
            version,
            auto_archive,
            expedited=True,
        )


class FoundationAdmin(FoundationCommitter):
    BLOCKING_QUARANTINE_STATUSES: Final[frozenset[sql.QuarantineStatus]] = frozenset(
        {sql.QuarantineStatus.STAGING, sql.QuarantineStatus.PENDING}
    )

    ELIGIBLE_PHASES: Final[frozenset[sql.ReleasePhase]] = frozenset(
        {
            sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            sql.ReleasePhase.RELEASE_CANDIDATE,
        }
    )

    BLOCKING_TASK_STATUSES: Final[frozenset[sql.TaskStatus]] = frozenset({sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE})

    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid

    async def archive(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
    ) -> str | None:
        """Archive an announced release as the system.

        Used by the dist watcher when a release directory leaves the dist area.
        Shares the archive core with the committee-member path, log matches.
        """
        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
        ).get()
        if release is None:
            return f"Release {project_key!s} {version_key!s} not found"
        return await _archive_release(self.__data, self.__write_as, self.__asf_uid, project_key, version_key, release)

    async def complete_archive(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        approval_request_id: int,
    ) -> str | None:
        """Archive a release whose CAP approval vote has passed, as the system.

        The CAP resolve task runs this once a vote passes, so there's no
        committer in the loop to press the button. It mirrors the by-hand
        committee-member path: take the write lock, claim the approval, then
        archive. The lock is taken before the approval is read, so an approval
        can only ever complete one archival.
        """
        await self.__data.begin_immediate()
        self.__data.expire_all()
        try:
            release = await self.__data.release(
                project_key=str(project_key),
                version=str(version_key),
                _committee=True,
            ).get()
            if release is None:
                return f"Release {project_key!s} {version_key!s} not found"
            committee = release.committee
            if committee is None:
                return f"Release {project_key!s} {version_key!s} has no committee"
            await _claim_release_archive_approval(
                self.__data, approval_request_id, project_key, version_key, committee.key
            )
            if release.is_archived:
                # The watcher or a by-hand archive got there while the vote ran. The
                # approval has nothing left to do, so complete it rather than leave it
                # blocking the release
                await self.__data.commit()
                return None
            return await _archive_release(
                self.__data, self.__write_as, self.__asf_uid, project_key, version_key, release
            )
        except storage.AccessError as e:
            await self.__data.rollback()
            return str(e)
        except Exception:
            await self.__data.rollback()
            raise

    async def catalogue_release(
        self,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
        released: datetime.datetime,
        artifacts: Sequence[ArtifactInput],
    ) -> str | None:
        """Catalogue a release found published in the dist area.

        Records a release ATR didn't itself publish, so the artifacts are left
        unmanaged. The project must already exist - an already-catalogued
        release is left alone.
        """
        project = await self.__data.project(key=str(project_key)).get()
        if project is None:
            return f"Project {project_key!s} not found"
        release_key = f"{project.key}-{version!s}"
        if await self.__data.release(key=release_key).get() is not None:
            return None

        try:
            cycle_name = cycles.cycle_name_for_version(project, str(version))
        except ValueError:
            cycle_name = _CATALOGUE_DEFAULT_CYCLE
        cycle_key = f"{project.key}-{cycle_name}"
        try:
            if not await self.__data.project_cycle(cycle_key=cycle_key).get():
                self.__data.add(
                    sql.ProjectCycle(cycle_key=cycle_key, cycle=cycle_name, project_key=project.key, lts=False)
                )

            self.__data.add(
                sql.Release(
                    key=release_key,
                    phase=sql.ReleasePhase.RELEASE,
                    created=released,
                    released=released,
                    project_key=project.key,
                    cycle_key=cycle_key,
                    version=str(version),
                )
            )
            # Nothing relates LifecycleEvent to Release, so there's no
            # dependency to order these inserts by. The release row has to save
            # before the event's foreign key is checked
            await self.__data.flush()
            # effective is the commit date we observed, but published stays at the
            # default now - that's when we recorded it, not a backdated claim
            self.__data.add(
                sql.LifecycleEvent(
                    project_key=project.key,
                    cycle_key=cycle_key,
                    version_key=release_key,
                    event=sql.LifecycleEventType.RELEASE,
                    effective=released,
                )
            )
            for artifact in artifacts:
                self.__data.add(
                    sql.Artifact(
                        project_key=project.key,
                        version=str(version),
                        release_key=release_key,
                        artifact_path=artifact.artifact_path,
                        classification=artifact.classification,
                        signature_path=artifact.signature_path,
                        checksum_path=artifact.checksum_path,
                        sbom_path=artifact.sbom_path,
                        download_path_suffix=artifact.download_path_suffix,
                        managed=False,
                        dated=released,
                    )
                )
            # A newly catalogued release adds a page to the static site.
            await catalog_site.queue_regeneration(self.__data, self.__asf_uid, project.key)
            await self.__data.commit()
        except Exception:
            # The caller catalogues each release in turn on one session, so a failure
            # here has to leave it clean enough for the next release to use
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version=str(version),
            cycle_key=cycle_key,
            artifacts=len(artifacts),
        )
        log.info(f"Catalogued dist release {release_key} with {util.plural(len(artifacts), 'artifact')}")
        return None

    async def delete(
        self,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
    ) -> str | None:
        release = await self.__data.release(project_key=str(project_key), version=str(version), _committee=True).demand(
            storage.AccessError(f"Release '{project_key!s} {version!s}' not found.", status=404)
        )
        storage.ensure_project_active(release.project)
        return await self.__delete_body(release, project_key, version)

    async def delete_inactive(
        self,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
        dry_run: bool = False,
    ) -> str | None:
        error = await self.__check_eligibility(project_key, version)
        if error is not None:
            return error
        if dry_run:
            return None
        await self.__data.begin_immediate()
        self.__data.expire_all()
        final_error = await self.__check_eligibility(project_key, version)
        if final_error is not None:
            await self.__data.rollback()
            return final_error
        return await self.delete(project_key, version)

    async def notify_seen(
        self,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
        released: datetime.datetime,
    ) -> str | None:
        """Tell the releases list about a release seen published in the dist area.

        Queues the notification whether or not the release was catalogued, since
        it depends only on having seen it. The caller filters out releases ATR
        already knows about, so an ATR release never double-notifies.
        """
        project = await self.__data.project(key=str(project_key), _committee=True).get()
        if project is None:
            return f"Project {project_key!s} not found"
        if project.committee is None:
            return f"Project {project_key!s} has no committee"
        notification = construct.release_notification(project.committee, project, str(version), released, detected=True)
        self.__data.add(
            sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.MESSAGE_SEND,
                task_args=notification.as_task_args(),
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                version_key=str(version),
            )
        )
        await self.__data.commit()
        return None

    async def __delete_body(
        self,
        release: sql.Release,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
    ) -> str | None:
        release_dirs = [
            paths.release_directory_base(release),
            paths.get_attestable_dir() / str(project_key) / str(version),
            paths.get_archives_dir() / str(project_key) / str(version),
            paths.get_quarantined_dir() / str(project_key) / str(version),
        ]

        # Delete from the database using bulk SQL DELETE for efficiency
        log.info(f"Deleting database records for release: {project_key!s} {version!s}")

        # Bulk delete tasks
        # These is no cascade, so we must delete explicitly
        via = sql.validate_instrumented_attribute
        task_delete_stmt = sqlmodel.delete(sql.Task).where(
            via(sql.Task.project_key) == release.project.key,
            via(sql.Task.version_key) == release.version,
        )
        task_result = await self.__data.execute(task_delete_stmt)
        task_count = task_result.rowcount if isinstance(task_result, engine.CursorResult) else 0
        log.debug(f"Deleted {util.plural(task_count, 'task')} for {project_key!s} {version!s}")

        release_key = release.key

        # These deletes would also be performed by database cascade
        # We do them here before the commit instead to be explicit
        rfs_delete_stmt = sqlmodel.delete(sql.ReleaseFileState).where(
            via(sql.ReleaseFileState.release_key) == release_key,
        )
        rfs_result = await self.__data.execute(rfs_delete_stmt)
        rfs_count = rfs_result.rowcount if isinstance(rfs_result, engine.CursorResult) else 0
        log.debug(f"Deleted {util.plural(rfs_count, 'file state row')} for {project_key!s} {version!s}")

        await self.__data.delete(release)
        log.info(f"Deleted release record: {project_key!s} {version!s}")

        # In test mode, delete the counter for test committee releases
        # This allows revision numbers to be reused in testing
        committee = release.project.committee
        is_test_release = config.is_test_mode() and (committee is not None) and (committee.key == "test")
        if is_test_release:
            counter_delete_stmt = sqlmodel.delete(sql.RevisionCounter).where(
                via(sql.RevisionCounter.release_key) == release_key
            )
            await self.__data.execute(counter_delete_stmt)
            vote_counter_delete_stmt = sqlmodel.delete(sql.VoteCounter).where(
                via(sql.VoteCounter.release_key) == release_key
            )
            await self.__data.execute(vote_counter_delete_stmt)
            log.info(f"Deleted revision and vote counters for test release: {release_key}")

        # Filesystem deletions are more likely to have permission errors than database deletions
        # Therefore we do filesystem deletions first
        error = await self.__delete_release_data_filesystem(release_dirs, project_key, version)

        await self.__data.commit()

        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version=str(version),
            error=error,
        )
        return error

    async def __delete_release_data_filesystem(
        self, release_dirs: Sequence[safe.StatePath], project_key: safe.ProjectKey, version: safe.VersionKey
    ) -> str | None:
        delete_errors: list[str] = []
        for release_dir in release_dirs:
            if await aiofiles.os.path.isdir(release_dir):
                try:
                    log.info(f"Deleting filesystem directory: {release_dir}")
                    await util.delete_immutable_directory(
                        release_dir, reason=f"user {self.__asf_uid} is deleting release {project_key!s} {version!s}"
                    )
                    log.info(f"Successfully deleted directory: {release_dir}")
                except Exception as e:
                    log.exception(f"Error deleting filesystem directory {release_dir}:")
                    delete_errors.append(f"{release_dir}: {e!s}")
            else:
                log.warning(f"Filesystem directory not found, skipping deletion: {release_dir}")
        if delete_errors:
            return (
                f"Database records for '{project_key!s} {version!s}' deleted,"
                f" but failed to delete filesystem directories: {', '.join(delete_errors)}"
            )
        return None

    async def __check_eligibility(
        self,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
    ) -> str | None:
        release = await self.__data.release(project_key=str(project_key), version=str(version), _committee=True).get()
        if release is None:
            return "release no longer exists"
        if release.project.status != sql.ProjectStatus.ACTIVE:
            return f"release project '{release.project_key}' is not active"
        if release.phase not in FoundationAdmin.ELIGIBLE_PHASES:
            return f"release phase {release.phase.value} is not eligible for system deletion"
        now = datetime.datetime.now(datetime.UTC)
        age_days = (now - release.activity_at).days
        if age_days < constants.INACTIVITY_DELETE_DAYS:
            return f"release activity is only {age_days} days old; threshold is {constants.INACTIVITY_DELETE_DAYS}"
        if await self.__has_blocking_tasks(release):
            return "release has queued or active tasks"
        if await self.__has_blocking_quarantine(release):
            return "release has staging or pending quarantine rows"
        return None

    async def __has_blocking_quarantine(self, release: sql.Release) -> bool:
        via = sql.validate_instrumented_attribute
        stmt = (
            sqlmodel.select(sqlalchemy.func.count())
            .select_from(sql.Quarantined)
            .where(
                via(sql.Quarantined.release_key) == release.key,
                via(sql.Quarantined.status).in_(list(FoundationAdmin.BLOCKING_QUARANTINE_STATUSES)),
            )
        )
        result = await self.__data.execute(stmt)
        return int(result.scalar_one()) > 0

    async def __has_blocking_tasks(self, release: sql.Release) -> bool:
        via = sql.validate_instrumented_attribute
        stmt = (
            sqlmodel.select(sqlalchemy.func.count())
            .select_from(sql.Task)
            .where(
                via(sql.Task.project_key) == release.project_key,
                via(sql.Task.version_key) == release.version,
                via(sql.Task.status).in_(list(FoundationAdmin.BLOCKING_TASK_STATUSES)),
            )
        )
        result = await self.__data.execute(stmt)
        return int(result.scalar_one()) > 0
