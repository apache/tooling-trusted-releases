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
import pathlib
import re
from typing import Final

import aiofiles.os

import atr.analysis as analysis
import atr.log as log
import atr.models.results as results
import atr.models.safe as safe
import atr.tasks.checks as checks
import atr.user as user
import atr.util as util

_ALLOWED_TOP_LEVEL_NAMES: Final = ("CHANGES", "LICENSE", "NOTICE", "README", "SECURITY")
_ALLOWED_TOP_LEVEL_SUFFIXES: Final = ("", ".adoc", ".md", ".rst", ".txt")
_ALLOWED_TOP_LEVEL: Final = frozenset(
    (name + suffix) for name in _ALLOWED_TOP_LEVEL_NAMES for suffix in _ALLOWED_TOP_LEVEL_SUFFIXES
)
# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = ["is_podling", "all_files"]
CHECK_VERSION: Final[str] = "2"


async def check(args: checks.FunctionArguments) -> results.Results | None:
    """Check file path structure and naming conventions against ASF release policy for all files in a release."""
    # We refer to the following authoritative policies:
    # - Release Creation Process (RCP)
    # https://infra.apache.org/release-publishing.html
    # - Release Distribution Policy (RDP)
    # https://infra.apache.org/release-distribution.html
    # - Incubation Policy (IP)
    # https://incubator.apache.org/policy/incubation.html
    base_recorder = await args.recorder(CHECK_VERSION)

    recorder_errors = await checks.Recorder.create(
        checker=checks.function_key(check) + "_errors",
        checker_version=CHECK_VERSION,
        inputs_hash=base_recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=None,
        afresh=True,
    )
    recorder_warnings = await checks.Recorder.create(
        checker=checks.function_key(check) + "_warnings",
        checker_version=CHECK_VERSION,
        inputs_hash=base_recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=None,
        afresh=True,
    )
    recorder_success = await checks.Recorder.create(
        checker=checks.function_key(check) + "_success",
        checker_version=CHECK_VERSION,
        inputs_hash=base_recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=None,
        afresh=True,
    )

    # As primary_rel_path is None, the base path is the release candidate draft directory
    if not (base_path := await recorder_success.abs_path()):
        return

    if not await aiofiles.os.path.isdir(base_path):
        log.error(f"Base release directory does not exist or is not a directory: {base_path}")
        return

    is_podling = args.extra_args.get("is_podling", False)
    relative_paths = [p async for p in util.paths_recursive(base_path)]
    relative_paths_set = set(str(p) for p in relative_paths)

    for relative_path in relative_paths:
        # Delegate processing of each path to the helper function
        await _check_path_process_single(
            args.asf_uid,
            base_path,
            relative_path,
            recorder_errors,
            recorder_warnings,
            recorder_success,
            relative_paths_set,
            is_podling,
        )

    return None


async def _check_artifact_rules(
    base_path: safe.StatePath,
    relative_path: safe.RelPath,
    relative_paths: set[str],
    errors: list[str],
    blockers: list[str],
    is_podling: bool,
) -> None:
    """Check rules specific to artifact files."""
    full_path = base_path / relative_path

    # RDP says that .asc is required
    asc_path = full_path.path.with_suffix(full_path.path.suffix + ".asc")
    if not await aiofiles.os.path.exists(asc_path):
        blockers.append(f"Missing corresponding signature file ({relative_path}.asc)")

    # RDP requires one of .sha256 or .sha512
    path = relative_path.as_path()
    relative_sha256_path = path.with_suffix(path.suffix + ".sha256")
    relative_sha512_path = path.with_suffix(path.suffix + ".sha512")
    has_sha256 = str(relative_sha256_path) in relative_paths
    has_sha512 = str(relative_sha512_path) in relative_paths
    if not (has_sha256 or has_sha512):
        blockers.append(f"Missing corresponding checksum file ({relative_path}.sha256 or {relative_path}.sha512)")

    # IP requires "incubating" in the filename
    if is_podling is True:
        # TODO: Allow "incubator" too as #114 requests?
        if "incubating" not in full_path.path.name:
            blockers.append("Podling artifact filenames must include 'incubating'")


