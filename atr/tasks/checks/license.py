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
import difflib
import hashlib
import os
import pathlib
import re
from collections.abc import Iterator
from typing import Any, Final

import atr.constants as constants
import atr.log as log
import atr.models.results as results
import atr.models.safe as safe
import atr.models.schema as schema
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.util as util

# Constant that must be present in the Apache License header
HTTP_APACHE_LICENSE_HEADER: Final[bytes] = (
    b"Licensed to the Apache Software Foundation ASF under one or mor"
    b"e contributor license agreements See the NOTICE file distribute"
    b"d with this work for additional information regarding copyright"
    b" ownership The ASF licenses this file to you under the Apache L"
    b"icense Version 2 0 the License you may not use this file except"
    b" in compliance with the License You may obtain a copy of the Li"
    b"cense at http www apache org licenses LICENSE 2 0 Unless requir"
    b"ed by applicable law or agreed to in writing software distribut"
    b"ed under the License"
)

HTTPS_APACHE_LICENSE_HEADER: Final[bytes] = HTTP_APACHE_LICENSE_HEADER.replace(b" http ", b" https ")

# Patterns for files to include in license header checks
# Ordered by their popularity in the Stack Overflow Developer Survey 2024
INCLUDED_PATTERNS: Final[list[str]] = [
    r"\.(js|mjs|cjs|jsx)$",  # JavaScript
    r"\.py$",  # Python
    r"\.(sql|ddl|dml)$",  # SQL
    r"\.(ts|tsx|mts|cts)$",  # TypeScript
    r"\.(sh|bash|zsh|ksh)$",  # Shell
    r"\.(java|jav)$",  # Java
    r"\.(cs|csx)$",  # C#
    r"\.(cpp|cxx|cc|c\+\+|hpp)$",  # C++
    r"\.(c|h)$",  # C
    r"\.(php|php[3-9]|phtml)$",  # PHP
    r"\.(ps1|psm1|psd1)$",  # PowerShell
    r"\.go$",  # Go
    r"\.rs$",  # Rust
    r"\.(kt|kts)$",  # Kotlin
    r"\.(lua)$",  # Lua
    r"\.dart$",  # Dart
    r"\.(asm|s|S)$",  # Assembly
    r"\.(rb|rbw)$",  # Ruby
    r"\.swift$",  # Swift
    r"\.(r|R)$",  # R
    r"\.(vb|vbs)$",  # Visual Basic
    r"\.m$",  # MATLAB
    r"\.vba$",  # VBA
    r"\.(groovy|gvy|gy|gsh)$",  # Groovy
    r"\.(scala|sc)$",  # Scala
    r"\.(pl|pm|t)$",  # Perl
]

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = ["license_check_mode", "source_excludes_lightweight"]
INPUT_EXTRA_ARGS: Final[list[str]] = ["is_podling"]
CHECK_VERSION_FILES: Final[str] = "4"
CHECK_VERSION_HEADERS: Final[str] = "3"

_BINARY_LICENSE_FILENAMES: Final[frozenset[str]] = frozenset({"LICENSE", "LICENSE.txt"})
_BINARY_NOTICE_FILENAMES: Final[frozenset[str]] = frozenset({"NOTICE", "NOTICE.txt"})
_MAX_LICENSE_NOTICE_SIZE: Final[int] = 1024 * 1024

# Types


class ArtifactData(schema.Strict):
    files_checked: int = schema.default(0)
    files_with_valid_headers: int = schema.default(0)
    files_with_invalid_headers: int = schema.default(0)
    files_skipped: int = schema.default(0)
    excludes_source: str = schema.default("none")


class ArtifactResult(schema.Strict):
    status: sql.CheckResultStatus
    message: str
    data: Any = schema.Field(default=None)


# class LicenseCheckResult(schema.Strict):
#     files_checked: list[str]
#     files_with_valid_headers: int
#     errors: list[str]
#     error_message: str | None
#     warning_message: str | None
#     valid: bool


class MemberResult(schema.Strict):
    status: sql.CheckResultStatus
    path: str
    message: str
    data: Any = schema.Field(default=None)


class MemberSkippedResult(schema.Strict):
    path: str
    reason: str


