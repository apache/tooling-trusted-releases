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
import re
import secrets
import tempfile
import uuid
from typing import TYPE_CHECKING, Final

import aiofiles.os
import aioshutil
import asfquart.base as base
import sqlmodel

import atr.attestable as attestable
import atr.db as db
import atr.db.interaction as interaction
import atr.detection as detection
import atr.merge as merge
import atr.models.attestable
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.tasks as tasks
import atr.util as util

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
    path_to_hash: dict[safe.RelPath, str],
    path_to_size: dict[safe.RelPath, int],
    previous_attestable: atr.models.attestable.Attestable | None,
    project_key: safe.ProjectKey,
    release: sql.Release,
    release_key: safe.ReleaseKey,
    temp_dir: str,
    temp_dir_path: pathlib.Path,
    version_key: safe.VersionKey,
    was_quarantined: bool = False,
    path_provenance: dict[safe.RelPath, atr.models.attestable.ProvenanceV2] | None = None,
    extracted_swhids: dict[str, str] | None = None,
) -> sql.Revision:
    try:
        previous_attestable, merge_base_revision_key, _, merged_release = await _lock_and_merge(
            data,
            base_hashes=base_hashes,
            base_inodes=base_inodes,
            merge_enabled=merge_enabled,
            n_inodes=n_inodes,
            old_revision=old_revision,
            path_to_hash=path_to_hash,
            path_to_size=path_to_size,
            previous_attestable=previous_attestable,
            project_key=project_key,
            release=release,
            _release_key=release_key,
            temp_dir_path=temp_dir_path,
            version_key=version_key,
        )
    except Exception:
        await aioshutil.rmtree(temp_dir)
        raise

    return await _commit_new_revision(
        data,
        asf_uid=asf_uid,
        description=description,
        merge_base_revision_key=merge_base_revision_key,
        path_to_hash=path_to_hash,
        path_to_size=path_to_size,
        previous_attestable=previous_attestable,
        project_key=project_key,
        release=merged_release,
        release_key=str(release_key),
        temp_dir=temp_dir,
        version_key=version_key,
        was_quarantined=was_quarantined,
        path_provenance=path_provenance,
        extracted_swhids=extracted_swhids,
    )


