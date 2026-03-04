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
import contextlib
import datetime
import pathlib
import secrets
import tempfile
import uuid
from typing import TYPE_CHECKING, Final

import aiofiles.os
import aioshutil

import atr.attestable as attestable
import atr.db as db
import atr.db.interaction as interaction
import atr.detection as detection
import atr.merge as merge
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.storage.types as types
import atr.tasks as tasks
import atr.util as util

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import atr.models.attestable

_QUARANTINE_TOKEN_ALPHABET: Final[str] = "qpzry9x8gf2tvdw0s3jn54khce6mua7b"
_QUARANTINE_TOKEN_LENGTH: Final[int] = 24


class SafeSession:
    def __init__(self, temp_dir: str):
        self._stack = contextlib.AsyncExitStack()
        self._manager = db.session()
        self._temp_dir = temp_dir

    async def __aenter__(self) -> db.Session:
        try:
            return await self._stack.enter_async_context(self._manager)
        except Exception:
            await aioshutil.rmtree(self._temp_dir)
            raise

    async def __aexit__(self, _exc_type, _exc, _tb):
        await self._stack.aclose()
        return False


async def finalise_revision(
    data: db.Session,
    *,
    asf_uid: str,
    base_hashes: dict[str, str],
    base_inodes: dict[str, int],
    description: str | None,
    merge_enabled: bool,
    n_inodes: dict[str, int],
    old_revision: sql.Revision | None,
    path_to_hash: dict[str, str],
    path_to_size: dict[str, int],
    previous_attestable: atr.models.attestable.AttestableV1 | None,
    project_name: safe.ProjectName,
    release: sql.Release,
    release_name: safe.ReleaseName,
    temp_dir: str,
    temp_dir_path: pathlib.Path,
    version_name: safe.VersionName,
    was_quarantined: bool = False,
) -> sql.Revision:
    try:
        previous_attestable, _, merged_release = await _lock_and_merge(
            data,
            base_hashes=base_hashes,
            base_inodes=base_inodes,
            merge_enabled=merge_enabled,
            n_inodes=n_inodes,
            old_revision=old_revision,
            path_to_hash=path_to_hash,
            path_to_size=path_to_size,
            previous_attestable=previous_attestable,
            project_name=project_name,
            release=release,
            _release_name=release_name,
            temp_dir_path=temp_dir_path,
            version_name=version_name,
        )
    except Exception:
        await aioshutil.rmtree(temp_dir)
        raise

    return await _commit_new_revision(
        data,
        asf_uid=asf_uid,
        description=description,
        path_to_hash=path_to_hash,
        path_to_size=path_to_size,
        previous_attestable=previous_attestable,
        project_name=project_name,
        release=merged_release,
        release_name=str(release_name),
        temp_dir=temp_dir,
        version_name=version_name,
        was_quarantined=was_quarantined,
    )


async def _commit_new_revision(
    data: db.Session,
    *,
    asf_uid: str,
    description: str | None,
    path_to_hash: dict[str, str],
    path_to_size: dict[str, int],
    previous_attestable: atr.models.attestable.AttestableV1 | None,
    project_name: safe.ProjectName,
    release: sql.Release,
    release_name: str,
    temp_dir: str,
    version_name: safe.VersionName,
    was_quarantined: bool = False,
) -> sql.Revision:
    try:
        # This is the only place where models.Revision is constructed
        # That makes models.populate_revision_sequence_and_name safe against races
        # Because that event is called when data.add is called below
        # And we have a write lock at that point through the use of data.begin_immediate
        new_revision = sql.Revision(
            release_name=release_name,
            release=release,
            asfuid=asf_uid,
            created=datetime.datetime.now(datetime.UTC),
            phase=release.phase,
            description=description,
            was_quarantined=was_quarantined,
        )
        data.add(new_revision)

        # Flush but do not commit the new revision row to get its name and number
        # The row will still be invisible to other sessions after flushing
        await data.flush()

        # Rename the directory to the new revision number
        await data.refresh(release)
        new_revision_dir = paths.release_directory(release)

        # Ensure that the parent directory exists
        await aiofiles.os.makedirs(new_revision_dir.parent, exist_ok=True)

        # Raise an error if the destination directory already exists
        # This can happen for example if there was a previous failed cleanup
        if await aiofiles.os.path.exists(new_revision_dir):
            raise types.FailedError(f"Revision directory {new_revision_dir} already exists")

        # Rename the temporary interim directory to the new revision number
        await aiofiles.os.rename(temp_dir, new_revision_dir)
    except Exception:
        await aioshutil.rmtree(temp_dir)
        raise

    # Change permissions of all directories in the new revision directory to 555
    # This prevents accidental modifications to any directory in the new revision
    # This must be done after the rename, otherwise the rename will fail
    # The ".." entry in a directory is modified when it is moved between parents
    # (Additionally, on macOS a 555 directory cannot be renamed within the same parent)
    await asyncio.to_thread(util.chmod_directories, new_revision_dir, 0o555)

    policy = release.release_policy or release.project.release_policy

    await attestable.write_files_data(
        project_name,
        version_name,
        new_revision.number,
        policy.model_dump() if policy else None,
        asf_uid,
        previous_attestable,
        path_to_hash,
        path_to_size,
    )

    # Commit to end the transaction started by data.begin_immediate
    # We must commit the revision before starting the checks
    # This also releases the write lock obtained in _lock_and_merge
    await data.commit()

    async with data.begin():
        # Run checks if in DRAFT phase
        # We could also run this outside the data Session
        # But then it would create its own new Session
        # It does, however, need a transaction to be created using data.begin()
        if release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            # Must use caller_data here because we acquired the write lock
            await tasks.draft_checks(asf_uid, project_name, version_name, new_revision.number, caller_data=data)

    return new_revision


