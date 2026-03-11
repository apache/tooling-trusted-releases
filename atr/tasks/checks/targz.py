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
import contextlib
import os
import pathlib
import stat
from typing import Final

import atr.archives as archives
import atr.log as log
import atr.models.results as results
import atr.tarzip as tarzip
import atr.tasks.checks as checks
import atr.util as util

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = []
CHECK_VERSION_INTEGRITY: Final[str] = "3"
CHECK_VERSION_STRUCTURE: Final[str] = "3"


class RootDirectoryError(Exception):
    """Exception raised when a root directory is not found in an archive."""

    ...


async def integrity(args: checks.FunctionArguments) -> results.Results | None:
    """Check the integrity of a .tar.gz file."""
    recorder = await args.recorder()
    if not (artifact_abs_path := await recorder.abs_path()):
        return None

    log.info(f"Checking integrity for {artifact_abs_path} (rel: {args.primary_rel_path})")

    chunk_size = 4096
    try:
        size = await asyncio.to_thread(archives.total_size, str(artifact_abs_path), chunk_size)
        await recorder.success("Able to read all entries of the archive using tarfile", {"size": size})
    except tarzip.ArchiveMemberLimitExceededError as e:
        await recorder.failure(f"Archive has too many members: {e}", {"error": str(e)})
    except Exception as e:
        await recorder.failure("Unable to read all entries of the archive using tarfile", {"error": str(e)})
    return None


def root_directory(cache_dir: pathlib.Path) -> tuple[str, bytes | None]:
    """Find root directory and read package/package.json from the extracted tree."""
    # The ._ prefix is a metadata convention
    entries = sorted(e for e in os.listdir(cache_dir) if not e.startswith("._"))

    if not entries:
        raise RootDirectoryError("No root directory found in archive")
    if len(entries) > 1:
        raise RootDirectoryError(f"Multiple root directories found: {entries[0]}, {entries[1]}")

    root = entries[0]
    root_path = cache_dir / root
    try:
        root_stat = root_path.lstat()
    except OSError as e:
        raise RootDirectoryError(f"Unable to inspect root entry '{root}': {e}") from e
    if not stat.S_ISDIR(root_stat.st_mode):
        raise RootDirectoryError(f"Root entry is not a directory: {root}")

    package_json: bytes | None = None

    if root == "package":
        package_json_path = cache_dir / "package" / "package.json"
        with contextlib.suppress(FileNotFoundError, OSError):
            package_json_stat = package_json_path.lstat()
            # We do this to avoid allowing package.json to be a symlink
            if stat.S_ISREG(package_json_stat.st_mode):
                size = package_json_stat.st_size
                if (size > 0) and (size <= util.NPM_PACKAGE_JSON_MAX_SIZE):
                    package_json = package_json_path.read_bytes()

    return root, package_json


async def structure(args: checks.FunctionArguments) -> results.Results | None:  # noqa: C901
    """Check the structure of a .tar.gz file using the extracted tree."""
    recorder = await args.recorder()
    if not (artifact_abs_path := await recorder.abs_path()):
        return None
    if await recorder.primary_path_is_binary():
        return None

    cache_dir = await checks.resolve_cache_dir(args)
    if cache_dir is None:
        await recorder.failure(
            "Extracted archive tree is not available",
            {"rel_path": args.primary_rel_path},
        )
        return None

    filename = artifact_abs_path.name
    basename_from_filename: Final[str] = (
        filename.removesuffix(".tar.gz") if filename.endswith(".tar.gz") else filename.removesuffix(".tgz")
    )
    expected_roots: Final[list[str]] = util.permitted_archive_roots(basename_from_filename)
    expected_roots_display = ", ".join(expected_roots)
    log.info(
        "Checking structure for "
        f"{artifact_abs_path} (expected roots: {expected_roots_display}) (rel: {args.primary_rel_path})"
    )

    try:
        root, package_json = await asyncio.to_thread(root_directory, cache_dir)
        data: dict[str, object] = {
            "root": root,
            "basename_from_filename": basename_from_filename,
            "expected_roots": expected_roots,
        }
        if root in expected_roots:
            await recorder.success("Archive contains exactly one root directory matching an expected name", data)
        elif root == "package":
            if package_json is not None:
                npm_info, npm_error = util.parse_npm_pack_info(package_json, basename_from_filename)
                if npm_info is not None:
                    data["npm_pack"] = {
                        "name": npm_info.name,
                        "version": npm_info.version,
                        "filename_match": npm_info.filename_match,
                    }
                    if npm_info.filename_match is False:
                        await recorder.failure(
                            "npm pack layout detected but filename does not match package.json", data
                        )
                    else:
                        await recorder.success("npm pack layout detected, allowing package/ root", data)
                else:
                    if npm_error is not None:
                        data["npm_pack_error"] = npm_error
                    await recorder.failure(
                        f"Root directory '{root}' does not match expected names '{expected_roots_display}'", data
                    )
            else:
                await recorder.failure(
                    f"Root directory '{root}' does not match expected names '{expected_roots_display}'", data
                )
        else:
            await recorder.failure(
                f"Root directory '{root}' does not match expected names '{expected_roots_display}'", data
            )
    except RootDirectoryError as e:
        await recorder.failure("Could not get the root directory of the archive", {"error": str(e)})
    except Exception as e:
        await recorder.failure("Unable to verify archive structure", {"error": str(e)})
    return None