async def _commit_new_revision(
    data: db.Session,
    *,
    asf_uid: str,
    description: str | None,
    merge_base_revision_key: str | None,
    path_to_hash: dict[safe.RelPath, str],
    path_to_size: dict[safe.RelPath, int],
    previous_attestable: atr.models.attestable.Attestable | None,
    project_key: safe.ProjectKey,
    release: sql.Release,
    release_key: str,
    temp_dir: str,
    version_key: safe.VersionKey,
    was_quarantined: bool = False,
    path_provenance: dict[safe.RelPath, atr.models.attestable.ProvenanceV2] | None = None,
    extracted_swhids: dict[str, str] | None = None,
) -> sql.Revision:
    try:
        # This is the only place where models.Revision is constructed
        # That makes models.populate_revision_sequence_and_name safe against races
        # Because that event is called when data.add is called below
        # And we have a write lock at that point through the use of data.begin_immediate
        now = datetime.datetime.now(datetime.UTC)
        new_revision = sql.Revision(
            release_key=release_key,
            release=release,
            asfuid=asf_uid,
            created=now,
            phase=release.phase,
            description=description,
            merge_base_revision_key=merge_base_revision_key,
            was_quarantined=was_quarantined,
        )
        data.add(new_revision)
        if release.activity_at <= now:
            release.activity_at = now
            release.inactivity_notice_key = None

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
            raise datatypes.FailedError(f"Revision directory {new_revision_dir} already exists")

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
    await asyncio.to_thread(util.chmod_directories, new_revision_dir.path, 0o555)

    policy = release.release_policy or release.project.release_policy
    policy_dict = policy.model_dump() if policy else None

    archives_base = paths.get_archives_dir() / str(project_key) / str(version_key)
    classifications = await attestable.compute_classifications(
        path_to_hash, policy_dict, new_revision_dir, archives_base
    )
    swhid_dirs = attestable.compute_swhid_dirs(path_to_hash, previous_attestable, extracted_swhids)
    effective_provenance = attestable.effective_path_provenance(path_provenance, path_to_hash, previous_attestable)

    await attestable.write_files_data(
        project_key,
        version_key,
        new_revision.safe_number,
        policy_dict,
        asf_uid,
        previous_attestable,
        path_to_hash,
        path_to_size,
        new_revision_dir,
        classifications=classifications,
        effective_path_provenance=effective_provenance,
        swhid_dirs=swhid_dirs,
    )

    if attestable.can_write_file_state_rows(previous_attestable, new_revision.parent_key):
        for row in attestable.compute_file_state_rows(
            release_key,
            new_revision.seq,
            path_to_hash,
            classifications,
            previous_attestable,
            effective_path_provenance=effective_provenance,
        ):
            data.add(row)

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
            await tasks.draft_checks(asf_uid, project_key, version_key, new_revision.safe_number, caller_data=data)

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
    path_to_hash: dict[safe.RelPath, str],
    path_to_size: dict[safe.RelPath, int],
    previous_attestable: atr.models.attestable.Attestable | None,
    project_key: safe.ProjectKey,
    release: sql.Release,
    _release_key: safe.ReleaseKey,
    temp_dir_path: pathlib.Path,
    version_key: safe.VersionKey,
) -> tuple[atr.models.attestable.Attestable | None, str | None, str | None, sql.Release]:
    # Acquire the write lock
    # We need this write lock for moving the directory afterwards atomically
    # But it also helps to make models.populate_revision_sequence_and_name safe against races
    await data.begin_immediate()

    merged_release: sql.Release = await data.merge(release)
    await data.refresh(merged_release)
    latest = await interaction.latest_revision(merged_release, caller_data=data)
    prior_revision_key = latest.key if latest else None

    # Merge with the prior revision if there was an intervening change
    merge_base_revision_key: str | None = None
    if (
        merge_enabled
        and (old_revision is not None)
        and (latest is not None)
        and (prior_revision_key is not None)
        and (prior_revision_key != old_revision.key)
    ):
        merge_base_revision_key = prior_revision_key
        # This won't be None because prior_revision_key is not None here
        prior_number = latest.safe_number
        prior_dir = paths.release_directory_base(merged_release) / str(prior_number)
        await merge.merge(
            data,
            base_inodes,
            base_hashes,
            prior_dir.path,
            project_key,
            version_key,
            latest.seq,
            temp_dir_path,
            n_inodes,
            {str(k): v for k, v in path_to_hash.items()},
            {str(k): v for k, v in path_to_size.items()},
        )
        previous_attestable = await attestable.load(project_key, version_key, prior_number)

    return previous_attestable, merge_base_revision_key, prior_revision_key, merged_release