def _generate_quarantine_token() -> str:
    value = int.from_bytes(secrets.token_bytes(15), "big")
    alphabet = _QUARANTINE_TOKEN_ALPHABET
    chars: list[str] = []
    for _ in range(_QUARANTINE_TOKEN_LENGTH):
        chars.append(alphabet[value & 0x1F])
        value >>= 5
    return "".join(chars)


async def _lock_and_merge(
    data: db.Session,
    *,
    base_hashes: dict[str, str],
    base_inodes: dict[str, int],
    merge_enabled: bool,
    n_inodes: dict[str, int],
    old_revision: sql.Revision | None,
    path_to_hash: dict[str, str],
    path_to_size: dict[str, int],
    previous_attestable: atr.models.attestable.AttestableV1 | None,
    project_name: safe.ProjectName,
    release: sql.Release,
    _release_name: safe.ReleaseName,
    temp_dir_path: pathlib.Path,
    version_name: safe.VersionName,
) -> tuple[atr.models.attestable.AttestableV1 | None, str | None, sql.Release]:
    # Acquire the write lock
    # We need this write lock for moving the directory afterwards atomically
    # But it also helps to make models.populate_revision_sequence_and_name safe against races
    await data.begin_immediate()

    merged_release: sql.Release = await data.merge(release)
    await data.refresh(merged_release)
    latest = await interaction.latest_revision(merged_release, caller_data=data)
    prior_revision_name = latest.name if latest else None

    # Merge with the prior revision if there was an intervening change
    if (
        merge_enabled
        and (old_revision is not None)
        and (prior_revision_name is not None)
        and (prior_revision_name != old_revision.name)
    ):
        prior_number = prior_revision_name.split()[-1]
        prior_dir = paths.release_directory_base(merged_release) / prior_number
        await merge.merge(
            base_inodes,
            base_hashes,
            prior_dir,
            project_name,
            version_name,
            prior_number,
            temp_dir_path,
            n_inodes,
            path_to_hash,
            path_to_size,
        )
        previous_attestable = await attestable.load(project_name, version_name, prior_number)

    return previous_attestable, prior_revision_name, merged_release


class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ):
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write.authorisation.asf_uid