async def _check_metadata_rules(
    _base_path: safe.StatePath,
    relative_path: safe.RelPath,
    relative_paths: set[str],
    ext_metadata: str,
    errors: list[str],
    blockers: list[str],
    warnings: list[str],
    *,
    is_standalone: bool = False,
) -> None:
    """Check rules specific to metadata files (.asc, .sha*, etc.)."""
    suffixes = set(relative_path.as_path().suffixes)

    if ".md5" in suffixes:
        # Forbidden by RCP, deprecated by RDP
        blockers.append("The use of .md5 is forbidden, please use .sha512")
    if ".sha1" in suffixes:
        # Deprecated by RDP
        errors.append("The use of .sha1 is deprecated, please use .sha512")
    if ".sha" in suffixes:
        # Discouraged by RDP
        errors.append("The use of .sha is discouraged, please use .sha512")
    if ".sig" in suffixes:
        # Forbidden by RCP, forbidden by RDP
        blockers.append("Binary signature files (.sig) are forbidden, please use .asc")

    # "Signature and checksum files for verifying distributed artifacts should
    # not be provided, unless named as indicated above." (RDP)
    # Also .mds is allowed, but we'll ignore that for now
    # TODO: Is .mds supported in analysis.METADATA_SUFFIXES?
    if ext_metadata not in {".asc", ".cdx.json", ".sha256", ".sha512", ".md5", ".sha", ".sha1"}:
        errors.append("The use of this metadata file is discouraged")

    # Check whether the corresponding artifact exists
    artifact_path_base = str(relative_path).removesuffix(ext_metadata)
    if is_standalone:
        has_artifact = any((p.startswith(artifact_path_base + ".") and analysis.is_artifact(p)) for p in relative_paths)
        if not has_artifact:
            errors.append(
                f"Metadata file exists but no corresponding artifact with base '{artifact_path_base}' was found"
            )
    elif artifact_path_base not in relative_paths:
        errors.append(f"Metadata file exists but corresponding artifact '{artifact_path_base}' is missing")


async def _check_path_process_single(  # noqa: C901
    asf_uid: str,
    base_path: safe.StatePath,
    relative_path: safe.RelPath,
    recorder_errors: checks.Recorder,
    recorder_warnings: checks.Recorder,
    recorder_success: checks.Recorder,
    relative_paths: set[str],
    is_podling: bool,
) -> None:
    """Process and check a single path within the release directory."""
    full_path = base_path / relative_path
    relative_path_str = str(relative_path)

    # For debugging and testing
    if (await user.is_admin_async(asf_uid)) and (full_path.path.name == "deliberately_slow_ATR_task_filename.txt"):
        await asyncio.sleep(20)

    errors: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []

    # The Release Distribution Policy specifically allows README and CHANGES, etc.
    # We assume that LICENSE and NOTICE are permitted also
    path = relative_path.as_path()
    if path.name == "KEYS":
        errors.append("The KEYS file should be uploaded via the 'Keys' section, not included in the artifact bundle")
    if path.name in analysis.DISALLOWED_FILENAMES:
        await _record(
            recorder_errors,
            recorder_warnings,
            recorder_success,
            relative_path,
            errors,
            [f"Disallowed file: {path.name}"],
            warnings,
        )
        return
    elif path.suffix in analysis.DISALLOWED_SUFFIXES:
        await _record(
            recorder_errors,
            recorder_warnings,
            recorder_success,
            relative_path,
            errors,
            [f"Disallowed file type: {path.suffix}"],
            warnings,
        )
        return
    elif any(util.is_disallowed_dotfile(part) for part in path.parts):
        # TODO: There is not a a policy for this
        # We should enquire as to whether such a policy should be instituted
        # We're forbidding dotfiles to catch accidental uploads of e.g. .git or .htaccess
        # Such cases are likely to be in error, and could carry security risks
        # We allow .atr/ files, e.g. .atr/license-headers-ignore
        errors.append("Contains a segment that is a disallowed dotfile")

    search = re.search(analysis.extension_pattern(), relative_path_str)
    ext_artifact = search.group("artifact") if search else None
    ext_metadata = search.group("metadata") if search else None

    is_standalone_metadata = False
    if (not ext_artifact) and (not ext_metadata):
        for suffix in analysis.STANDALONE_METADATA_SUFFIXES:
            if relative_path_str.endswith(suffix):
                ext_metadata = suffix
                is_standalone_metadata = True
                break

    if ext_artifact:
        log.info(f"Checking artifact rules for {full_path}")
        await _check_artifact_rules(base_path, relative_path, relative_paths, errors, blockers, is_podling)
    elif ext_metadata:
        log.info(f"Checking metadata rules for {full_path}")
        await _check_metadata_rules(
            base_path,
            relative_path,
            relative_paths,
            ext_metadata,
            errors,
            blockers,
            warnings,
            is_standalone=is_standalone_metadata,
        )
    else:
        log.info(f"Checking general rules for {full_path}")
        if (path.parent == pathlib.Path(".")) and (path.name not in _ALLOWED_TOP_LEVEL):
            errors.append(f"Unknown top level file: {path.name}")

    await _record(
        recorder_errors,
        recorder_warnings,
        recorder_success,
        relative_path,
        errors,
        blockers,
        warnings,
    )


async def _record(
    recorder_errors: checks.Recorder,
    recorder_warnings: checks.Recorder,
    recorder_success: checks.Recorder,
    relative_path: safe.RelPath,
    errors: list[str],
    blockers: list[str],
    warnings: list[str],
) -> None:
    for error in errors:
        await recorder_errors.failure(f"{relative_path}: {error}", {}, primary_rel_path=relative_path)
    for item in blockers:
        await recorder_errors.blocker(f"{relative_path}: {item}", {}, primary_rel_path=relative_path)
    for warning in warnings:
        await recorder_warnings.warning(f"{relative_path}: {warning}", {}, primary_rel_path=relative_path)
    if not (errors or blockers or warnings):
        await recorder_success.success(
            f"{relative_path}: Path structure and naming conventions conform to policy",
            {},
            primary_rel_path=relative_path,
        )
