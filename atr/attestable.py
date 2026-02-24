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
from typing import TYPE_CHECKING, Any

import aiofiles
import aiofiles.os
import pydantic

import atr.hashes as hashes
import atr.log as log
import atr.models.attestable as models
import atr.paths as paths
import atr.util as util

if TYPE_CHECKING:
    import pathlib


def attestable_checks_path(project_name: str, version_name: str, revision_number: str) -> pathlib.Path:
    return paths.get_attestable_dir() / project_name / version_name / f"{revision_number}.checks.json"


def attestable_path(project_name: str, version_name: str, revision_number: str) -> pathlib.Path:
    return paths.get_attestable_dir() / project_name / version_name / f"{revision_number}.json"


def attestable_paths_path(project_name: str, version_name: str, revision_number: str) -> pathlib.Path:
    return paths.get_attestable_dir() / project_name / version_name / f"{revision_number}.paths.json"


def github_tp_payload_path(project_name: str, version_name: str, revision_number: str) -> pathlib.Path:
    return paths.get_attestable_dir() / project_name / version_name / f"{revision_number}.github-tp.json"


async def github_tp_payload_write(
    project_name: str, version_name: str, revision_number: str, github_payload: dict[str, Any]
) -> None:
    payload_path = github_tp_payload_path(project_name, version_name, revision_number)
    await util.atomic_write_file(payload_path, json.dumps(github_payload, indent=2))


async def load(
    project_name: str,
    version_name: str,
    revision_number: str,
) -> models.AttestableV1 | None:
    file_path = attestable_path(project_name, version_name, revision_number)
    if not await aiofiles.os.path.isfile(file_path):
        return None
    try:
        async with aiofiles.open(file_path, encoding="utf-8") as f:
            data = json.loads(await f.read())
        return models.AttestableV1.model_validate(data)
    except (json.JSONDecodeError, pydantic.ValidationError) as e:
        log.warning(f"Could not parse {file_path}, starting fresh: {e}")
        return None


async def load_checks(
    project_name: str,
    version_name: str,
    revision_number: str,
) -> dict[str, dict[str, str]]:
    file_path = attestable_checks_path(project_name, version_name, revision_number)
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


async def load_paths(
    project_name: str,
    version_name: str,
    revision_number: str,
) -> dict[str, str] | None:
    file_path = attestable_paths_path(project_name, version_name, revision_number)
    if await aiofiles.os.path.isfile(file_path):
        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                data = json.loads(await f.read())
            return models.AttestablePathsV1.model_validate(data).paths
        except (json.JSONDecodeError, pydantic.ValidationError) as e:
            # log.warning(f"Could not parse {file_path}, trying combined file: {e}")
            log.warning(f"Could not parse {file_path}: {e}")
    # combined = await load(project_name, version_name, revision_number)
    # if combined is not None:
    #     return combined.paths
    return None


def migrate_to_paths_files() -> int:
    attestable_dir = paths.get_attestable_dir()
    if not attestable_dir.is_dir():
        return 0
    count = 0
    for project_dir in sorted(attestable_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for version_dir in sorted(project_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            for json_file in sorted(version_dir.glob("*.json")):
                if "." in json_file.stem:
                    continue
                target = version_dir / f"{json_file.stem}.paths.json"
                if target.exists():
                    continue
                try:
                    with open(json_file, encoding="utf-8") as f:
                        data = json.loads(f.read())
                    validated = models.AttestableV1.model_validate(data)
                    paths_result = models.AttestablePathsV1(paths=validated.paths)
                    tmp = target.with_suffix(".tmp")
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(paths_result.model_dump_json(indent=2))
                    tmp.replace(target)
                    count += 1
                except (json.JSONDecodeError, pydantic.ValidationError):
                    continue
    return count


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
    project_name: str,
    version_name: str,
    revision_number: str,
    rel_path: str,
    checks: dict[str, str],
) -> None:
    log.info(f"Writing checks for {project_name}/{version_name}/{revision_number}/{rel_path}: {checks}")

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

    await util.atomic_modify_file(attestable_checks_path(project_name, version_name, revision_number), modify)


async def write_files_data(
    project_name: str,
    version_name: str,
    revision_number: str,
    release_policy: dict[str, Any] | None,
    uploader_uid: str,
    previous: models.AttestableV1 | None,
    path_to_hash: dict[str, str],
    path_to_size: dict[str, int],
) -> None:
    result = _generate_files_data(path_to_hash, path_to_size, revision_number, release_policy, uploader_uid, previous)
    file_path = attestable_path(project_name, version_name, revision_number)
    await util.atomic_write_file(file_path, result.model_dump_json(indent=2))
    paths_result = models.AttestablePathsV1(paths=result.paths)
    paths_file_path = attestable_paths_path(project_name, version_name, revision_number)
    await util.atomic_write_file(paths_file_path, paths_result.model_dump_json(indent=2))
    checks_file_path = attestable_checks_path(project_name, version_name, revision_number)
    if not checks_file_path.exists():
        async with aiofiles.open(checks_file_path, "w", encoding="utf-8") as f:
            await f.write(models.AttestableChecksV2().model_dump_json(indent=2))


def _compute_hashes_with_attribution(  # noqa: C901
    current_hash_to_paths: dict[str, set[str]],
    path_to_size: dict[str, int],
    previous: models.AttestableV1 | None,
    uploader_uid: str,
    revision_number: str,
) -> dict[str, models.HashEntry]:
    previous_hash_to_paths: dict[str, set[str]] = {}
    if previous is not None:
        for path_key, hash_ref in previous.paths.items():
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
                uploaders=[(uploader_uid, revision_number)],
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
            if (uploader_uid, revision_number) not in existing_entries:
                new_hashes[hash_ref].uploaders.append((uploader_uid, revision_number))

    return new_hashes


def _generate_files_data(
    path_to_hash: dict[str, str],
    path_to_size: dict[str, int],
    revision_number: str,
    release_policy: dict[str, Any] | None,
    uploader_uid: str,
    previous: models.AttestableV1 | None,
) -> models.AttestableV1:
    current_hash_to_paths: dict[str, set[str]] = {}
    for path_key, hash_ref in path_to_hash.items():
        current_hash_to_paths.setdefault(hash_ref, set()).add(path_key)

    new_hashes = _compute_hashes_with_attribution(
        current_hash_to_paths, path_to_size, previous, uploader_uid, revision_number
    )

    return models.AttestableV1(
        paths=dict(path_to_hash),
        hashes=dict(new_hashes),
        policy=release_policy or {},
    )


def _path_basename(path_key: str) -> str:
    return path_key.rsplit("/", maxsplit=1)[-1]