async def _verify_provenance_sources_unchanged(
    *,
    temp_dir_path: safe.StatePath,
    pre_merge_inodes: dict[str, int],
    path_provenance: dict[safe.RelPath, atr.models.attestable.ProvenanceV2],
) -> None:
    # When there's a revision merge, the provenance for SHA512 files may no longer be accurate
    # This function checks input file inodes to ensure that they match
    # If they don't match, we raise an error
    for generated_rel, provenance_entry in path_provenance.items():
        dependency_paths = [path for path in provenance_entry.metadata.get("source_paths", []) if isinstance(path, str)]
        if (signature_path := provenance_entry.metadata.get("signature_path")) is not None:
            if isinstance(signature_path, str) and (signature_path not in dependency_paths):
                dependency_paths.append(signature_path)
        for dependency_rel in dependency_paths:
            pre_merge_inode = pre_merge_inodes.get(dependency_rel)
            dependency_path_full = temp_dir_path.path / dependency_rel
            try:
                stat_result = await aiofiles.os.stat(dependency_path_full)
                post_merge_inode: int | None = stat_result.st_ino
            except FileNotFoundError:
                post_merge_inode = None
            if (pre_merge_inode is None) or (post_merge_inode != pre_merge_inode):
                raise datatypes.FailedError(
                    f"A concurrent revision replaced {dependency_rel}; please regenerate {generated_rel}."
                )


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
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def clear_quarantine(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        quarantined_id: int,
    ) -> None:
        release_key = sql.release_key(str(project_key), str(version_key))
        release = await self.__data.release(key=str(release_key)).demand(
            storage.AccessError(f"Release '{project_key!s} {version_key!s}' not found.", status=404)
        )
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        quarantined = await self.__data.quarantined(
            id=quarantined_id, release_key=release_key, status=sql.QuarantineStatus.FAILED
        ).get()
        if quarantined is None:
            raise RuntimeError("Quarantine failure not found or not in FAILED state")
        quarantined.status = sql.QuarantineStatus.ACKNOWLEDGED
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version_key=str(version_key),
            quarantined_id=quarantined_id,
        )

    async def create_revision_with_quarantine(  # noqa: C901
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        asf_uid: str,
        *,
        allowed_phases: frozenset[sql.ReleasePhase],
        description: str | None = None,
        set_local_cache: bool = False,
        reset_to_global_cache: bool = False,
        modify: Callable[
            [safe.StatePath, sql.Revision | None],
            Awaitable[dict[safe.RelPath, atr.models.attestable.ProvenanceV2] | None],
        ]
        | None = None,
        clone_from: safe.RevisionNumber | None = None,
    ) -> sql.Revision | sql.Quarantined:
        """Create a new revision, quarantining archives that require validation."""
        release_key = sql.release_key(str(project_key), str(version_key))
        async with db.session() as data:
            release = await data.release(key=release_key, _release_policy=True, _project_release_policy=True).demand(
                RuntimeError("Release does not exist for new revision creation")
            )
            storage.ensure_project_active(release.project)
            self.__write.ensure_release_writable(release)
            if release.phase not in allowed_phases:
                raise datatypes.PhaseMismatchError(
                    f"Cannot create revision: release phase is {release.phase.value}, "
                    f"allowed: {', '.join(sorted(p.value for p in allowed_phases))}"
                )
            if clone_from is not None:
                old_revision = await data.revision(release_key=release_key, number=str(clone_from)).demand(
                    RuntimeError(f"Revision {clone_from} does not exist")
                )
            else:
                old_revision = await interaction.latest_revision(release)

        if clone_from is not None:
            old_release_dir = paths.release_directory_base(release) / str(clone_from)
        else:
            old_release_dir = paths.release_directory(release)
        merge_enabled = clone_from is None

        # Create a temporary directory
        # We ensure, below, that it's removed on any exception
        # Use the tmp subdirectory of state, to ensure that it is on the same filesystem
        prefix_token = secrets.token_hex(16)
        temp_dir: str = await asyncio.to_thread(tempfile.mkdtemp, prefix=prefix_token + "-", dir=paths.get_tmp_dir())
        temp_dir_path = safe.StatePath(pathlib.Path(temp_dir), paths.get_tmp_dir().path)
        path_provenance: dict[safe.RelPath, atr.models.attestable.ProvenanceV2] | None = None

        try:
            # The directory was created by mkdtemp, but it's empty
            if old_revision is not None:
                # If this is not the first revision, hard link the previous revision
                await util.create_hard_link_clone(old_release_dir, temp_dir_path, do_not_create_dest_dir=True)
            # The directory is either empty or its files are hard linked to the previous revision
            if modify is not None:
                path_provenance = await modify(temp_dir_path, old_revision)
        except datatypes.FailedError:
            await aioshutil.rmtree(temp_dir)
            raise
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        validation_errors = await asyncio.to_thread(detection.validate_directory, temp_dir_path.path)
        if validation_errors:
            await aioshutil.rmtree(temp_dir)
            raise datatypes.FailedError("File validation failed:\n" + "\n".join(validation_errors))

        # Ensure that the permissions of every directory are 755
        try:
            await asyncio.to_thread(util.chmod_directories, temp_dir_path.path)
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        # Make files read only to prevent them from being modified through hard links
        try:
            await asyncio.to_thread(util.chmod_files, temp_dir_path.path, 0o444)
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        try:
            path_to_hash, path_to_size = await attestable.paths_to_hashes_and_sizes(temp_dir_path.path)
            parent_revision_number = old_revision.safe_number if old_revision else None
            previous_attestable = None
            if parent_revision_number is not None:
                previous_attestable = await attestable.load(project_key, version_key, parent_revision_number)
            base_inodes: dict[str, int] = {}
            base_hashes: dict[str, str] = {}
            if merge_enabled and (old_revision is not None):
                base_dir = old_release_dir
                base_inodes = await asyncio.to_thread(util.paths_to_inodes, base_dir.path)
                base_hashes = attestable.path_hashes(previous_attestable) if (previous_attestable is not None) else {}
            n_inodes = await asyncio.to_thread(util.paths_to_inodes, temp_dir_path.path)
        except Exception:
            await aioshutil.rmtree(temp_dir)
            raise

        async with SafeSession(temp_dir) as data:
            try:
                (
                    previous_attestable,
                    merge_base_revision_key,
                    prior_revision_key,
                    merged_release,
                ) = await _lock_and_merge(
                    data,
                    base_hashes=base_hashes,
                    base_inodes=base_inodes,
                    merge_enabled=merge_enabled,
                    n_inodes=n_inodes,
                    old_revision=old_revision,
                    path_to_hash=path_to_hash,
                    path_to_size=path_to_size,
                    previous_attestable=previous_attestable,
                    project_key=project_key,
                    release=release,
                    _release_key=release.safe_key,
                    temp_dir_path=temp_dir_path.path,
                    version_key=version_key,
                )
            except Exception:
                await aioshutil.rmtree(temp_dir)
                raise

            if merged_release.phase not in allowed_phases:
                await aioshutil.rmtree(temp_dir)
                raise datatypes.PhaseMismatchError(
                    f"Cannot create revision: release phase is {merged_release.phase.value}, "
                    f"allowed: {', '.join(sorted(p.value for p in allowed_phases))}"
                )

            if (merge_base_revision_key is not None) and path_provenance:
                try:
                    await _verify_provenance_sources_unchanged(
                        temp_dir_path=temp_dir_path,
                        pre_merge_inodes=n_inodes,
                        path_provenance=path_provenance,
                    )
                except Exception:
                    await aioshutil.rmtree(temp_dir)
                    raise

            if set_local_cache:
                merged_release.check_cache_key = str(uuid.uuid4())
            if reset_to_global_cache:
                merged_release.check_cache_key = None

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
                        prior_revision_key=prior_revision_key,
                        project_key=project_key,
                        release_key=release_key,
                        temp_dir=temp_dir,
                        version_key=version_key,
                    )

            return await _commit_new_revision(
                data,
                asf_uid=asf_uid,
                description=description,
                merge_base_revision_key=merge_base_revision_key,
                path_to_hash=path_to_hash,
                path_to_size=path_to_size,
                previous_attestable=previous_attestable,
                project_key=project_key,
                release=merged_release,
                release_key=release_key,
                temp_dir=temp_dir,
                version_key=version_key,
                path_provenance=path_provenance,
            )

    async def set_tag(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: str,
        tag: str,
    ) -> None:
        tag = tag.strip()
        if not tag:
            raise storage.AccessError("Tag is required", status=400)
        if re.match(r"^[a-zA-Z0-9+_.-]+$", tag) is None:
            raise storage.AccessError(
                "Tag must contain only letters, numbers, plus, underscore, dot, or hyphen", status=400
            )
        if len(tag.encode("utf-8")) > 256:
            raise storage.AccessError("Tag must be at most 256 bytes", status=400)

        release_key = str(sql.release_key(project_key, version_key))
        release = await self.__data.release(release_key).demand(storage.AccessError("No release found"))
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        via = sql.validate_instrumented_attribute
        stmt = (
            sqlmodel.update(sql.Revision)
            .where(
                via(sql.Revision.release_key) == release_key,
                via(sql.Revision.number) == revision_number,
                via(sql.Revision.tag).is_(None),
            )
            .values(tag=tag)
        )
        result = await self.__data.execute(stmt)
        if getattr(result, "rowcount", 0) != 1:
            await self.__data.rollback()
            revision = await self.__data.revision(release_key=release_key, number=revision_number).get()
            if revision is None:
                raise base.ASFQuartException(f"Revision {revision_number} not found", errorcode=404)
            raise storage.AccessError(f"Revision {revision_number} already has a tag and cannot be changed", status=409)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version_key=str(version_key),
            revision_number=revision_number,
            tag=tag,
        )

    async def _quarantine_archives(
        self,
        data: db.Session,
        *,
        asf_uid: str,
        deduped_archives: list[tuple[safe.RelPath, str]],
        description: str | None,
        path_to_size: dict[safe.RelPath, int],
        prior_revision_key: str | None,
        project_key: safe.ProjectKey,
        release_key: str,
        temp_dir: str,
        version_key: safe.VersionKey,
    ) -> sql.Quarantined:
        file_metadata = [
            sql.QuarantineFileEntryV1(
                rel_path=str(rel_path),
                size_bytes=path_to_size[rel_path],
                content_hash=content_hash,
                errors=[],
            )
            for rel_path, content_hash in deduped_archives
        ]

        token = _generate_quarantine_token()
        quarantined = sql.Quarantined(
            release_key=release_key,
            asf_uid=asf_uid,
            prior_revision_key=prior_revision_key,
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
                project_key=str(project_key),
                version_key=str(version_key),
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
        committee_key: str,
    ):
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key