type Result = ArtifactResult | MemberResult | MemberSkippedResult

# Tasks


async def files(args: checks.FunctionArguments) -> results.Results | None:
    """Check that the LICENSE and NOTICE files exist and are valid."""
    recorder = await args.recorder(CHECK_VERSION_FILES)
    if not (artifact_abs_path := await recorder.abs_path()):
        return None

    is_source = await recorder.primary_path_is_source()
    if is_source:
        project = await recorder.project()
        if project.policy_license_check_mode == sql.LicenseCheckMode.RAT:
            return None

    is_podling = args.extra_args.get("is_podling", False)

    archive_dir = await checks.resolve_archive_dir(args)
    if archive_dir is None:
        await recorder.failure(
            "Extracted archive tree is not available",
            {"rel_path": args.primary_rel_path},
        )
        return None

    log.info(f"Checking license files for {artifact_abs_path} (rel: {args.primary_rel_path})")

    try:
        for result in await asyncio.to_thread(_files_check_core_logic, archive_dir, is_podling, not is_source):
            match result:
                case ArtifactResult():
                    await _record_artifact(recorder, result)
                case MemberResult():
                    await _record_member(recorder, result)
                case MemberSkippedResult():
                    pass

    except Exception as e:
        log.exception("Error during license file check execution:")
        await recorder.exception("Error during license file check execution", {"error": str(e)})

    return None


async def headers(args: checks.FunctionArguments) -> results.Results | None:
    """Check that all source files have valid license headers."""
    recorder = await args.recorder(CHECK_VERSION_HEADERS)
    if not (artifact_abs_path := await recorder.abs_path()):
        return None

    is_source = await recorder.primary_path_is_source()
    if is_source:
        project = await recorder.project()
        if project.policy_license_check_mode == sql.LicenseCheckMode.RAT:
            return None

    # if await recorder.check_cache(artifact_abs_path):
    #     log.info(f"Using cached license headers result for {artifact_abs_path} (rel: {args.primary_rel_path})")
    #     return None

    archive_dir = await checks.resolve_archive_dir(args)
    if archive_dir is None:
        await recorder.failure(
            "Extracted archive tree is not available",
            {"rel_path": args.primary_rel_path},
        )
        return None

    log.info(f"Checking license headers for {artifact_abs_path} (rel: {args.primary_rel_path})")

    project = await recorder.project()

    ignore_lines: list[str] = []
    excludes_source: str
    if is_source:
        ignore_lines = project.policy_source_excludes_lightweight
        excludes_source = "policy" if ignore_lines else "none"
    else:
        excludes_source = "none"

    artifact_basename = os.path.basename(str(artifact_abs_path))
    return await _headers_core(recorder, archive_dir, artifact_basename, ignore_lines, excludes_source)


def headers_validate(content: bytes, _filename: str) -> tuple[bool, str | None]:
    """Validate that the content contains the Apache License header."""
    generated_by_patterns = [
        b"Generated By:JJTree",
        b"Generated By:JavaCC",
    ]
    for pattern in generated_by_patterns:
        if pattern in content:
            return True, None

    r_span = re.compile(rb"Licensed to the.*?under the License", re.MULTILINE)
    r_words = re.compile(rb"[A-Za-z0-9]+")

    # Normalise the content
    content = re.sub(rb"[ \t\r\n]+", b" ", content)

    # For each matching heuristic span...
    for span in r_span.finditer(content):
        # Get only the words in the span
        words = r_words.findall(span.group(0))
        joined = b" ".join(words).lower()
        if joined == HTTP_APACHE_LICENSE_HEADER.lower():
            return True, None
        elif joined == HTTPS_APACHE_LICENSE_HEADER.lower():
            return True, None
    return False, "Could not find Apache License header"


def _files_check_binary(root_path: safe.StatePath) -> Iterator[Result]:
    license_paths: list[pathlib.Path] = []
    notice_paths: list[pathlib.Path] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("._"))

        for filename in sorted(filenames):
            if filename.startswith("._"):
                continue

            if filename in _BINARY_LICENSE_FILENAMES:
                license_paths.append(pathlib.Path(dirpath) / filename)

            if filename in _BINARY_NOTICE_FILENAMES:
                notice_paths.append(pathlib.Path(dirpath) / filename)

    yield _files_check_binary_license(license_paths, root_path.path)
    yield _files_check_binary_notice(notice_paths, root_path.path)