class FoundationCommitter(GeneralPublic):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationCommitter, data: db.Session):
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
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name

    async def create_revision_with_quarantine(  # noqa: C901
        self,
        project_name: safe.ProjectName,
        version_name: safe.VersionName,
        asf_uid: str,
        description: str | None = None,
        set_local_cache: bool = False,
        reset_to_global_cache: bool = False,
        modify: Callable[[pathlib.Path, sql.Revision | None], Awaitable[None]] | None = None,
        clone_from: str | None = None,
    ) -> sql.Revision | sql.Quarantined:
        """Create a new revision, quarantining archives that require validation."""
        release_name = sql.release_name(str(project_name), str(version_name))
        async with db.session() as data:
            release = await data.release(name=release_name, _release_policy=True, _project_release_policy=True).demand(
                RuntimeError("Release does not exist for new revision creation")
            )
            if clone_from is not None:
                old_revision = await data.revision(release_name=release_name, number=clone_from).demand(
                    RuntimeError(f"Revision {clone_from} does not exist")
                )
            else:
                old_revision = await interaction.latest_revision(release)
            if set_local_cache:
                release.check_cache_key = str(uuid.uuid4())
            if reset_to_global_cache:
                release.check_cache_key = None

        if clone_from is not None:
            old_release_dir = paths.release_directory_base(release) / clone_from
        else:
            old_release_dir = paths.release_directory(release)
        merge_enabled = clone_from is None

        # Create a temporary directory
        # We ensure, below, that it's removed on any exception
        # Use the tmp subdirectory of state, to ensure that it is on the same filesystem
        prefix_token = secrets.token_hex(16)
        temp_dir: str = await asyncio.to_thread(tempfile.mkdtemp, prefix=prefix_token + "-", dir=paths.get_tmp_dir())
        temp_dir_path = pathlib.Path(temp_dir)

        try:
            # The directory was created by mkdtemp, but it's empty
            if old_revision is not None:
                # If this is not the first revision, hard link the previous revision
                await util.create_hard_link_clone(old_release_dir, temp_dir_path, do_not_create_dest_dir=True)
            # The directory is either empty or its files are hard linked to the previous revision
            if modify is not None:
                await modify(temp_dir_path, old_revision)
        except types.FailedError:
            await aioshutil.rmtree(temp_dir)
            raise
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        validation_errors = await asyncio.to_thread(detection.validate_directory, temp_dir_path)
        if validation_errors:
            await aioshutil.rmtree(temp_dir)
            raise types.FailedError("File validation failed:\n" + "\n".join(validation_errors))

        # Ensure that the permissions of every directory are 755
        try:
            await asyncio.to_thread(util.chmod_directories, temp_dir_path)
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        # Make files read only to prevent them from being modified through hard links
        try:
            await asyncio.to_thread(util.chmod_files, temp_dir_path, 0o444)
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        try:
            path_to_hash, path_to_size = await attestable.paths_to_hashes_and_sizes(temp_dir_path)
            parent_revision_number = old_revision.number if old_revision else None
            previous_attestable = None
            if parent_revision_number is not None:
                previous_attestable = await attestable.load(project_name, version_name, parent_revision_number)
            base_inodes: dict[str, int] = {}
            base_hashes: dict[str, str] = {}
            if merge_enabled and (old_revision is not None):
                base_dir = old_release_dir
                base_inodes = await asyncio.to_thread(util.paths_to_inodes, base_dir)
                base_hashes = dict(previous_attestable.paths) if (previous_attestable is not None) else {}
            n_inodes = await asyncio.to_thread(util.paths_to_inodes, temp_dir_path)
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        async with SafeSession(temp_dir) as data:
            try:
                # TODO: Do we really need _release_name when we have release in this method?
                previous_attestable, prior_revision_name, merged_release = await _lock_and_merge(
                    data,
                    base_hashes=base_hashes,
                    base_inodes=base_inodes,
                    merge_enabled=merge_enabled,
                    n_inodes=n_inodes,
                    old_revision=old_revision,
                    path_to_hash=path_to_hash,
                    path_to_size=path_to_size,
                    previous_attestable=previous_attestable,
                    project_name=project_name,
                    release=release,
                    _release_name=release.safe_name,
                    temp_dir_path=temp_dir_path,
                    version_name=version_name,
                )
            except Exception:
                await aioshutil.rmtree(temp_dir)
                raise

            archive_paths = detection.detect_archives_requiring_quarantine(path_to_hash, previous_attestable)
            if archive_paths:
                deduped = detection.deduplicate_quarantine_archives(archive_paths, path_to_hash)
                if deduped:
                    return await self._quarantine_archives(
                        data,
                        asf_uid=asf_uid,
                        deduped_archives=deduped,
                        description=description,
                        path_to_size=path_to_size,
                        prior_revision_name=prior_revision_name,
                        project_name=project_name,
                        release_name=release_name,
                        temp_dir=temp_dir,
                        version_name=version_name,
                    )

            return await _commit_new_revision(
                data,
                asf_uid=asf_uid,
                description=description,
                path_to_hash=path_to_hash,
                path_to_size=path_to_size,
                previous_attestable=previous_attestable,
                project_name=project_name,
                release=merged_release,
                release_name=release_name,
                temp_dir=temp_dir,
                version_name=version_name,
            )

    async def _quarantine_archives(
        self,
        data: db.Session,
        *,
        asf_uid: str,
        deduped_archives: list[tuple[str, str]],
        description: str | None,
        path_to_size: dict[str, int],
        prior_revision_name: str | None,
        project_name: safe.ProjectName,
        release_name: str,
        temp_dir: str,
        version_name: safe.VersionName,
    ) -> sql.Quarantined:
        file_metadata = [
            sql.QuarantineFileEntryV1(
                rel_path=rel_path,
                size_bytes=path_to_size[rel_path],
                content_hash=content_hash,
                errors=[],
            )
            for rel_path, content_hash in deduped_archives
        ]

        token = _generate_quarantine_token()
        quarantined = sql.Quarantined(
            release_name=release_name,
            asf_uid=asf_uid,
            prior_revision_name=prior_revision_name,
            status=sql.QuarantineStatus.STAGING,
            token=token,
            created=datetime.datetime.now(datetime.UTC),
            file_metadata=file_metadata,
            description=description,
        )

        try:
            data.add(quarantined)
            await data.flush()

            quarantine_dir = paths.quarantine_directory(quarantined)
            await aiofiles.os.makedirs(quarantine_dir.parent, exist_ok=True)
            await aiofiles.os.rename(temp_dir, quarantine_dir)
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        # Release the write lock obtained in _lock_and_merge
        await data.commit()

        data.add(
            sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.QUARANTINE_VALIDATE,
                task_args={
                    "quarantined_id": quarantined.id,
                    "archives": [
                        {"rel_path": entry.rel_path, "content_hash": entry.content_hash} for entry in file_metadata
                    ],
                },
                asf_uid=asf_uid,
                project_name=str(project_name),
                version_name=str(version_name),
                revision_number=None,
                primary_rel_path=None,
            )
        )
        quarantined.status = sql.QuarantineStatus.PENDING
        await data.commit()

        return quarantined


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_name: str,
    ):
        super().__init__(write, write_as, data, committee_name)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name
