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
import json
import os
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable

import aiofiles
import aiofiles.os
import pydantic

import atr.classify as classify
import atr.hashes as hashes
import atr.log as log
import atr.models.attestable as models
import atr.models.github as github
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.util as util

if TYPE_CHECKING:
    import pathlib

_READONLY_PERMISSIONS: Final[int] = 0o444


def attestable_checks_path(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> safe.StatePath:
    return paths.get_attestable_dir() / str(project_key) / str(version_key) / f"{revision_number!s}.checks.json"


def attestable_path(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> safe.StatePath:
    return paths.get_attestable_dir() / str(project_key) / str(version_key) / f"{revision_number!s}.json"


def can_write_file_state_rows(
    previous: models.Attestable | None,
    parent_name: str | None,
) -> bool:
    is_first_revision = (previous is None) and (parent_name is None)
    is_v2_continuation = isinstance(previous, models.AttestableV2)
    return is_first_revision or is_v2_continuation


async def compute_classifications(
    path_to_hash: dict[safe.RelPath, str],
    release_policy: dict[str, Any] | None,
    base_path: safe.StatePath,
    archives_base: safe.StatePath | None = None,
) -> dict[safe.RelPath, str]:
    policy = release_policy or {}
    source_matcher, binary_matcher = classify.matchers_from_policy(
        policy.get("source_artifact_paths", []),
        policy.get("binary_artifact_paths", []),
        base_path,
    )
    classifications: dict[safe.RelPath, str] = {}
    for path_key in path_to_hash:
        archive_cache_dir = (
            archives_base / hashes.filesystem_archives_key(path_to_hash[path_key])
            if (archives_base is not None)
            else None
        )
        file_type = await classify.classify(
            path_key,
            base_path=base_path,
            source_matcher=source_matcher,
            binary_matcher=binary_matcher,
            archive_cache_dir=archive_cache_dir,
        )
        classifications[path_key] = file_type.value
    return classifications


def compute_file_state_rows(
    release_key: str,
    since_revision_seq: int,
    path_to_hash: dict[safe.RelPath, str],
    classifications: dict[safe.RelPath, str],
    previous: models.Attestable | None,
    effective_path_provenance: dict[str, models.ProvenanceV2] | None = None,
) -> list[sql.ReleaseFileState]:
    # This function is called when committing a new revision
    # It's called after writing attestable data to attestable files
    # Now we add some of that same data to ReleaseFileState rows
    # We then return those rows for actually adding to the database
    prev_hashes: dict[str, str] = {}
    prev_classifications: dict[str, str] = {}
    prev_provenance: dict[str, models.ProvenanceV2 | None] = {}
    if previous is not None:
        prev_hashes = path_hashes(previous)
        if isinstance(previous, models.AttestableV2):
            prev_classifications = {path_key: entry.classification for path_key, entry in previous.paths.items()}
            prev_provenance = {path_key: entry.provenance for path_key, entry in previous.paths.items()}

    effective = effective_path_provenance or {}
    rows: list[sql.ReleaseFileState] = []

    # ReleaseFileState only reflects attestable path state, i.e. omitting hashes and policy
    # Also, we only record new rows when the metadata changes
    # But in attestable files, we record the data always, for each revision
    for path_key in sorted(path_to_hash, key=str):
        path_str = str(path_key)
        content_hash = path_to_hash[path_key]
        classification = classifications[path_key]
        provenance_entry = effective.get(path_str)
        provenance_payload = provenance_entry.model_dump(mode="json") if (provenance_entry is not None) else None
        prev_provenance_entry = prev_provenance.get(path_str)
        # If all prior metadata properties are the same, we skip recording an event
        if (
            (prev_hashes.get(path_str) == content_hash)
            and (prev_classifications.get(path_str) == classification)
            and (prev_provenance_entry == provenance_entry)
        ):
            continue
        rows.append(
            sql.ReleaseFileState(
                release_key=release_key,
                path=path_str,
                since_revision_seq=since_revision_seq,
                present=True,
                content_hash=content_hash,
                classification=classification,
                provenance=provenance_payload,
            )
        )
    str_keys = {str(k) for k in path_to_hash}
    for path_key in sorted(prev_hashes):
        if path_key not in str_keys:
            rows.append(
                sql.ReleaseFileState(
                    release_key=release_key,
                    path=path_key,
                    since_revision_seq=since_revision_seq,
                    present=False,
                    content_hash=None,
                    classification=None,
                    provenance=None,
                )
            )

    return rows


def compute_swhid_dirs(
    path_to_hash: dict[safe.RelPath, str],
    previous: models.Attestable | None,
    extracted: dict[str, str] | None = None,
) -> dict[str, str]:
    extracted = extracted or {}
    carried: dict[str, str] = {}
    if isinstance(previous, models.AttestableV2):
        for hash_ref, entry in previous.hashes.items():
            if entry.swhid_dir_inner is not None:
                carried[hash_ref] = entry.swhid_dir_inner
    result: dict[str, str] = {}
    for content_hash in path_to_hash.values():
        swhid_dir_inner = extracted.get(content_hash)
        if swhid_dir_inner is None:
            swhid_dir_inner = carried.get(content_hash)
        if swhid_dir_inner is not None:
            result[content_hash] = swhid_dir_inner
    return result


def cross_format_siblings(attestable: models.Attestable, primary_rel_path: str) -> dict[str, str | None]:
    stem = util.archive_format_stem(os.path.basename(primary_rel_path))
    if stem is None:
        return {}
    primary_dir = os.path.dirname(primary_rel_path)
    siblings: dict[str, str | None] = {}
    for path_key in path_hashes(attestable):
        if path_key == primary_rel_path:
            continue
        if os.path.dirname(path_key) != primary_dir:
            continue
        if util.archive_format_stem(os.path.basename(path_key)) != stem:
            continue
        siblings[path_key] = path_swhid_dir(attestable, path_key)
    return siblings


def effective_path_provenance(
    path_provenance: dict[safe.RelPath, models.ProvenanceV2] | None,
    path_to_hash: dict[safe.RelPath, str],
    previous: models.Attestable | None,
) -> dict[str, models.ProvenanceV2]:
    result: dict[str, models.ProvenanceV2] = {}
    caller: dict[str, models.ProvenanceV2] = {}
    if path_provenance is not None:
        caller = {str(path_key): value for path_key, value in path_provenance.items()}
    prev_v2: models.AttestableV2 | None = previous if isinstance(previous, models.AttestableV2) else None
    for path_key, content_hash in path_to_hash.items():
        path_str = str(path_key)
        # Use the provenance from the caller, if provided
        if (caller_entry := caller.get(path_str)) is not None:
            result[path_str] = caller_entry
            continue
        if prev_v2 is None:
            continue
        prev_entry = prev_v2.paths.get(path_str)
        # Skip the path if it is not in the previous attestable data
        if prev_entry is None:
            continue
        if prev_entry.content_hash != content_hash:
            continue
        if prev_entry.provenance is None:
            continue
        # Otherwise, we use the provenance from the previous attestable
        result[path_str] = prev_entry.provenance
    return result


def github_tp_payload_path(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> safe.StatePath:
    return paths.get_attestable_dir() / str(project_key) / str(version_key) / f"{revision_number!s}.github-tp.json"


async def github_tp_payload_read(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> github.TrustedPublisherPayload | None:
    payload_path = github_tp_payload_path(project_key, version_key, revision_number)
    if not await aiofiles.os.path.isfile(payload_path):
        return None
    try:
        async with aiofiles.open(payload_path, encoding="utf-8") as f:
            data = json.loads(await f.read())
        if not isinstance(data, dict):
            log.warning(f"TP payload was not a JSON object in {payload_path}")
            return None
        # Remove exp and nbf if they're stored - as of 2026-03-18 they're validated and then removed before storage
        # but we might have older data
        if "exp" in data:
            del data["exp"]
        if "nbf" in data:
            del data["nbf"]
        return github.TrustedPublisherPayload.model_validate(data)
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"Failed to read TP payload from {payload_path}: {e}")
        return None
    except pydantic.ValidationError as e:
        log.warning(f"Failed to validate TP payload from {payload_path}: {e}")
        return None


async def github_tp_payload_write(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    github_payload: github.TrustedPublisherPayload,
) -> None:
    payload_path = github_tp_payload_path(project_key, version_key, revision_number)
    # Dump the workflow payload, excluding exp and nbf - which shouldn't have made it this far. If they do,
    # it's safe to remove them as they've been validated by the model already, and we should never store
    # stale dates
    await _atomic_write_readonly(
        payload_path.path, json.dumps(github_payload.model_dump(exclude={"exp", "nbf"}), indent=2)
    )


async def load(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
) -> models.Attestable | None:
    file_path = attestable_path(project_key, version_key, revision_number)
    if not await aiofiles.os.path.isfile(file_path):
        return None
    try:
        async with aiofiles.open(file_path, encoding="utf-8") as f:
            content = await f.read()
        return _parse_attestable(content)
    except (json.JSONDecodeError, pydantic.ValidationError) as e:
        log.warning(f"Could not parse {file_path}, starting fresh: {e}")
        return None


async def load_checks(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
) -> dict[str, dict[str, str]]:
    file_path = attestable_checks_path(project_key, version_key, revision_number)
    # TODO: Once we're sure everyone is on V2, we should be strict about failures here,
    # rather than returning {}
    if await aiofiles.os.path.isfile(file_path):
        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                content = await f.read()
            if json.loads(content).get("version") == 1:
                log.warning(f"Found old checks file format in {file_path}, ignoring old checks")
                return {}
            return models.AttestableChecksV2.model_validate_json(content).checks
        except (json.JSONDecodeError, pydantic.ValidationError) as e:
            log.warning(f"Could not parse {file_path}: {e}")
            return {}
    return {}


def path_classification(attestable: models.Attestable, path_key: str) -> str | None:
    if isinstance(attestable, models.AttestableV2):
        entry = attestable.paths.get(path_key)
        return entry.classification if (entry is not None) else None
    return None


def path_hash(attestable: models.Attestable, path_key: str) -> str | None:
    if isinstance(attestable, models.AttestableV2):
        entry = attestable.paths.get(path_key)
        return entry.content_hash if (entry is not None) else None
    return attestable.paths.get(path_key)


def path_hashes(attestable: models.Attestable) -> dict[str, str]:
    if isinstance(attestable, models.AttestableV2):
        return {path_key: entry.content_hash for path_key, entry in attestable.paths.items()}
    return dict(attestable.paths)


def path_provenance(attestable: models.Attestable, path_key: str) -> models.ProvenanceV2 | None:
    if isinstance(attestable, models.AttestableV2):
        entry = attestable.paths.get(path_key)
        return entry.provenance if (entry is not None) else None
    return None


def path_swhid_dir(attestable: models.Attestable, path_key: str) -> str | None:
    if not isinstance(attestable, models.AttestableV2):
        return None
    entry = attestable.paths.get(path_key)
    if entry is None:
        return None
    hash_entry = attestable.hashes.get(entry.content_hash)
    return hash_entry.swhid_dir_inner if (hash_entry is not None) else None


async def paths_to_hashes_and_sizes(directory: pathlib.Path) -> tuple[dict[safe.RelPath, str], dict[safe.RelPath, int]]:
    path_to_hash: dict[safe.RelPath, str] = {}
    path_to_size: dict[safe.RelPath, int] = {}
    async for rel_path in util.paths_recursive(directory):
        full_path = directory / rel_path
        if "\\" in str(rel_path):
            # TODO: We should centralise this, and forbid some other characters too
            raise ValueError(f"Backslash in path is forbidden: {rel_path!s}")
        path_to_hash[rel_path] = await hashes.compute_file_hash(full_path)
        path_to_size[rel_path] = (await aiofiles.os.stat(full_path)).st_size
    return path_to_hash, path_to_size


async def write_checks_data(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    rel_path: str,
    checks: dict[str, str],
) -> None:
    log.info(f"Writing checks for {project_key}/{version_key}/{revision_number}/{rel_path}: {checks}")

    def modify(content: str) -> str:
        try:
            current = models.AttestableChecksV2.model_validate_json(content).checks
        except pydantic.ValidationError:
            current = {}
        if rel_path not in current:
            current[rel_path] = checks
        else:
            current[rel_path].update(checks)
        result = models.AttestableChecksV2(checks=current)
        return result.model_dump_json(indent=2)

    await _atomic_modify_readonly(attestable_checks_path(project_key, version_key, revision_number).path, modify)


async def write_files_data(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    release_policy: dict[str, Any] | None,
    uploader_uid: str,
    previous: models.Attestable | None,
    path_to_hash: dict[safe.RelPath, str],
    path_to_size: dict[safe.RelPath, int],
    base_path: safe.StatePath,
    classifications: dict[safe.RelPath, str] | None = None,
    effective_path_provenance: dict[str, models.ProvenanceV2] | None = None,
    swhid_dirs: dict[str, str] | None = None,
) -> None:
    result = await _generate_files_data(
        path_to_hash,
        path_to_size,
        revision_number,
        release_policy,
        uploader_uid,
        previous,
        base_path,
        classifications=classifications,
        effective_path_provenance=effective_path_provenance,
        swhid_dirs=swhid_dirs,
    )
    file_path = attestable_path(project_key, version_key, revision_number)
    await _atomic_write_readonly(file_path.path, result.model_dump_json(indent=2))
    checks_file_path = attestable_checks_path(project_key, version_key, revision_number)
    if not checks_file_path.path.exists():
        await _atomic_write_readonly(checks_file_path.path, models.AttestableChecksV2().model_dump_json(indent=2))


async def _atomic_modify_readonly(file_path: pathlib.Path, modify: Callable[[str], str]) -> None:
    await util.atomic_modify_file(file_path, modify)
    await asyncio.to_thread(os.chmod, file_path, _READONLY_PERMISSIONS)


async def _atomic_write_readonly(file_path: pathlib.Path, content: str) -> None:
    await util.atomic_write_file(file_path, content)
    await asyncio.to_thread(os.chmod, file_path, _READONLY_PERMISSIONS)


def _compute_hashes_with_attribution(  # noqa: C901
    current_hash_to_paths: dict[str, set[safe.RelPath]],
    path_to_size: dict[safe.RelPath, int],
    previous: models.Attestable | None,
    uploader_uid: str,
    revision_number: safe.RevisionNumber,
) -> dict[str, models.HashEntryV2]:
    previous_hash_to_paths: dict[str, set[str]] = {}
    if previous is not None:
        for path_key, hash_ref in path_hashes(previous).items():
            previous_hash_to_paths.setdefault(hash_ref, set()).add(path_key)

    new_hashes: dict[str, models.HashEntryV2] = {}
    if previous is not None:
        for hash_key, hash_entry in previous.hashes.items():
            new_hashes[hash_key] = models.HashEntryV2.model_validate(hash_entry.model_dump())

    for hash_ref, current_paths in current_hash_to_paths.items():
        previous_paths = previous_hash_to_paths.get(hash_ref, set())
        sample_path = next(iter(current_paths))
        file_size = path_to_size[sample_path]
        current_basenames = {_path_basename(str(path_key)) for path_key in current_paths}

        if hash_ref not in new_hashes:
            new_hashes[hash_ref] = models.HashEntryV2(
                size=file_size,
                uploaders=[(uploader_uid, str(revision_number))],
                basenames=sorted(current_basenames),
            )
            continue

        existing_basenames = set(new_hashes[hash_ref].basenames)
        for basename in sorted(current_basenames):
            if basename not in existing_basenames:
                new_hashes[hash_ref].basenames.append(basename)
                existing_basenames.add(basename)

        if len(current_paths) > len(previous_paths):
            existing_entries = set(new_hashes[hash_ref].uploaders)
            if (uploader_uid, str(revision_number)) not in existing_entries:
                new_hashes[hash_ref].uploaders.append((uploader_uid, str(revision_number)))

    return new_hashes


async def _generate_files_data(
    path_to_hash: dict[safe.RelPath, str],
    path_to_size: dict[safe.RelPath, int],
    revision_number: safe.RevisionNumber,
    release_policy: dict[str, Any] | None,
    uploader_uid: str,
    previous: models.Attestable | None,
    base_path: safe.StatePath,
    classifications: dict[safe.RelPath, str] | None = None,
    effective_path_provenance: dict[str, models.ProvenanceV2] | None = None,
    swhid_dirs: dict[str, str] | None = None,
) -> models.AttestableV2:
    current_hash_to_paths: dict[str, set[safe.RelPath]] = {}
    for path_key, hash_ref in path_to_hash.items():
        current_hash_to_paths.setdefault(hash_ref, set()).add(path_key)

    new_hashes = _compute_hashes_with_attribution(
        current_hash_to_paths, path_to_size, previous, uploader_uid, revision_number
    )
    for hash_ref, swhid_dir_inner in (swhid_dirs or {}).items():
        if (entry := new_hashes.get(hash_ref)) is not None:
            entry.swhid_dir_inner = swhid_dir_inner

    if classifications is None:
        classifications = await compute_classifications(path_to_hash, release_policy, base_path)
    provenance_map = effective_path_provenance or {}
    return models.AttestableV2(
        hashes=dict(new_hashes),
        paths={
            str(path_key): models.PathEntryV2(
                content_hash=hash_ref,
                classification=classifications[path_key],
                provenance=provenance_map.get(str(path_key)),
            )
            for path_key, hash_ref in path_to_hash.items()
        },
        policy=release_policy or {},
    )


def _parse_attestable(content: str) -> models.Attestable:
    data = json.loads(content)
    if data.get("version") == 2:
        return models.AttestableV2.model_validate_json(content)
    return models.AttestableV1.model_validate_json(content)


def _path_basename(path_key: str) -> str:
    return path_key.rsplit("/", maxsplit=1)[-1]