def _files_check_binary_license(paths: list[pathlib.Path], root_path: pathlib.Path) -> ArtifactResult:
    if not paths:
        return ArtifactResult(
            status=sql.CheckResultStatus.BLOCKER,
            message="No LICENSE or LICENSE.txt file found",
            data=None,
        )
    for path in paths:
        if _files_check_core_logic_license(path) is None:
            rel_path = str(path.relative_to(root_path))
            return ArtifactResult(
                status=sql.CheckResultStatus.SUCCESS,
                message=f"{rel_path} is valid",
                data=None,
            )
    return ArtifactResult(
        status=sql.CheckResultStatus.FAILURE,
        message="No valid LICENSE or LICENSE.txt file found",
        data=None,
    )


def _files_check_binary_notice(paths: list[pathlib.Path], root_path: pathlib.Path) -> ArtifactResult:
    if not paths:
        return ArtifactResult(
            status=sql.CheckResultStatus.BLOCKER,
            message="No NOTICE or NOTICE.txt file found",
            data=None,
        )
    for path in paths:
        notice_ok, _, _ = _files_check_core_logic_notice(path)
        if notice_ok:
            rel_path = str(path.relative_to(root_path))
            return ArtifactResult(
                status=sql.CheckResultStatus.SUCCESS,
                message=f"{rel_path} is valid",
                data=None,
            )
    return ArtifactResult(
        status=sql.CheckResultStatus.FAILURE,
        message="No valid NOTICE or NOTICE.txt file found",
        data=None,
    )


def _files_check_core_logic(archive_dir: safe.StatePath, is_podling: bool, is_binary: bool) -> Iterator[Result]:
    if not archive_dir.path.is_dir():
        # Already protected by the caller
        # We add it here again to make unit testing cleaner
        yield ArtifactResult(
            status=sql.CheckResultStatus.FAILURE,
            message="Cache directory is not available",
            data=None,
        )
        return

    # Check for license files in the root directory
    top_entries = sorted(e for e in os.listdir(archive_dir) if not e.startswith("._"))
    root_dirs = [e for e in top_entries if (archive_dir / e).path.is_dir()]
    if len(root_dirs) != 1:
        yield ArtifactResult(
            status=sql.CheckResultStatus.FAILURE,
            message=f"Expected single root directory, found {len(root_dirs)}",
            data=None,
        )
        return

    root_path = archive_dir / root_dirs[0]
    if is_binary:
        yield from _files_check_binary(root_path)
    else:
        yield from _files_check_source(root_path, is_podling)


def _files_check_core_logic_license(file_path: pathlib.Path) -> str | None:
    """Verify that the start of the LICENSE file matches the Apache 2.0 license."""
    with open(file_path, "rb") as f:
        package_license_bytes = f.read(_MAX_LICENSE_NOTICE_SIZE + 1)
    if len(package_license_bytes) > _MAX_LICENSE_NOTICE_SIZE:
        log.warning(f"LICENSE file exceeds {_MAX_LICENSE_NOTICE_SIZE} byte limit: {file_path}")
        package_license_bytes = package_license_bytes[:_MAX_LICENSE_NOTICE_SIZE]

    sha3e = hashlib.sha3_256()
    sha3e.update(constants.APACHE_LICENSE_2_0.encode("utf-8"))
    sha3_expected = sha3e.hexdigest()

    if sha3_expected != "5efa4839f385df309ffc022ca5ce9763c4bc709dab862ca77d9a894db6598456":
        log.error("SHA3 expected value is incorrect, please update the static.LICENSE constant")

    package_license = package_license_bytes.decode("utf-8", errors="replace")

    # Some whitespace variations are permitted:
    # - Any form of leading or trailing whitespace
    # - Any increase or reduction in blank lines
    expected_lines = constants.APACHE_LICENSE_2_0.splitlines()
    actual_lines = package_license.splitlines()

    expected_lines = _normal_whitespace(expected_lines)
    actual_lines = _normal_whitespace(actual_lines)
    # Allow extra lines at the bottom of the license
    # This could invalidate the license, but we cannot check that automatically
    # if len(actual_lines) > len(expected_lines):
    actual_lines = actual_lines[: len(expected_lines)]
    if expected_lines != actual_lines:
        # TODO: Only show a contextual diff, not the full diff
        diff = difflib.ndiff(expected_lines, actual_lines)
        return "\n".join(diff)
    return None


