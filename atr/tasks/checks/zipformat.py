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

import asyncio
import os
import zipfile
from typing import Any, Final

import atr.log as log
import atr.models.results as results
import atr.tarzip as tarzip
import atr.tasks.checks as checks
import atr.util as util

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = []
CHECK_VERSION: Final[str] = "1"


async def integrity(args: checks.FunctionArguments) -> results.Results | None:
    """Check that the zip archive is not corrupted and can be opened."""
    recorder = await args.recorder()
    if not (artifact_abs_path := await recorder.abs_path()):
        return None

    await recorder.cache_key_set(INPUT_POLICY_KEYS, CHECK_VERSION, INPUT_EXTRA_ARGS)

    log.info(f"Checking zip integrity for {artifact_abs_path} (rel: {args.primary_rel_path})")

    try:
        result_data = await asyncio.to_thread(_integrity_check_core_logic, str(artifact_abs_path))
        if result_data.get("error"):
            await recorder.failure(result_data["error"], result_data)
        else:
            await recorder.success(
                f"Zip archive integrity OK ({util.plural(result_data['member_count'], 'member')})", result_data
            )
    except Exception as e:
        await recorder.failure("Error checking zip integrity", {"error": str(e)})

    return None


async def structure(args: checks.FunctionArguments) -> results.Results | None:
    """Check that the zip archive has a single root directory matching the artifact name."""
    recorder = await args.recorder()
    if not (artifact_abs_path := await recorder.abs_path()):
        return None
    if await recorder.primary_path_is_binary():
        return None

    await recorder.cache_key_set(INPUT_POLICY_KEYS, CHECK_VERSION, INPUT_EXTRA_ARGS)

    log.info(f"Checking zip structure for {artifact_abs_path} (rel: {args.primary_rel_path})")

    try:
        result_data = await asyncio.to_thread(_structure_check_core_logic, str(artifact_abs_path))

        if result_data.get("warning"):
            await recorder.failure(result_data["warning"], result_data)
        elif result_data.get("error"):
            await recorder.failure(result_data["error"], result_data)
        else:
            await recorder.success(f"Zip structure OK (root: {result_data['root_dir']})", result_data)
    except Exception as e:
        await recorder.failure("Error checking zip structure", {"error": str(e)})

    return None


def _integrity_check_core_logic(artifact_path: str) -> dict[str, Any]:
    """Verify that a zip file can be opened and its members listed."""
    try:
        with tarzip.open_archive(artifact_path) as archive:
            # This is a simple check using list members
            # We can use zf.testzip() for CRC checks if needed, though this will be slower
            member_count = sum(1 for _ in archive)
            return {"member_count": member_count}
    except tarzip.ArchiveMemberLimitExceededError as e:
        return {"error": f"Archive has too many members: {e}"}
    except zipfile.BadZipFile as e:
        return {"error": f"Bad zip file: {e}"}
    except FileNotFoundError:
        return {"error": "File not found"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}


def _structure_check_core_logic(artifact_path: str) -> dict[str, Any]:  # noqa: C901
    """Verify the internal structure of the zip archive."""
    try:
        with tarzip.open_archive(artifact_path) as archive:
            members: list[tarzip.Member] = []
            package_json: bytes | None = None

            for member in archive:
                members.append(member)
                if package_json is None:
                    member_name = member.name.lstrip("./")
                    if (member_name == "package/package.json") and member.isfile():
                        size = member.size if hasattr(member, "size") else 0
                        if (size > 0) and (size <= util.NPM_PACKAGE_JSON_MAX_SIZE):
                            f = archive.extractfile(member)
                            if f is not None:
                                try:
                                    package_json = f.read()
                                finally:
                                    f.close()

            if not members:
                return {"error": "Archive is empty"}

            base_name = os.path.basename(artifact_path)
            basename_from_filename = base_name.removesuffix(".zip")
            expected_roots = util.permitted_archive_roots(basename_from_filename)

            root_dirs, non_rooted_files = _structure_check_core_logic_find_roots(members)
            member_names = [m.name for m in members]
            actual_root, error_msg = _structure_check_core_logic_validate_root(
                member_names, root_dirs, non_rooted_files, expected_roots
            )

            if error_msg is not None:
                if (actual_root == "package") and (package_json is not None):
                    npm_info, _ = util.parse_npm_pack_info(package_json, basename_from_filename)
                    if npm_info is not None:
                        npm_data: dict[str, Any] = {
                            "root_dir": actual_root,
                            "expected_roots": expected_roots,
                            "npm_pack": {
                                "name": npm_info.name,
                                "version": npm_info.version,
                                "filename_match": npm_info.filename_match,
                            },
                        }
                        if npm_info.filename_match is False:
                            npm_data["error"] = "npm pack layout detected but filename does not match package.json"
                        return npm_data
                result_data: dict[str, Any] = {"expected_roots": expected_roots}
                if error_msg.startswith("Root directory mismatch"):
                    result_data["error"] = error_msg
                else:
                    result_data["error"] = error_msg
                return result_data
            if actual_root:
                return {"root_dir": actual_root, "expected_roots": expected_roots}
            return {"error": "Unknown structure validation error"}

    except tarzip.ArchiveMemberLimitExceededError as e:
        return {"error": f"Archive has too many members: {e}"}
    except zipfile.BadZipFile as e:
        return {"error": f"Bad zip file: {e}"}
    except FileNotFoundError:
        return {"error": "File not found"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}


def _structure_check_core_logic_find_roots(members: list[tarzip.Member]) -> tuple[set[str], list[str]]:
    """Identify root directories and non-rooted files in a zip archive."""
    root_dirs: set[str] = set()
    non_rooted_files: list[str] = []
    for member in members:
        if "/" in member.name:
            root_dirs.add(member.name.split("/", 1)[0])
        elif not member.isdir():
            non_rooted_files.append(member.name)
    return root_dirs, non_rooted_files


def _structure_check_core_logic_validate_root(
    members: list[str], root_dirs: set[str], non_rooted_files: list[str], expected_roots: list[str]
) -> tuple[str | None, str | None]:
    """Validate the identified root structure against expectations."""
    if non_rooted_files:
        return None, f"Files found directly in root: {non_rooted_files}"
    if not root_dirs:
        return None, "No directories found in archive"
    if len(root_dirs) > 1:
        return None, f"Multiple root directories found: {sorted(list(root_dirs))}"

    actual_root = next(iter(root_dirs))
    if actual_root not in expected_roots:
        expected_roots_display = "', '".join(expected_roots)
        return (
            actual_root,
            f"Root directory mismatch. Expected one of '{expected_roots_display}', found '{actual_root}'",
        )

    # Check whether all members are under the correct root directory
    expected_prefix = f"{actual_root.rstrip('/')}/"
    for member in members:
        if member == actual_root.rstrip("/"):
            continue
        if not member.startswith(expected_prefix):
            return None, f"Member found outside expected root directory: {member}"

    return actual_root, None
