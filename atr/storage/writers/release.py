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
import datetime
import hashlib
from typing import TYPE_CHECKING, Final

import aiofiles.os
import sqlalchemy
import sqlalchemy.engine as engine
import sqlmodel

import atr.analysis as analysis
import atr.config as config
import atr.db as db
import atr.form as form
import atr.log as log
import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.storage.types as types
import atr.util as util

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Sequence

    import werkzeug.datastructures as datastructures

SPECIAL_SUFFIXES: Final[frozenset[str]] = frozenset({".asc", ".sha256", ".sha512"})


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
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_name: str,
    ) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name

    async def delete(
        self,
        project_key: safe.ProjectKey,
        version: safe.VersionKey,
        phase: db.Opt[sql.ReleasePhase] = db.NOT_SET,
        include_downloads: bool = True,
    ) -> str | None:
        """Handle the deletion of database records and filesystem data for a release."""
        release = await self.__data.release(
            project_key=str(project_key), version=str(version), phase=phase, _committee=True
        ).demand(storage.AccessError(f"Release '{project_key!s} {version!s}' not found."))
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

        release_name = release.key

        # These deletes would also be performed by database cascade
        # We do them here before the commit instead to be explicit
        rfs_delete_stmt = sqlmodel.delete(sql.ReleaseFileState).where(
            via(sql.ReleaseFileState.release_key) == release_name,
        )
        rfs_result = await self.__data.execute(rfs_delete_stmt)
        rfs_count = rfs_result.rowcount if isinstance(rfs_result, engine.CursorResult) else 0
        log.debug(f"Deleted {util.plural(rfs_count, 'file state row')} for {project_key!s} {version!s}")

        await self.__data.delete(release)
        log.info(f"Deleted release record: {project_key!s} {version!s}")

        # In test mode, delete the counter for test committee releases
        # This allows revision numbers to be reused in testing
        committee = release.project.committee
        is_test_release = config.get().ALLOW_TESTS and (committee is not None) and (committee.key == "test")
        if is_test_release:
            counter_delete_stmt = sqlmodel.delete(sql.RevisionCounter).where(
                via(sql.RevisionCounter.release_key) == release_name
            )
            await self.__data.execute(counter_delete_stmt)
            log.info(f"Deleted revision counter for test release: {release_name}")

        # Filesystem deletions are more likely to have permission errors than database deletions
        # Therefore we do filesystem deletions first
        if include_downloads:
            await self.__delete_release_data_downloads(release)
        error = await self.__delete_release_data_filesystem(release_dirs, project_key, version)

        await self.__data.commit()

        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version=str(version),
            error=error,
        )
        return error

    async def delete_empty_directory(
        self, project_key: safe.ProjectKey, version_key: safe.VersionKey, dir_to_delete_rel: pathlib.Path
    ) -> str | None:
        description = f"Delete empty directory {dir_to_delete_rel} via web interface"

        async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
            path_to_remove = path / dir_to_delete_rel
            resolved = await asyncio.to_thread(path_to_remove.resolve)
            resolved.relative_to(await asyncio.to_thread(path.resolve))
            if not await aiofiles.os.path.isdir(path_to_remove):
                raise types.FailedError(f"Path '{dir_to_delete_rel}' is not a directory.")
            if await aiofiles.os.listdir(path_to_remove):
                raise types.FailedError(f"Directory '{dir_to_delete_rel}' is not empty.")
            await aiofiles.os.rmdir(path_to_remove)

        try:
            await self.__write_as.revision.create_revision_with_quarantine(
                project_key, version_key, self.__asf_uid, description=description, modify=modify
            )
        except types.FailedError as e:
            return str(e)
        return None

    async def delete_file(
        self, project_key: safe.ProjectKey, version: safe.VersionKey, rel_path_to_delete: pathlib.Path
    ) -> int:
        metadata_files_deleted = 0
        description = "File deletion through web interface"

        async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
            nonlocal metadata_files_deleted
            # Uses new_revision_number for logging only
            # Path to delete within the new revision directory
            path_in_new_revision = path / rel_path_to_delete

            # Make sure the requested path is relative to the actual path
            resolved = await asyncio.to_thread(path_in_new_revision.resolve)
            resolved.relative_to(await asyncio.to_thread(path.resolve))

            # Check that the file exists in the new revision
            if not await aiofiles.os.path.exists(path_in_new_revision):
                # This indicates a potential severe issue with hard linking or logic
                log.error(f"SEVERE ERROR! File {rel_path_to_delete} not found in new revision before deletion")
                raise storage.AccessError("File to delete was not found in the new revision")

            # Check whether the file is an artifact
            if analysis.is_artifact(path_in_new_revision):
                # If so, delete all associated metadata files in the new revision
                async for p in util.paths_recursive(path_in_new_revision.parent):
                    # Construct full path within the new revision
                    metadata_path_obj = path / p
                    if p.name.startswith(rel_path_to_delete.name + "."):
                        await aiofiles.os.remove(metadata_path_obj)
                        metadata_files_deleted += 1

            # Delete the file
            await aiofiles.os.remove(path_in_new_revision)

        await self.__write_as.revision.create_revision_with_quarantine(
            project_key, version, self.__asf_uid, description=description, modify=modify
        )
        return metadata_files_deleted

    async def generate_hash_file(
        self, project_key: safe.ProjectKey, version_key: safe.VersionKey, rel_path: pathlib.Path
    ) -> None:
        description = "Hash generation through web interface"

        async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
            # Uses new_revision_number for logging only
            path_in_new_revision = path / rel_path

            # Make sure the requested path is relative to the actual path
            resolved = await asyncio.to_thread(path_in_new_revision.resolve)
            resolved.relative_to(await asyncio.to_thread(path.resolve))

            hash_path_rel = rel_path.name + ".sha512"
            hash_path_in_new_revision = path / rel_path.parent / hash_path_rel

            # Check that the source file exists in the new revision
            if not await aiofiles.os.path.exists(path_in_new_revision):
                log.error(f"Source file {rel_path} not found in new revision for hash generation.")
                raise storage.AccessError("Source file not found in the new revision.")

            # Check that the hash file does not already exist in the new revision
            if await aiofiles.os.path.exists(hash_path_in_new_revision):
                raise storage.AccessError("SHA512 file already exists")

            # Read the source file from the new revision and compute the hash
            hash_obj = hashlib.sha512()
            async with aiofiles.open(path_in_new_revision, "rb") as f:
                while chunk := await f.read(8192):
                    hash_obj.update(chunk)

            # Write the hash file into the new revision
            hash_value = hash_obj.hexdigest()
            async with aiofiles.open(hash_path_in_new_revision, "w") as f:
                await f.write(f"{hash_value}  {rel_path.name}\n")

        await self.__write_as.revision.create_revision_with_quarantine(
            project_key, version_key, self.__asf_uid, description=description, modify=modify
        )

    async def import_from_svn(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        svn_url: str,
        revision: str,
        target_subdirectory: str | None,
    ) -> sql.Task:
        task_args = {
            "svn_url": svn_url,
            "revision": revision,
            "target_subdirectory": target_subdirectory,
            "project_key": str(project_key),
            "version_key": str(version_key),
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
        source_files_rel: list[pathlib.Path],
        target_dir_rel: pathlib.Path,
    ) -> tuple[str | None, list[str], list[str]]:
        description = "File move through web interface"
        moved_files_names: list[str] = []
        skipped_files_names: list[str] = []

        async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
            await self.__setup_revision(
                source_files_rel,
                target_dir_rel,
                path,
                moved_files_names,
                skipped_files_names,
            )

        try:
            await self.__write_as.revision.create_revision_with_quarantine(
                project_key, version_key, self.__asf_uid, description=description, modify=modify
            )
        except types.FailedError as e:
            return str(e), moved_files_names, skipped_files_names
        return None, moved_files_names, skipped_files_names

    async def promote_to_candidate(
        self,
        release_name: safe.ReleaseKey,
        selected_revision_number: safe.RevisionNumber,
        vote_manual: bool = False,
    ) -> str | None:
        """Promote a release candidate draft to a new phase."""
        release_for_pre_checks = await self.__data.release(key=str(release_name), _project=True).demand(
            storage.AccessError("Release candidate draft not found")
        )
        project_key = release_for_pre_checks.safe_project_key
        version_key = release_for_pre_checks.safe_version_key
        revision_number = release_for_pre_checks.safe_latest_revision_number

        # Check for ongoing tasks
        ongoing_tasks = await self.__tasks_ongoing(project_key, version_key, selected_revision_number)
        if ongoing_tasks > 0:
            return "All checks must be completed before starting a vote"

        # Verify that it's in the correct phase
        if release_for_pre_checks.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            return "This release is not in the candidate draft phase"

        # Check that the revision number is the latest
        if revision_number != selected_revision_number:
            return "The selected revision number does not match the latest revision number"

        # Check that there is at least one file in the draft
        file_count = await util.number_of_release_files(release_for_pre_checks)
        if file_count == 0:
            return "This candidate draft is empty, containing no files"

        # Promote it to RELEASE_CANDIDATE
        via = sql.validate_instrumented_attribute
        stmt = (
            sqlmodel.update(sql.Release)
            .where(
                via(sql.Release.key) == release_for_pre_checks.key,
                via(sql.Release.phase) == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                sql.latest_revision_number_query() == str(selected_revision_number),
            )
            .values(
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                vote_started=datetime.datetime.now(datetime.UTC),
                vote_resolved=None,
                vote_manual=vote_manual,
            )
        )

        result = await self.__data.execute(stmt)
        if not isinstance(result, engine.CursorResult):
            log.error(f"Expected cursor result, got {type(result)}")
            return "An error occurred while promoting the release candidate"
        if result.rowcount != 1:
            await self.__data.rollback()
            return "A newer revision appeared, please refresh and try again."
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            release_name=str(release_name),
            selected_revision_number=str(selected_revision_number),
            vote_manual=vote_manual,
        )
        return None

    async def remove_rc_tags(
        self, project_key: safe.ProjectKey, version_key: safe.VersionKey
    ) -> tuple[str | None, int, list[str]]:
        description = "Remove RC tags from paths via web interface"
        error_messages: list[str] = []
        renamed_count = 0

        async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
            nonlocal renamed_count
            renamed_count = await self.__remove_rc_tags_revision(path, error_messages)

        try:
            await self.__write_as.revision.create_revision_with_quarantine(
                project_key, version_key, self.__asf_uid, description=description, modify=modify
            )
        except types.FailedError as e:
            return str(e), renamed_count, error_messages
        return None, renamed_count, error_messages

    async def start(self, project_key: safe.ProjectKey, version: safe.VersionKey) -> tuple[sql.Release, sql.Project]:  # noqa: C901
        """Creates the initial release draft record and revision directory."""
        # Get the project from the project name
        project = await self.__data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _committee=True
        ).get()
        if not project:
            raise storage.AccessError(f"Project {project_key} not found")

        tests_allowed = config.get().ALLOW_TESTS
        committee = project.committee
        is_test_committee = tests_allowed and (committee is not None) and (committee.key == "test")
        should_skip_auth = is_test_committee

        if not should_skip_auth:
            display_name = project.display_name
            if committee is None:
                raise storage.AccessError(
                    f"You must be a member or committer of the {display_name} committee to start a release draft."
                )

            is_committee_member = self.__asf_uid in committee.committee_members
            is_committee_committer = self.__asf_uid in committee.committers
            has_committee_access = is_committee_member or is_committee_committer

            if not has_committee_access:
                raise storage.AccessError(
                    f"You must be a member or committer of the {display_name} committee to start a release draft."
                )

        # TODO: Consider using Release.revision instead of ./latest
        # Check whether the release already exists
        if release := await self.__data.release(project_key=project.key, version=str(version)).get():
            match release.phase:
                case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
                    phase_desc = "A draft release (being composed)"
                case sql.ReleasePhase.RELEASE_CANDIDATE:
                    phase_desc = "A release candidate (being voted on)"
                case sql.ReleasePhase.RELEASE_PREVIEW:
                    phase_desc = "A release preview (being finished)"
                case sql.ReleasePhase.RELEASE:
                    phase_desc = "A finished release"
            raise storage.AccessError(f"{phase_desc} for {project_key!s} {version} already exists.")

        # Validate the version name
        # TODO: We should check that it's bigger than the current version
        # We have the packaging library as a dependency, but it is Python specific
        if version_key_error := util.version_key_error(str(version)):
            raise storage.AccessError(f'Invalid version name "{version!s}": {version_key_error}')
        release = sql.Release(
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            project_key=project.key,
            project=project,
            version=str(version),
            created=datetime.datetime.now(datetime.UTC),
        )
        self.__data.add(release)
        await self.__data.commit()
        await self.__data.refresh(release)

        description = "Creation of empty release candidate draft through web interface"
        await self.__write_as.revision.create_revision_with_quarantine(
            project_key, version, self.__asf_uid, description=description
        )
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=project.key,
            version=str(version),
            created=release.created.isoformat(),
        )
        return release, project

    async def upload_file(self, args: api.ReleaseUploadArgs) -> sql.Revision | sql.Quarantined:
        file_bytes = base64.b64decode(args.content, validate=True)
        validated_path = form.to_relpath(args.relpath)
        if validated_path is None:
            raise storage.AccessError("Invalid file path")
        description = f"Upload via API: {validated_path}"

        async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
            target_path = path / validated_path
            await aiofiles.os.makedirs(target_path.parent, exist_ok=True)
            if await aiofiles.os.path.exists(target_path):
                raise storage.AccessError("File already exists")
            async with aiofiles.open(target_path, "wb") as f:
                await f.write(file_bytes)

        result = await self.__write_as.revision.create_revision_with_quarantine(
            args.project, args.version, self.__asf_uid, description=description, modify=modify
        )
        if isinstance(result, sql.Quarantined):
            return result
        async with db.session() as data:
            release_name = sql.release_key(args.project, args.version)
            return await data.revision(
                release_key=str(release_name),
                number=result.number,
            ).demand(storage.AccessError("Revision not found"))

    async def upload_files(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        files: Sequence[datastructures.FileStorage],
    ) -> tuple[str | None, int, bool]:
        """Process and save the uploaded files into a new draft revision."""
        number_of_files = len(files)
        description = f"Upload of {util.plural(number_of_files, 'file')} through web interface"

        async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
            for file in files:
                if not file.filename:
                    raise storage.AccessError("No filename provided")
                # Validate the filename from multipart upload
                validated_path = form.to_relpath(file.filename)
                if validated_path is None:
                    raise storage.AccessError("Invalid filename")
                target_path = path / validated_path
                await aiofiles.os.makedirs(target_path.parent, exist_ok=True)
                await self.__save_file(file, target_path)

        try:
            result = await self.__write_as.revision.create_revision_with_quarantine(
                project_key, version_key, self.__asf_uid, description=description, modify=modify
            )
        except types.FailedError as e:
            return str(e), len(files), False
        return None, len(files), isinstance(result, sql.Quarantined)

    async def __current_paths(self, interim_path: pathlib.Path) -> list[pathlib.Path]:
        all_current_paths_interim: list[pathlib.Path] = []
        async for p_rel_interim in util.paths_recursive_all(interim_path):
            all_current_paths_interim.append(p_rel_interim)

        # This manner of sorting is necessary to ensure that directories are removed after their contents
        all_current_paths_interim.sort(key=lambda p: (-len(p.parts), str(p)))
        return all_current_paths_interim

    async def __delete_release_data_downloads(self, release: sql.Release) -> None:
        # Delete hard links from the downloads directory
        finished_dir = paths.release_directory(release)
        if await aiofiles.os.path.isdir(finished_dir):
            release_inodes = set()
            async for file_path in util.paths_recursive(finished_dir):
                try:
                    stat_result = await aiofiles.os.stat(finished_dir / file_path)
                    release_inodes.add(stat_result.st_ino)
                except FileNotFoundError:
                    continue

            if release_inodes:
                downloads_dir = paths.get_downloads_dir()
                async for link_path in util.paths_recursive(downloads_dir):
                    full_link_path = downloads_dir / link_path
                    try:
                        link_stat = await aiofiles.os.stat(full_link_path)
                        if link_stat.st_ino in release_inodes:
                            await aiofiles.os.remove(full_link_path)
                            log.info(f"Deleted hard link: {full_link_path}")
                    except FileNotFoundError:
                        continue

    async def __delete_release_data_filesystem(
        self, release_dirs: Sequence[pathlib.Path], project_key: safe.ProjectKey, version: safe.VersionKey
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

    async def __remove_rc_tags_revision(
        self,
        interim_path: pathlib.Path,
        error_messages: list[str],
    ) -> int:
        all_current_paths_interim = await self.__current_paths(interim_path)
        renamed_count_local = 0
        for path_rel_original_interim in all_current_paths_interim:
            path_rel_stripped_interim = analysis.candidate_removed(path_rel_original_interim)

            if path_rel_original_interim != path_rel_stripped_interim:
                # Absolute paths of the source and destination
                full_original_path = interim_path / path_rel_original_interim
                full_stripped_path = interim_path / path_rel_stripped_interim

                skip, renamed_count_local = await self.__remove_rc_tags_revision_item(
                    path_rel_original_interim,
                    full_original_path,
                    full_stripped_path,
                    error_messages,
                    renamed_count_local,
                )
                if skip:
                    continue

                try:
                    if not await aiofiles.os.path.exists(full_stripped_path.parent):
                        # This could happen if e.g. a file is in an RC tagged directory
                        await aiofiles.os.makedirs(full_stripped_path.parent, exist_ok=True)

                    if await aiofiles.os.path.exists(full_stripped_path):
                        error_messages.append(
                            f"Skipped '{path_rel_original_interim}':"
                            f" target '{path_rel_stripped_interim}' already exists."
                        )
                        continue

                    await aiofiles.os.rename(full_original_path, full_stripped_path)
                    renamed_count_local += 1
                except Exception as e:
                    error_messages.append(f"Error renaming '{path_rel_original_interim}': {e}")
        return renamed_count_local

    async def __remove_rc_tags_revision_item(
        self,
        path_rel_original_interim: pathlib.Path,
        full_original_path: pathlib.Path,
        full_stripped_path: pathlib.Path,
        error_messages: list[str],
        renamed_count_local: int,
    ) -> tuple[bool, int]:
        if await aiofiles.os.path.isdir(full_original_path):
            # If moving an RC tagged directory to an existing directory...
            is_target_dir_and_exists = await aiofiles.os.path.isdir(full_stripped_path)
            if is_target_dir_and_exists and (full_stripped_path != full_original_path):
                try:
                    # And the source directory is empty...
                    if not await aiofiles.os.listdir(full_original_path):
                        # This means we probably moved files out of the RC tagged directory
                        # In any case, we can't move it, so we have to delete it
                        await aiofiles.os.rmdir(full_original_path)
                        renamed_count_local += 1
                    else:
                        error_messages.append(
                            f"Source RC directory '{path_rel_original_interim}' is not empty, skipping."
                        )
                except OSError as e:
                    error_messages.append(f"Error removing source RC directory '{path_rel_original_interim}': {e}")
                return True, renamed_count_local
        return False, renamed_count_local

    async def __save_file(self, file: datastructures.FileStorage, target_path: pathlib.Path) -> None:
        async with aiofiles.open(target_path, "wb") as f:
            while chunk := await asyncio.to_thread(file.stream.read, 8192):
                await f.write(chunk)

    async def __setup_revision(
        self,
        source_files_rel: list[pathlib.Path],
        target_dir_rel: pathlib.Path,
        interim_path: pathlib.Path,
        moved_files_names: list[str],
        skipped_files_names: list[str],
    ) -> None:
        target_path = interim_path / target_dir_rel
        try:
            resolved = await asyncio.to_thread(target_path.resolve)
            resolved.relative_to(await asyncio.to_thread(interim_path.resolve))
        except ValueError:
            # Path traversal detected
            raise types.FailedError("Paths must be restricted to the release directory")

        if not await aiofiles.os.path.exists(target_path):
            for part in target_path.parts:
                # TODO: This .prefix check could include some existing directory segment
                if util.is_disallowed_dotfile(part):
                    raise types.FailedError("This segment is a disallowed dotfile")
                if ".." in part:
                    raise types.FailedError("Segments must not contain '..'")

            try:
                await aiofiles.os.makedirs(target_path)
            except OSError:
                raise types.FailedError("Failed to create target directory")
        elif not await aiofiles.os.path.isdir(target_path):
            raise types.FailedError("Target path is not a directory")

        for source_file_rel in source_files_rel:
            await self.__setup_revision_item(
                source_file_rel, target_dir_rel, interim_path, moved_files_names, skipped_files_names, target_path
            )

    async def __setup_revision_item(
        self,
        source_file_rel: pathlib.Path,
        target_dir_rel: pathlib.Path,
        interim_path: pathlib.Path,
        moved_files_names: list[str],
        skipped_files_names: list[str],
        target_path: pathlib.Path,
    ) -> None:
        if source_file_rel.parent == target_dir_rel:
            skipped_files_names.append(source_file_rel.name)
            return

        full_source_item_path = interim_path / source_file_rel

        if await aiofiles.os.path.isdir(full_source_item_path):
            if (target_dir_rel == source_file_rel) or (interim_path / target_dir_rel).resolve().is_relative_to(
                full_source_item_path.resolve()
            ):
                raise types.FailedError("Cannot move a directory into itself or a subdirectory of itself")

            final_target_for_item = target_path / source_file_rel.name
            if await aiofiles.os.path.exists(final_target_for_item):
                raise types.FailedError("Target name already exists")

            await aiofiles.os.rename(full_source_item_path, final_target_for_item)
            moved_files_names.append(source_file_rel.name)
        else:
            related_files = self.__related_files(source_file_rel)
            bundle = [f for f in related_files if await aiofiles.os.path.exists(interim_path / f)]
            for f_check in bundle:
                if await aiofiles.os.path.isdir(interim_path / f_check):
                    raise types.FailedError("A related 'file' is actually a directory")

            collisions = [f.name for f in bundle if await aiofiles.os.path.exists(target_path / f.name)]
            if collisions:
                raise types.FailedError("A related file already exists in the target directory")

            for f in bundle:
                await aiofiles.os.rename(interim_path / f, target_path / f.name)
                if f == source_file_rel:
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


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_name: str,
    ) -> None:
        super().__init__(write, write_as, data, committee_name)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name


class FoundationAdmin(FoundationCommitter):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