def _files_check_core_logic_notice(file_path: pathlib.Path) -> tuple[bool, list[str], str]:
    """Verify that the NOTICE file follows the required format."""
    try:
        with open(file_path, "rb") as f:
            raw = f.read(_MAX_LICENSE_NOTICE_SIZE + 1)
        if len(raw) > _MAX_LICENSE_NOTICE_SIZE:
            log.warning(f"NOTICE file exceeds {_MAX_LICENSE_NOTICE_SIZE} byte limit: {file_path}")
            raw = raw[:_MAX_LICENSE_NOTICE_SIZE]
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, ["the NOTICE file is not valid UTF-8"], ""
    preamble = "".join(content.splitlines(keepends=True)[:3])
    issues = []

    normalised = re.sub(r"\s+", " ", content)
    if not re.search(r"Apache [\w\-\.]+", normalised):
        issues.append("missing or invalid Apache product header")
    if not re.search(r"Copyright (\d{4}|\d{4}-\d{4}) The Apache Software Foundation", normalised):
        issues.append("missing or invalid copyright statement")
    if not re.search(r"This product includes software developed at The Apache Software Foundation", normalised):
        issues.append("missing or invalid foundation attribution")

    return len(issues) == 0, issues, preamble


def _files_check_source(root_path: safe.StatePath, is_podling: bool) -> Iterator[Result]:
    license_results: dict[str, str | None] = {}
    notice_results: dict[str, tuple[bool, list[str], str]] = {}
    disclaimer_found = False

    for entry in sorted(os.listdir(root_path)):
        if entry.startswith("._"):
            continue

        # We explicitly escape the StatePath safety here as archives *can* contain prohibited files
        # TODO: Should they?
        entry_path = root_path.path / entry
        if not entry_path.is_file():
            continue

        if entry == "LICENSE":
            # TODO: Check length, should be 11,358 bytes
            license_diff = _files_check_core_logic_license(entry_path)
            license_results[entry] = license_diff
        elif entry == "NOTICE":
            # TODO: Check length doesn't exceed some preset
            notice_ok, notice_issues, notice_preamble = _files_check_core_logic_notice(entry_path)
            notice_results[entry] = (notice_ok, notice_issues, notice_preamble)
        elif entry in {"DISCLAIMER", "DISCLAIMER-WIP"}:
            disclaimer_found = True

    yield from _license_results(license_results)
    yield from _notice_results(notice_results)
    if is_podling and (not disclaimer_found):
        yield ArtifactResult(
            status=sql.CheckResultStatus.BLOCKER,
            message="No DISCLAIMER or DISCLAIMER-WIP file found",
            data=None,
        )


def _get_file_extension(filename: str) -> str | None:
    """Get the file extension without the dot."""
    _, ext = os.path.splitext(filename)
    if not ext:
        return None
    return ext[1:].lower()


