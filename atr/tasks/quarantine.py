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

import atr.attestable as attestable
import atr.config as config
import atr.db as db
import atr.detection as detection
import atr.hashes as hashes
import atr.log as log
import atr.models.results as results
import atr.models.safe as safe
import atr.models.schema as schema
import atr.models.sql as sql
import atr.paths as paths
import atr.storage.writers.revision as revision
import atr.tarzip as tarzip
import atr.tasks.checks as checks
import atr.util as util


class QuarantineArchiveEntry(schema.Strict):
    rel_path: str
    content_hash: str


class QuarantineValidate(schema.Strict):
    quarantined_id: int
    archives: list[QuarantineArchiveEntry]


def backfill_archive_cache() -> list[tuple[str, pathlib.Path, float]]:
    done_file = _backfill_done_file()
    if done_file.exists():
        return []

    unfinished_dir = paths.get_unfinished_dir()
    if not unfinished_dir.is_dir():
        done_file.parent.mkdir(parents=True, exist_ok=True)
        done_file.touch()
        return []

    cache_archives_dir = paths.get_cache_archives_dir()
    staging_base = paths.get_tmp_dir()
    staging_base.mkdir(parents=True, exist_ok=True)
    extraction_cfg = _extraction_config()
    seen_cache_keys: set[str] = set()
    results_list: list[tuple[str, pathlib.Path, float]] = []

    for project_dir in sorted(unfinished_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for version_dir in sorted(project_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            for revision_dir in sorted(version_dir.iterdir()):
                if not revision_dir.is_dir():
                    continue
                _backfill_revision(
                    revision_dir,
                    project_dir.name,
                    version_dir.name,
                    cache_archives_dir,
                    staging_base,
                    extraction_cfg,
                    seen_cache_keys,
                    results_list,
                )

    done_file.parent.mkdir(parents=True, exist_ok=True)
    done_file.touch()
    return results_list


@checks.with_model(QuarantineValidate)
async def validate(args: QuarantineValidate) -> results.Results | None:
    async with db.session() as data:
        quarantined = await data.quarantined(id=args.quarantined_id, _release=True).get()

    if quarantined is None:
        log.error(f"Quarantined row {args.quarantined_id} not found")
        return None

    if quarantined.status != sql.QuarantineStatus.PENDING:
        log.error(f"Quarantined row {args.quarantined_id} is not PENDING")
        return None

    release = quarantined.release
    project_name = release.safe_project_name
    version_name = release.safe_version_name
    quarantine_dir = paths.quarantine_directory(quarantined)

    if not await aiofiles.os.path.isdir(quarantine_dir):
        await _mark_failed(quarantined, None, "Quarantine directory does not exist")
        return None

    file_entries, any_failed = await _run_safety_checks(args.archives, quarantine_dir)

    if any_failed:
        await _mark_failed(quarantined, file_entries)
        await aioshutil.rmtree(quarantine_dir)
        return None

    try:
        await _extract_archives_to_cache(
            args.archives, quarantine_dir, str(project_name), str(version_name), file_entries
        )
    except Exception as exc:
        await _mark_failed(quarantined, file_entries, f"Archive extraction to cache failed: {exc}")
        await aioshutil.rmtree(quarantine_dir)
        return None

    await _promote(quarantined, project_name, version_name, release.name, str(quarantine_dir))
    return None


def _backfill_done_file() -> pathlib.Path:
    return paths.get_cache_archives_dir().parent / "archive-backfill.done"


def _backfill_extract_archive(
    archive_path: pathlib.Path,
    cache_dir: pathlib.Path,
    staging_base: pathlib.Path,
    extraction_cfg: exarch.SecurityConfig,
    results_list: list[tuple[str, pathlib.Path, float]],
) -> None:
    try:
        elapsed = _extract_archive_to_cache_dir(archive_path, cache_dir, staging_base, extraction_cfg)
        results_list.append((str(archive_path), cache_dir, elapsed))
    except Exception as exc:
        log.warning(f"Backfill: failed to extract {archive_path}: {exc}")


def _backfill_revision(
    revision_dir: pathlib.Path,
    project_name: str,
    version_name: str,
    cache_archives_dir: pathlib.Path,
    staging_base: pathlib.Path,
    extraction_cfg: exarch.SecurityConfig,
    seen_cache_keys: set[str],
    results_list: list[tuple[str, pathlib.Path, float]],
) -> None:
    cache_base = cache_archives_dir / project_name / version_name
    for archive_path in sorted(revision_dir.rglob("*")):
        if not archive_path.is_file():
            continue
        if not _is_archive_suffix(archive_path.name):
            continue
        content_hash = hashes.compute_file_hash_sync(archive_path)
        cache_key = hashes.filesystem_cache_archives_key(content_hash)
        dedupe_key = f"{project_name}/{version_name}/{cache_key}"
        if dedupe_key in seen_cache_keys:
            continue
        seen_cache_keys.add(dedupe_key)
        cache_dir = cache_base / cache_key
        if cache_dir.is_dir():
            continue
        _backfill_extract_archive(archive_path, cache_dir, staging_base, extraction_cfg, results_list)


def _extract_archive_to_cache_dir(
    archive_path: pathlib.Path,
    cache_dir: pathlib.Path,
    staging_base: pathlib.Path,
    extraction_cfg: exarch.SecurityConfig,
) -> float:
    staging_dir = staging_base / f"archive-extract-{uuid.uuid4().hex}"
    try:
        staging_dir.mkdir(parents=False, exist_ok=False)
        start = time.monotonic()
        exarch.extract_archive(str(archive_path), str(staging_dir), extraction_cfg)
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(staging_dir, cache_dir)
        except OSError as err:
            if isinstance(err, FileExistsError) or err.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                shutil.rmtree(staging_dir, ignore_errors=True)
            else:
                raise
        return time.monotonic() - start
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


async def _extract_archives_to_cache(
    archives: list[QuarantineArchiveEntry],
    quarantine_dir: pathlib.Path,
    project_name: str,
    version_name: str,
    file_entries: list[sql.QuarantineFileEntryV1],
) -> None:
    cache_base = paths.get_cache_archives_dir() / project_name / version_name
    staging_base = paths.get_tmp_dir()
    await aiofiles.os.makedirs(cache_base, exist_ok=True)
    await aiofiles.os.makedirs(staging_base, exist_ok=True)

    extraction_config = _extraction_config()

    for archive in archives:
        cache_dir = cache_base / hashes.filesystem_cache_archives_key(archive.content_hash)
        if await aiofiles.os.path.isdir(cache_dir):
            continue
        archive_path = quarantine_dir / archive.rel_path
        try:
            await asyncio.to_thread(
                _extract_archive_to_cache_dir,
                archive_path,
                cache_dir,
                staging_base,
                extraction_config,
            )
        except Exception as exc:
            log.exception(f"Failed to extract archive {archive.rel_path} to cache")
            for entry in file_entries:
                if entry.rel_path == archive.rel_path:
                    entry.errors.append(f"Extraction failed: {exc}")
                    break
            raise


def _extraction_config() -> exarch.SecurityConfig:
    conf = config.get()
    extraction_config = (
        exarch.SecurityConfig()
        .max_file_size(conf.MAX_EXTRACT_SIZE)
        .max_total_size(conf.MAX_EXTRACT_SIZE)
        .max_file_count(tarzip.MAX_ARCHIVE_MEMBERS)
        .max_compression_ratio(100.0)
        .max_path_depth(32)
        # Escaping the root is still disallowed by exarch even when symlinks are allowed
        .allow_symlinks(True)
        .allow_hardlinks(False)
        .allow_absolute_paths(False)
        # Too many archives use this for us to disallow it
        # We could set to 0o444 after extraction anyway
        .allow_world_writable(True)
    )
    banned_path_components = extraction_config.banned_path_components  # pyright: ignore[reportAttributeAccessIssue]
    extraction_config.banned_path_components = [  # pyright: ignore[reportAttributeAccessIssue]
        component for component in banned_path_components if component.lower() != ".env"
    ]
    return extraction_config


def _is_archive_suffix(filename: str) -> bool:
    lower_name = filename.lower()
    return any(lower_name.endswith(suffix) for suffix in detection.QUARANTINE_ARCHIVE_SUFFIXES)


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
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    release_name: str,
    quarantine_dir: str,
) -> None:
    quarantine_dir_path = pathlib.Path(quarantine_dir)

    async with db.session() as data:
        release = await data.release(name=release_name, _release_policy=True, _project_release_policy=True).demand(
            RuntimeError(f"Release {release_name} not found during quarantine promotion")
        )

    path_to_hash, path_to_size = await attestable.paths_to_hashes_and_sizes(quarantine_dir_path)

    old_revision: sql.Revision | None = None
    if quarantined.prior_revision_name is not None:
        prior_number = quarantined.prior_revision_name.split()[-1]
        async with db.session() as data:
            old_revision = await data.revision(release_name=release_name, number=prior_number).get()

    previous_attestable = None
    if old_revision is not None:
        previous_attestable = await attestable.load(project_name, version_name, old_revision.number)

    base_inodes: dict[str, int] = {}
    base_hashes: dict[str, str] = {}
    if old_revision is not None:
        old_release_dir = paths.release_directory_base(release) / old_revision.number
        base_inodes = await asyncio.to_thread(util.paths_to_inodes, old_release_dir)
        base_hashes = dict(previous_attestable.paths) if (previous_attestable is not None) else {}
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
            project_name=project_name,
            release=release,
            release_name=release.safe_name,
            temp_dir=quarantine_dir,
            temp_dir_path=quarantine_dir_path,
            version_name=version_name,
            was_quarantined=True,
        )

    async with db.session() as data:
        await data.delete(quarantined)
        await data.commit()


async def _run_safety_checks(
    archives: list[QuarantineArchiveEntry], quarantine_dir: pathlib.Path
) -> tuple[list[sql.QuarantineFileEntryV1], bool]:
    file_entries: list[sql.QuarantineFileEntryV1] = []
    any_failed = False
    for archive in archives:
        archive_path = str(quarantine_dir / archive.rel_path)
        stat = await aiofiles.os.stat(archive_path)
        errors = await asyncio.to_thread(detection.check_archive_safety, archive_path)
        entry = sql.QuarantineFileEntryV1(
            rel_path=archive.rel_path,
            size_bytes=stat.st_size,
            content_hash=archive.content_hash,
            errors=errors,
        )
        file_entries.append(entry)
        if errors:
            any_failed = True
    return file_entries, any_failed
