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
from typing import Any, Final

import aiofiles.os

import atr.analysis as analysis
import atr.log as log
import atr.models.results as results
import atr.sbom.streaming as streaming
import atr.tasks.checks as checks
import atr.tasks.task as task

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = ["suffixed_file_existence"]
CHECK_VERSION: Final[str] = "3"
REVIEW_VERSION: Final = "1"


async def check(args: checks.FunctionArguments) -> results.Results | None:
    """Check an SBOM exists for an artifact."""
    recorder = await args.recorder(CHECK_VERSION)
    if not (primary_abs_path := await recorder.abs_path()):
        return None
    if args.primary_rel_path is None:
        return None

    log.info(f"Checking SBOM exists for {primary_abs_path}")

    try:
        result_data = await _check_core_logic(recorder, str(args.primary_rel_path))
    except OSError as e:
        raise task.CheckRetryableError("Error during SBOM check execution", {"error": str(e)}) from e

    match result_data:
        case {"error": error} if error:
            await recorder.suggestion(error, result_data)
        case _ if result_data.get("found"):
            await recorder.note("SBOM located successfully", result_data)
        case _:
            raise RuntimeError("SBOM location failed for unknown reasons")

    return None


async def review(args: checks.FunctionArguments) -> results.Results | None:
    recorder = await args.recorder(REVIEW_VERSION)
    if (path := await recorder.abs_path()) is None:
        return None
    try:
        await asyncio.to_thread(streaming.sbom, path.path)
    except streaming.MalformedError as error:
        await recorder.concern(f"SBOM structure cannot be interpreted reliably: {error}", {"problem": str(error)})
    except streaming.UnsupportedError:
        return None
    except (OSError, streaming.LimitError) as error:
        raise task.CheckRetryableError("SBOM structure could not be checked", {"error": str(error)}) from error
    return None


async def _check_core_logic(recorder: checks.Recorder, artifact_rel_path: str) -> dict[str, Any]:
    """Verify an SBOM exists for the specified artifact."""

    # The candidates are relative to the release root, so resolving each through the recorder keeps
    # the parent-directory search bounded to the revision rather than climbing the filesystem
    sbom_expected_paths = analysis.sbom_candidates(artifact_rel_path, analysis.SBOM_SUFFIXES)
    log.info(f"Attempting to find one of: '{','.join(sbom_expected_paths)}'")

    return await _check_core_logic_find_sboms(recorder, sbom_expected_paths)


async def _check_core_logic_find_sboms(recorder: checks.Recorder, sbom_expected_paths: list[str]) -> dict[str, Any]:

    for sbom in sbom_expected_paths:
        abs_path = await recorder.abs_path(sbom)
        if (abs_path is not None) and await aiofiles.os.path.exists(abs_path.path):
            return {
                "found": True,
                "sbom_path": sbom,
                "status": "SBOM present",
            }

    return {
        "found": False,
        "error": "Could not locate a matching SBOM",
        "error_kind": "missing_sbom",
    }