def _headers_check_core_logic(  # noqa: C901
    archive_dir: safe.StatePath, artifact_basename: str, ignore_lines: list[str], excludes_source: str
) -> Iterator[Result]:
    """Verify Apache License headers in source files within an extracted cache tree."""
    # We could modify @Lucas-C/pre-commit-hooks instead for this
    # But hopefully this will be robust enough, at least for testing
    # First find and validate the root directory
    artifact_data = ArtifactData(excludes_source=excludes_source)

    # try:
    #     targz.root_directory(artifact_path)
    # except targz.RootDirectoryError as e:
    #     # Tooling believes that this should be a warning, not an error
    #     yield ArtifactResult(
    #         status=models.CheckResultStatus.WARNING,
    #         message=f"Could not determine root directory: {e!s}",
    #         data=None,
    #     )

    if not archive_dir.path.is_dir():
        yield ArtifactResult(
            status=sql.CheckResultStatus.FAILURE,
            message="Cache directory is not available",
            data=None,
        )
        return

    # log.info(f"Ignore lines: {ignore_lines}")

    for dirpath, dirnames, filenames in os.walk(archive_dir):
        dirnames.sort()
        for filename in sorted(filenames):
            if filename.startswith("._"):
                # Metadata convention
                continue

            file_path = pathlib.Path(dirpath) / filename
            rel_path = str(file_path.relative_to(archive_dir))

            ignore_path = "/" + artifact_basename + "/" + rel_path
            matcher = util.create_path_matcher(ignore_lines, pathlib.Path(ignore_path), pathlib.Path("/"))
            # log.info(f"Checking {ignore_path} with matcher {matcher}")
            if matcher(ignore_path):
                # log.info(f"Skipping {ignore_path} because it matches the ignore list")
                continue

            match _headers_check_core_logic_process_file(file_path, rel_path):
                case ArtifactResult() | MemberResult() as result:
                    artifact_data.files_checked += 1
                    match result.status:
                        case sql.CheckResultStatus.SUCCESS:
                            artifact_data.files_with_valid_headers += 1
                        case sql.CheckResultStatus.WARNING:
                            artifact_data.files_with_invalid_headers += 1
                        case sql.CheckResultStatus.FAILURE:
                            artifact_data.files_with_invalid_headers += 1
                        case sql.CheckResultStatus.BLOCKER:
                            artifact_data.files_with_invalid_headers += 1
                        case sql.CheckResultStatus.EXCEPTION:
                            artifact_data.files_with_invalid_headers += 1
                    yield result
                case MemberSkippedResult():
                    artifact_data.files_skipped += 1

    yield ArtifactResult(
        status=sql.CheckResultStatus.SUCCESS,
        message=f"Checked {util.plural(artifact_data.files_checked, 'file')},"
        f" found {artifact_data.files_with_valid_headers} with valid headers,"
        f" {artifact_data.files_with_invalid_headers} with invalid headers,"
        f" and {artifact_data.files_skipped} skipped",
        data=artifact_data.model_dump(),
    )


def _headers_check_core_logic_process_file(
    file_path: pathlib.Path,
    rel_path: str,
) -> Result:
    """Process a single file for license header verification."""
    if not file_path.is_file():
        return MemberSkippedResult(
            path=rel_path,
            reason="Not a file",
        )

    # Check if we should verify this file, based on extension
    if not _headers_check_core_logic_should_check(rel_path):
        return MemberSkippedResult(
            path=rel_path,
            reason="Not a source file",
        )

    # Read and check the file
    try:
        # Allow for some extra content at the start of the file
        # That may be shebangs, encoding declarations, etc.
        with open(file_path, "rb") as f:
            content = f.read(4096)
        is_valid, error = headers_validate(content, rel_path)
        if is_valid:
            return MemberResult(
                status=sql.CheckResultStatus.SUCCESS,
                path=rel_path,
                message="Valid license header",
                data=None,
            )
        else:
            return MemberResult(
                status=sql.CheckResultStatus.FAILURE,
                path=rel_path,
                message=f"Invalid license header: {error}",
                data=None,
            )
    except Exception as e:
        return MemberResult(
            status=sql.CheckResultStatus.EXCEPTION,
            path=rel_path,
            message=f"Error processing file: {e!s}",
            data=None,
        )


def _headers_check_core_logic_should_check(filepath: str) -> bool:
    """Determine whether a file should be checked for license headers."""
    if filepath.endswith(constants.GENERATED_FILE_SUFFIXES):
        return False

    ext = _get_file_extension(filepath)
    if ext is None:
        return False

    # Then check if the file matches any of our included patterns
    for pattern in INCLUDED_PATTERNS:
        if re.search(pattern, filepath, re.IGNORECASE):
            return True

    return False


