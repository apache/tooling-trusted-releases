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

import json
import pathlib
from typing import Any

import aiofiles
import aiofiles.os
import pydantic

import atr.classify as classify
import atr.hashes as hashes
import atr.log as log
import atr.models.attestable as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.util as util


def attestable_checks_path(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> pathlib.Path:
    return paths.get_attestable_dir() / str(project_key) / str(version_key) / f"{revision_number!s}.checks.json"


def attestable_path(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> pathlib.Path:
    return paths.get_attestable_dir() / str(project_key) / str(version_key) / f"{revision_number!s}.json"


def can_write_file_state_rows(
    previous: models.Attestable | None,
    parent_name: str | None,
) -> bool:
    is_first_revision = (previous is None) and (parent_name is None)
    is_v2_continuation = isinstance(previous, models.AttestableV2)
    return is_first_revision or is_v2_continuation


def compute_classifications(
    path_to_hash: dict[str, str],
    release_policy: dict[str, Any] | None,
    base_path: pathlib.Path,
) -> dict[str, str]:
    policy = release_policy or {}
    source_matcher, binary_matcher = classify.matchers_from_policy(
        policy.get("source_artifact_paths", []),
        policy.get("binary_artifact_paths", []),
        base_path,
    )
    return {
        path_key: classify.classify(pathlib.Path(path_key), base_path, source_matcher, binary_matcher).value
        for path_key in path_to_hash
    }


def compute_file_state_rows(
    release_key: str,
    since_revision_seq: int,
    path_to_hash: dict[str, str],
    classifications: dict[str, str],
    previous: models.Attestable | None,
) -> list[sql.ReleaseFileState]:
    prev_hashes: dict[str, str] = {}
    prev_classifications: dict[str, str] = {}
    if previous is not None:
        prev_hashes = path_hashes(previous)
        if isinstance(previous, models.AttestableV2):
            prev_classifications = {path_key: entry.classification for path_key, entry in previous.paths.items()}

    rows: list[sql.ReleaseFileState] = []

    for path_key in sorted(path_to_hash):
        content_hash = path_to_hash[path_key]
        classification = classifications[path_key]
        # If all prior metadata properties are the same, we skip recording an event
        if (prev_hashes.get(path_key) == content_hash) and (prev_classifications.get(path_key) == classification):
            continue
        rows.append(
            sql.ReleaseFileState(
                release_key=release_key,
                path=path_key,
                since_revision_seq=since_revision_seq,
                present=True,
                content_hash=content_hash,
                classification=classification,
            )
        )

    for path_key in sorted(prev_hashes):
        if path_key not in path_to_hash:
            rows.append(
                sql.ReleaseFileState(
                    release_key=release_key,
                    path=path_key,
                    since_revision_seq=since_revision_seq,
                    present=False,
                    content_hash=None,
                    classification=None,
                )
            )

    return rows


def github_tp_payload_path(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> pathlib.Path:
    return paths.get_attestable_dir() / str(project_key) / str(version_key) / f"{revision_number!s}.github-tp.json"


async def github_tp_payload_write(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    github_payload: dict[str, Any],
) -> None:
    payload_path = github_tp_payload_path(project_key, version_key, revision_number)
    await util.atomic_write_file(payload_path, json.dumps(github_payload, indent=2))


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
            data = json.loads(await f.read())
        return _parse_attestable(data)
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
                data = json.loads(await f.read())
                if data.get("version") == 1:
                    log.warning(f"Found old checks file format in {file_path}, ignoring old checks")
                    return {}
            return models.AttestableChecksV2.model_validate(data).checks
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


async def paths_to_hashes_and_sizes(directory: pathlib.Path) -> tuple[dict[str, str], dict[str, int]]:
    path_to_hash: dict[str, str] = {}
    path_to_size: dict[str, int] = {}
    async for rel_path in util.paths_recursive(directory):
        full_path = directory / rel_path
        path_key = str(rel_path)
        if "\\" in path_key:
            # TODO: We should centralise this, and forbid some other characters too
            raise ValueError(f"Backslash in path is forbidden: {path_key}")
        path_to_hash[path_key] = await hashes.compute_file_hash(full_path)
        path_to_size[path_key] = (await aiofiles.os.stat(full_path)).st_size
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

    await util.atomic_modify_file(attestable_checks_path(project_key, version_key, revision_number), modify)


async def write_files_data(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    release_policy: dict[str, Any] | None,
    uploader_uid: str,
    previous: models.Attestable | None,
    path_to_hash: dict[str, str],
    path_to_size: dict[str, int],
    base_path: pathlib.Path,
    classifications: dict[str, str] | None = None,
) -> None:
    result = _generate_files_data(
        path_to_hash,
        path_to_size,
        revision_number,
        release_policy,
        uploader_uid,
        previous,
        base_path,
        classifications=classifications,
    )
    file_path = attestable_path(project_key, version_key, revision_number)
    await util.atomic_write_file(file_path, result.model_dump_json(indent=2))
    checks_file_path = attestable_checks_path(project_key, version_key, revision_number)
    if not checks_file_path.exists():
        async with aiofiles.open(checks_file_path, "w", encoding="utf-8") as f:
            await f.write(models.AttestableChecksV2().model_dump_json(indent=2))


def _compute_hashes_with_attribution(  # noqa: C901
    current_hash_to_paths: dict[str, set[str]],
    path_to_size: dict[str, int],
    previous: models.Attestable | None,
    uploader_uid: str,
    revision_number: safe.RevisionNumber,
) -> dict[str, models.HashEntry]:
    previous_hash_to_paths: dict[str, set[str]] = {}
    if previous is not None:
        for path_key, hash_ref in path_hashes(previous).items():
            previous_hash_to_paths.setdefault(hash_ref, set()).add(path_key)

    new_hashes: dict[str, models.HashEntry] = {}
    if previous is not None:
        for hash_key, hash_entry in previous.hashes.items():
            new_hashes[hash_key] = hash_entry.model_copy(deep=True)

    for hash_ref, current_paths in current_hash_to_paths.items():
        previous_paths = previous_hash_to_paths.get(hash_ref, set())
        sample_path = next(iter(current_paths))
        file_size = path_to_size[sample_path]
        current_basenames = {_path_basename(path_key) for path_key in current_paths}

        if hash_ref not in new_hashes:
            new_hashes[hash_ref] = models.HashEntry(
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


def _generate_files_data(
    path_to_hash: dict[str, str],
    path_to_size: dict[str, int],
    revision_number: safe.RevisionNumber,
    release_policy: dict[str, Any] | None,
    uploader_uid: str,
    previous: models.Attestable | None,
    base_path: pathlib.Path,
    classifications: dict[str, str] | None = None,
) -> models.AttestableV2:
    current_hash_to_paths: dict[str, set[str]] = {}
    for path_key, hash_ref in path_to_hash.items():
        current_hash_to_paths.setdefault(hash_ref, set()).add(path_key)

    new_hashes = _compute_hashes_with_attribution(
        current_hash_to_paths, path_to_size, previous, uploader_uid, revision_number
    )

    if classifications is None:
        classifications = compute_classifications(path_to_hash, release_policy, base_path)
    return models.AttestableV2(
        hashes=dict(new_hashes),
        paths={
            path_key: models.PathEntryV2(content_hash=hash_ref, classification=classifications[path_key])
            for path_key, hash_ref in path_to_hash.items()
        },
        policy=release_policy or {},
    )


def _parse_attestable(data: dict[str, object]) -> models.Attestable:
    if data.get("version") == 2:
        return models.AttestableV2.model_validate(data)
    return models.AttestableV1.model_validate(data)


def _path_basename(path_key: str) -> str:
    return path_key.rsplit("/", maxsplit=1)[-1]
