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
import pathlib
import stat
from typing import Any, Final

import atr.log as log
import atr.models.results as results
import atr.tasks.checks as checks
import atr.util as util

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = []
CHECK_VERSION_STRUCTURE: Final[str] = "2"


async def structure(args: checks.FunctionArguments) -> results.Results | None:
    """Check that the zip archive has a single root directory matching the artifact name."""
    # TODO: We can merge this with the targz structure check
    # Now that we migrated to extracted trees, they're very similar
    # For simplicity, they've been updated separately for now
    # (There are several small differences to resolve between the two)
    recorder = await args.recorder()
    if not (artifact_abs_path := await recorder.abs_path()):
        return None
    if await recorder.primary_path_is_binary():
        return None

    archive_dir = await checks.resolve_archive_dir(args)
    if archive_dir is None:
        await recorder.failure(
            "Extracted archive tree is not available",
            {"rel_path": args.primary_rel_path},
        )
        return None

    log.info(f"Checking zip structure for {artifact_abs_path} (rel: {args.primary_rel_path})")

    try:
        result_data = await asyncio.to_thread(_structure_check_core_logic, archive_dir, str(artifact_abs_path))

        if result_data.get("error"):
            await recorder.failure(result_data["error"], result_data)
        else:
            await recorder.success(f"Zip structure OK (root: {result_data['root_dir']})", result_data)
    except Exception as e:
        await recorder.failure("Error checking zip structure", {"error": str(e)})

    return None


def _structure_check_core_logic(archive_dir: pathlib.Path, artifact_path: str) -> dict[str, Any]:
    """Verify the internal structure of the zip archive."""
    # The ._ prefix is a metadata convention
    entries = sorted(e for e in os.listdir(archive_dir) if not e.startswith("._"))
    if not entries:
        return {"error": "Archive is empty"}

    base_name = os.path.basename(artifact_path)
    basename_from_filename = base_name.removesuffix(".zip")
    expected_roots = util.permitted_archive_roots(basename_from_filename)

    root_dirs: list[str] = []
    non_rooted_entries: list[str] = []
    for entry in entries:
        entry_path = archive_dir / entry
        try:
            entry_stat = entry_path.lstat()
        except OSError as e:
            return {"error": f"Unable to inspect archive root entry '{entry}': {e}", "expected_roots": expected_roots}

        if stat.S_ISDIR(entry_stat.st_mode):
            root_dirs.append(entry)
        else:
            non_rooted_entries.append(entry)

    if non_rooted_entries:
        return {"error": f"Files found directly in root: {non_rooted_entries}", "expected_roots": expected_roots}

    if not root_dirs:
        return {"error": "No directories found in archive", "expected_roots": expected_roots}
    if len(root_dirs) > 1:
        return {"error": f"Multiple root directories found: {root_dirs}", "expected_roots": expected_roots}

    actual_root = root_dirs[0]
    if actual_root in expected_roots:
        return {"root_dir": actual_root, "expected_roots": expected_roots}

    if (actual_root == "package") and (
        npm_result := _structure_npm_result(archive_dir, basename_from_filename, actual_root, expected_roots)
    ):
        return npm_result

    expected_roots_display = "', '".join(expected_roots)
    return {
        "error": f"Root directory mismatch. Expected one of '{expected_roots_display}', found '{actual_root}'",
        "expected_roots": expected_roots,
    }


def _structure_npm_result(
    archive_dir: pathlib.Path, basename_from_filename: str, actual_root: str, expected_roots: list[str]
) -> dict[str, Any] | None:
    package_json_path = archive_dir / "package" / "package.json"
    try:
        package_json_stat = package_json_path.lstat()
    except (FileNotFoundError, OSError):
        return None

    if not stat.S_ISREG(package_json_stat.st_mode):
        return None

    size = package_json_stat.st_size
    if (size <= 0) or (size > util.NPM_PACKAGE_JSON_MAX_SIZE):
        return None

    package_json = package_json_path.read_bytes()
    npm_info, _ = util.parse_npm_pack_info(package_json, basename_from_filename)
    if npm_info is None:
        return None

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