async def _headers_core(
    recorder: checks.Recorder,
    archive_dir: safe.StatePath,
    artifact_basename: str,
    ignore_lines: list[str],
    excludes_source: str,
) -> None:
    try:
        for result in await asyncio.to_thread(
            _headers_check_core_logic, archive_dir, artifact_basename, ignore_lines, excludes_source
        ):
            match result:
                case ArtifactResult():
                    await _record_artifact(recorder, result)
                case MemberResult():
                    await _record_member(recorder, result)
                case MemberSkippedResult():
                    pass
        member_failures = recorder.member_problems.get(sql.CheckResultStatus.FAILURE, 0)
        if member_failures > 0:
            await recorder.failure(
                f"Some files had invalid license headers ({member_failures} failures)",
                None,
            )

    except Exception as e:
        await recorder.exception("Error during license header check execution", {"error": str(e)})

    return None


def _license_results(
    license_results: dict[str, str | None],
) -> Iterator[Result]:
    """Build status messages for license file verification."""
    license_files_size = len(license_results)
    if license_files_size == 0:
        yield ArtifactResult(
            status=sql.CheckResultStatus.BLOCKER,
            message="No LICENSE file found",
            data=None,
        )
        return

    if license_files_size > 1:
        yield ArtifactResult(
            status=sql.CheckResultStatus.FAILURE,
            message="Multiple LICENSE files found",
            data=None,
        )
        return

    for filename, license_diff in license_results.items():
        # Unpack the single result by iterating
        if license_diff is None:
            yield ArtifactResult(
                status=sql.CheckResultStatus.SUCCESS,
                message=f"{filename} is valid",
                data=None,
            )
        else:
            yield ArtifactResult(
                status=sql.CheckResultStatus.FAILURE,
                message=f"{filename} is invalid",
                data={"diff": license_diff},
            )


def _normal_whitespace(lines: list[str]) -> list[str]:
    result = []
    for line in lines:
        line = line.strip()
        if line:
            result.append(line)
    return result


def _notice_results(
    notice_results: dict[str, tuple[bool, list[str], str]],
) -> Iterator[Result]:
    """Build status messages for notice file verification."""
    notice_files_size = len(notice_results)
    if notice_files_size == 0:
        yield ArtifactResult(
            status=sql.CheckResultStatus.BLOCKER,
            message="No NOTICE file found",
            data=None,
        )
        return

    if notice_files_size > 1:
        yield ArtifactResult(
            status=sql.CheckResultStatus.FAILURE,
            message="Multiple NOTICE files found",
            data=None,
        )
        return

    for filename, (notice_ok, notice_issues, notice_preamble) in notice_results.items():
        # Unpack the single result by iterating
        if notice_ok:
            yield ArtifactResult(
                status=sql.CheckResultStatus.SUCCESS,
                message=f"{filename} is valid",
                data=None,
            )
        else:
            yield ArtifactResult(
                status=sql.CheckResultStatus.FAILURE,
                message=f"{filename} is invalid",
                data={"issues": notice_issues, "preamble": notice_preamble},
            )


async def _record_artifact(recorder: checks.Recorder, result: ArtifactResult) -> None:
    match result.status:
        case sql.CheckResultStatus.SUCCESS:
            await recorder.success(result.message, result.data)
        case sql.CheckResultStatus.WARNING:
            await recorder.warning(result.message, result.data)
        case sql.CheckResultStatus.FAILURE:
            await recorder.failure(result.message, result.data)
        case sql.CheckResultStatus.BLOCKER:
            await recorder.blocker(result.message, result.data)
        case sql.CheckResultStatus.EXCEPTION:
            await recorder.exception(result.message, result.data)


async def _record_member(recorder: checks.Recorder, result: MemberResult) -> None:
    match result.status:
        case sql.CheckResultStatus.SUCCESS:
            await recorder.success(result.message, result.data, member_rel_path=result.path)
        case sql.CheckResultStatus.WARNING:
            await recorder.warning(result.message, result.data, member_rel_path=result.path)
        case sql.CheckResultStatus.FAILURE:
            await recorder.failure(result.message, result.data, member_rel_path=result.path)
        case sql.CheckResultStatus.BLOCKER:
            await recorder.blocker(result.message, result.data, member_rel_path=result.path)
        case sql.CheckResultStatus.EXCEPTION:
            await recorder.exception(result.message, result.data, member_rel_path=result.path)
