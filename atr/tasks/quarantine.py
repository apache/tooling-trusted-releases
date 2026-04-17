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

from __future__ import annotations

import asyncio
import datetime
import errno
import os
import pathlib
import shutil
import time
import uuid

import aiofiles.os
import aioshutil
import exarch

import atr.archives as archives
import atr.attestable as attestable
import atr.config as config
import atr.db as db
import atr.detection as detection
import atr.hashes as hashes
import atr.log as log
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage.writers.revision as revision
import atr.tasks.checks as checks
import atr.util as util


def backfill_archive_cache() -> list[tuple[str, safe.StatePath, float]]:
    done_file = _backfill_done_file()
    done_file_path = done_file.path
    if done_file_path.exists():
        return []

    unfinished_dir = paths.get_unfinished_dir()
    unfinished_dir_path = unfinished_dir.path
    if not unfinished_dir_path.is_dir():
        done_file_path.parent.mkdir(parents=True, exist_ok=True)
        done_file_path.touch()
        return []

    archives_dir = paths.get_archives_dir()
    staging_base = paths.get_tmp_dir()
    staging_base_path = staging_base.path
    staging_base_path.mkdir(parents=True, exist_ok=True)
    extraction_cfg = archives.extraction_config()
    seen_archive_keys: set[str] = set()
    results_list: list[tuple[str, safe.StatePath, float]] = []

    for project_dir in sorted(unfinished_dir_path.iterdir()):
        if not project_dir.is_dir():
            continue
        for version_dir in sorted(project_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            for revision_dir in sorted(version_dir.iterdir()):
                if not revision_dir.is_dir():
                    continue
                _backfill_revision(
                    safe.StatePath(revision_dir, unfinished_dir_path),
                    project_dir.name,
                    version_dir.name,
                    archives_dir,
                    staging_base,
                    extraction_cfg,
                    seen_archive_keys,
                    results_list,
                )

    done_file_path.parent.mkdir(parents=True, exist_ok=True)
    done_file_path.touch()
    return results_list


@checks.with_model(args.QuarantineValidate)
async def validate(task_args: args.QuarantineValidate) -> results.Results | None:
    async with db.session() as data:
        quarantined = await data.quarantined(id=task_args.quarantined_id, _release=True).get()

    if quarantined is None:
        log.error(f"Quarantined row {task_args.quarantined_id} not found")
        return None

    if quarantined.status != sql.QuarantineStatus.PENDING:
        log.error(f"Quarantined row {task_args.quarantined_id} is not PENDING")
        return None

    release = quarantined.release
    project_key = release.safe_project_key
    version_key = release.safe_version_key
    quarantine_dir = paths.quarantine_directory(quarantined)

    if not await aiofiles.os.path.isdir(quarantine_dir):
        await _mark_failed(quarantined, None, "Quarantine directory does not exist")
        return None

    file_entries = await _build_file_entries(task_args.archives, quarantine_dir)

    try:
        await _extract_archives(task_args.archives, quarantine_dir, project_key, version_key, file_entries)
    except Exception as exc:
        await _mark_failed(quarantined, file_entries, f"Archive extraction failed: {exc}")
        await aioshutil.rmtree(quarantine_dir)
        return None

    await _promote(quarantined, project_key, version_key, release.key, str(quarantine_dir))
    return None


def _backfill_done_file() -> safe.StatePath:
    return safe.StatePath(pathlib.Path(config.get().STATE_DIR)) / "cache" / "archive-backfill.done"


def _backfill_extract_archive(
    archive_path: safe.StatePath,
    archive_dir: safe.StatePath,
    staging_base: safe.StatePath,
    extraction_cfg: exarch.SecurityConfig,
    results_list: list[tuple[str, safe.StatePath, float]],
) -> None:
    try:
        elapsed = _extract_archive_to_dir(archive_path, archive_dir, staging_base, extraction_cfg)
        results_list.append((str(archive_path), archive_dir, elapsed))
    except Exception as exc:
        log.warning(f"Backfill: failed to extract {archive_path}: {exc}")


def _backfill_revision(
    revision_dir: safe.StatePath,
    project_key: str,
    version_key: str,
    archives_dir: safe.StatePath,
    staging_base: safe.StatePath,
    extraction_cfg: exarch.SecurityConfig,
    seen_archive_keys: set[str],
    results_list: list[tuple[str, safe.StatePath, float]],
) -> None:
    revision_dir_path = revision_dir.path
    archives_base = archives_dir / project_key / version_key
    for archive_path in sorted(revision_dir_path.rglob("*")):
        if not archive_path.is_file():
            continue
        if not detection.is_quarantine_archive(archive_path.name):
            continue
        content_hash = hashes.compute_file_hash_sync(archive_path)
        archive_key = hashes.filesystem_archives_key(content_hash)
        dedupe_key = f"{project_key}/{version_key}/{archive_key}"
        if dedupe_key in seen_archive_keys:
            continue
        seen_archive_keys.add(dedupe_key)
        archive_dir = archives_base / archive_key
        if archive_dir.path.is_dir():
            continue
        _backfill_extract_archive(
            safe.StatePath(archive_path, root=revision_dir_path),
            archive_dir,
            staging_base,
            extraction_cfg,
            results_list,
        )


async def _build_file_entries(
    archive_entries: list[args.QuarantineArchiveEntry], quarantine_dir: safe.StatePath
) -> list[sql.QuarantineFileEntryV1]:
    file_entries: list[sql.QuarantineFileEntryV1] = []
    for archive in archive_entries:
        archive_path = quarantine_dir / archive.rel_path
        stat = await aiofiles.os.stat(archive_path)
        entry = sql.QuarantineFileEntryV1(
            rel_path=archive.rel_path,
            size_bytes=stat.st_size,
            content_hash=archive.content_hash,
            errors=[],
        )
        file_entries.append(entry)
    return file_entries


def _extract_archive_to_dir(
    archive_path: safe.StatePath,
    archive_dir: safe.StatePath,
    staging_base: safe.StatePath,
    extraction_cfg: exarch.SecurityConfig,
) -> float:
    archive_dir_path = archive_dir.path
    staging_dir = staging_base / f"archive-extract-{uuid.uuid4().hex}"
    staging_dir_path = staging_dir.path
    try:
        staging_dir_path.mkdir(parents=False, exist_ok=False)
        start = time.monotonic()
        with archives.exarch_compatible_path(archive_path) as exarch_path:
            exarch.extract_archive(exarch_path, str(staging_dir), extraction_cfg)
        archive_dir_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(staging_dir, archive_dir)
        except OSError as err:
            if isinstance(err, FileExistsError) or (err.errno in {errno.EEXIST, errno.ENOTEMPTY}):
                shutil.rmtree(staging_dir, ignore_errors=True)
            else:
                raise
        _set_archive_permissions(archive_dir)
        return time.monotonic() - start
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


async def _extract_archives(
    archive_entries: list[args.QuarantineArchiveEntry],
    quarantine_dir: safe.StatePath,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_entries: list[sql.QuarantineFileEntryV1],
) -> None:
    archives_base = paths.get_archives_dir() / project_key / version_key
    staging_base = paths.get_tmp_dir()
    await aiofiles.os.makedirs(archives_base, exist_ok=True)
    await aiofiles.os.makedirs(staging_base, exist_ok=True)

    extraction_config = archives.extraction_config()

    for archive in archive_entries:
        archive_dir = archives_base / hashes.filesystem_archives_key(archive.content_hash)
        if await aiofiles.os.path.isdir(archive_dir):
            continue
        archive_path = quarantine_dir / archive.rel_path
        try:
            await asyncio.to_thread(
                _extract_archive_to_dir,
                archive_path,
                archive_dir,
                staging_base,
                extraction_config,
            )
        except Exception as exc:
            log.exception(f"Failed to extract archive {archive.rel_path}")
            for entry in file_entries:
                if entry.rel_path == archive.rel_path:
                    entry.errors.append(f"Extraction failed: {exc}")
                    break
            raise


async def _mark_failed(
    quarantined: sql.Quarantined,
    file_entries: list[sql.QuarantineFileEntryV1] | None,
    message: str | None = None,
) -> None:
    async with db.session() as data:
        managed = await data.merge(quarantined)
        managed.status = sql.QuarantineStatus.FAILED
        managed.completed = datetime.datetime.now(datetime.UTC)
        if file_entries is not None:
            managed.file_metadata = file_entries
        await data.commit()
    if message:
        log.error(f"Quarantine {quarantined.id} failed: {message}")
    else:
        log.error(f"Quarantine {quarantined.id} failed safety checks")


async def _promote(
    quarantined: sql.Quarantined,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    release_key: str,
    quarantine_dir: str,
) -> None:
    quarantine_dir_path = pathlib.Path(quarantine_dir)

    async with db.session() as data:
        release = await data.release(key=release_key, _release_policy=True, _project_release_policy=True).demand(
            RuntimeError(f"Release {release_key} not found during quarantine promotion")
        )

    path_to_hash, path_to_size = await attestable.paths_to_hashes_and_sizes(quarantine_dir_path)

    old_revision: sql.Revision | None = None
    if quarantined.prior_revision_key is not None:
        prior_number = quarantined.prior_revision_key.split()[-1]
        async with db.session() as data:
            old_revision = await data.revision(release_key=release_key, number=prior_number).get()

    previous_attestable = None
    if old_revision is not None:
        previous_attestable = await attestable.load(project_key, version_key, old_revision.safe_number)

    base_inodes: dict[str, int] = {}
    base_hashes: dict[str, str] = {}
    if old_revision is not None:
        old_release_dir = paths.release_directory_base(release) / old_revision.number
        base_inodes = await asyncio.to_thread(util.paths_to_inodes, old_release_dir)
        base_hashes = attestable.path_hashes(previous_attestable) if (previous_attestable is not None) else {}
    n_inodes = await asyncio.to_thread(util.paths_to_inodes, quarantine_dir_path)

    async with revision.SafeSession(quarantine_dir) as data:
        await revision.finalise_revision(
            data,
            asf_uid=quarantined.asf_uid,
            base_hashes=base_hashes,
            base_inodes=base_inodes,
            description=quarantined.description,
            merge_enabled=True,
            n_inodes=n_inodes,
            old_revision=old_revision,
            path_to_hash=path_to_hash,
            path_to_size=path_to_size,
            previous_attestable=previous_attestable,
            project_key=project_key,
            release=release,
            release_key=release.safe_key,
            temp_dir=quarantine_dir,
            temp_dir_path=quarantine_dir_path,
            version_key=version_key,
            was_quarantined=True,
        )

    async with db.session() as data:
        await data.delete(quarantined)
        await data.commit()


def _set_archive_permissions(archive_dir: os.PathLike) -> None:
    util.chmod_files(archive_dir, 0o444)
    util.chmod_directories(archive_dir, 0o555)
