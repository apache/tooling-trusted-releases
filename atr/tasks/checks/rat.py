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
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
from typing import Final

import atr.archives as archives
import atr.config as config
import atr.constants as constants
import atr.log as log
import atr.models.checkdata as checkdata
import atr.models.results as results
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.util as util

_CONFIG: Final = config.get()
_JAVA_MEMORY_ARGS: Final[list[str]] = []
# Use this to set smaller memory limits and use SerialGC which also requires less memory
# We prefer, however, to set this in the container
# _JAVA_MEMORY_ARGS: Final[list[str]] = [
#     "-XX:MaxMetaspaceSize=32m",
#     "-Xmx128m",
#     "-XX:+UseSerialGC",
#     "-XX:MaxRAM=256m",
#     "-XX:CompressedClassSpaceSize=16m"
# ]

# Generated file patterns, always excluded
_GENERATED_FILE_PATTERNS: Final[list[str]] = [f"**/*{s}" for s in constants.GENERATED_FILE_SUFFIXES]

# The name of the temp file for excludes defined in release policies
_POLICY_EXCLUDES_FILENAME: Final[str] = ".atr-policy-rat-excludes"

# The name of the file that contains the exclusions for the specified archive
_RAT_EXCLUDES_FILENAME: Final[str] = ".rat-excludes"

# The name of the RAT report file
_RAT_REPORT_FILENAME: Final[str] = ".atr-rat-report.xml"

# Standard exclusions, always applied explicitly
_STD_EXCLUSIONS_ALWAYS: Final[list[str]] = ["MISC", "HIDDEN_DIR", "MAC"]

# Additional exclusions when no project exclusion file is found
_STD_EXCLUSIONS_EXTENDED: Final[list[str]] = [
    "MAVEN",
    "ECLIPSE",
    "IDEA",
    "GIT",
    "STANDARD_SCMS",
]


class RatError(RuntimeError):
    pass


async def check(args: checks.FunctionArguments) -> results.Results | None:
    """Use Apache RAT to check the licenses of the files in the artifact."""
    recorder = await args.recorder()
    if not (artifact_abs_path := await recorder.abs_path()):
        return None
    if await recorder.primary_path_is_binary():
        log.info(f"Skipping RAT check for binary artifact {artifact_abs_path} (rel: {args.primary_rel_path})")
        return None

    project = await recorder.project()
    if project.policy_license_check_mode == sql.LicenseCheckMode.LIGHTWEIGHT:
        log.info(f"Skipping RAT check for {artifact_abs_path} (mode is LIGHTWEIGHT)")
        return None

    if await recorder.check_cache(artifact_abs_path):
        log.info(f"Using cached RAT result for {artifact_abs_path} (rel: {args.primary_rel_path})")
        return None

    log.info(f"Checking RAT licenses for {artifact_abs_path} (rel: {args.primary_rel_path})")

    is_source = await recorder.primary_path_is_source()
    policy_excludes = project.policy_source_excludes_rat if is_source else []

    try:
        await _check_core(args, recorder, artifact_abs_path, policy_excludes)
    except Exception as e:
        # TODO: Or bubble for task failure?
        await recorder.failure("Error running Apache RAT check", {"error": str(e)})

    return None


def _build_rat_command(
    rat_jar_path: str,
    xml_output_path: str,
    excludes_file: str | None,
    apply_extended_std: bool,
) -> list[str]:
    """Build the RAT command with appropriate exclusions."""
    command = [
        "java",
        *_JAVA_MEMORY_ARGS,
        "-jar",
        rat_jar_path,
        "--output-style",
        "xml",
        "--output-file",
        xml_output_path,
        "--counter-max",
        "UNAPPROVED:-1",
        "--counter-min",
        "LICENSE_CATEGORIES:0",
        "LICENSE_NAMES:0",
        "STANDARDS:0",
    ]

    for std in _STD_EXCLUSIONS_ALWAYS:
        command.extend(["--input-exclude-std", std])

    if apply_extended_std:
        for std in _STD_EXCLUSIONS_EXTENDED:
            command.extend(["--input-exclude-std", std])

    for pattern in _GENERATED_FILE_PATTERNS:
        command.extend(["--input-exclude", pattern])

    # Exclude the output just in case
    # TODO: Check whether this file exists in the archive
    command.extend(["--input-exclude", _RAT_REPORT_FILENAME])

    if excludes_file is not None:
        command.extend(["--input-exclude", excludes_file])
        command.extend(["--input-exclude-file", excludes_file])

    command.extend(["--", "."])

    return command


async def _check_core(
    args: checks.FunctionArguments,
    recorder: checks.Recorder,
    artifact_abs_path: pathlib.Path,
    policy_excludes: list[str],
) -> None:
    result = await asyncio.to_thread(
        _synchronous,
        artifact_path=str(artifact_abs_path),
        policy_excludes=policy_excludes,
        rat_jar_path=args.extra_args.get("rat_jar_path", _CONFIG.APACHE_RAT_JAR_PATH),
        max_extract_size=args.extra_args.get("max_extract_size", _CONFIG.MAX_EXTRACT_SIZE),
        chunk_size=args.extra_args.get("chunk_size", _CONFIG.EXTRACT_CHUNK_SIZE),
    )

    # Record individual file failures before the overall result
    for file in result.unknown_license_files:
        await recorder.failure("Unknown license", None, member_rel_path=file.name)
    for file in result.unapproved_files:
        await recorder.failure("Unapproved license", {"license": file.license}, member_rel_path=file.name)

    # Convert to dict for storage, excluding the file lists, which are already recorded
    result_data = result.model_dump(exclude={"unapproved_files", "unknown_license_files"})

    if result.warning:
        await recorder.warning(result.warning, result_data)
    elif (not result.valid) or result.errors:
        await recorder.failure(result.message, result_data)
    else:
        await recorder.success(result.message, result_data)


def _check_core_logic_execute_rat(
    command: list[str],
    scan_root: str,
    temp_dir: str,
    xml_output_path: str,
) -> tuple[checkdata.Rat | None, str | None]:
    """Execute Apache RAT and process its output."""
    # Change working directory to scan_root when running the process
    current_dir = os.getcwd()
    os.chdir(scan_root)

    log.info(f"Executing Apache RAT from directory: {os.getcwd()}")

    try:
        # Run the actual RAT command
        # We do check=False because we'll handle errors below
        # The timeout is five minutes
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

        if process.returncode != 0:
            log.error(f"Apache RAT failed with return code {process.returncode}")
            log.error(f"STDOUT: {process.stdout}")
            log.error(f"STDERR: {process.stderr}")
            os.chdir(current_dir)
            return checkdata.Rat(
                message=f"Apache RAT process failed with code {process.returncode}",
                errors=[
                    f"Process error code: {process.returncode}",
                    f"STDOUT: {process.stdout}",
                    f"STDERR: {process.stderr}",
                ],
            ), None

        log.info(f"Apache RAT completed successfully with return code {process.returncode}")
        log.info(f"stdout: {process.stdout[:200]}...")
    except subprocess.TimeoutExpired as e:
        os.chdir(current_dir)
        log.error(f"Apache RAT process timed out: {e}")
        return checkdata.Rat(
            message="Apache RAT process timed out",
            errors=[f"Timeout: {e}"],
        ), None
    except Exception as e:
        # Change back to the original directory before raising
        os.chdir(current_dir)
        log.error(f"Exception running Apache RAT: {e}")
        return checkdata.Rat(
            message=f"Apache RAT process failed: {e}",
            errors=[f"Process error: {e}"],
        ), None

    # Change back to the original directory
    os.chdir(current_dir)

    # Check that the output file exists
    if not os.path.exists(xml_output_path):
        log.error(f"XML output file not found at: {xml_output_path}")
        # List files in the temporary directory
        log.info(f"Files in {temp_dir}: {os.listdir(temp_dir)}")
        # Look in the current directory too
        log.info(f"Files in current directory: {os.listdir('.')}")
        return checkdata.Rat(
            message=f"RAT output XML file not found: {xml_output_path}",
            errors=[f"Missing output file: {xml_output_path}"],
        ), None

    # The XML was found correctly
    log.info(f"Found XML output at: {xml_output_path} (size: {os.path.getsize(xml_output_path)} bytes)")
    return None, xml_output_path


def _count_files_outside_directory(temp_dir: str, scan_root: str) -> int:
    """Count regular files that exist outside the scan_root directory."""
    count = 0
    scan_root_rel = os.path.relpath(scan_root, temp_dir)
    if scan_root_rel == ".":
        scan_root_rel = ""

    for root, _dirs, files in os.walk(temp_dir):
        rel_root = os.path.relpath(root, temp_dir)
        if rel_root == ".":
            rel_root = ""

        if _is_inside_directory(rel_root, scan_root_rel):
            continue

        # for filename in files:
        #     if not filename.startswith("."):
        #         count += 1
        count += len(files)

    return count


def _get_command_and_xml_output_path(
    temp_dir: str, excludes_file_path: str | None, apply_extended_std: bool, scan_root: str, rat_jar_path: str
) -> tuple[list[str], str]:
    xml_output_path = os.path.join(temp_dir, _RAT_REPORT_FILENAME)
    log.info(f"XML output will be written to: {xml_output_path}")

    # Convert exclusion file path from temp_dir relative to scan_root relative
    excludes_file: str | None = None
    if excludes_file_path is not None:
        abs_path = os.path.join(temp_dir, excludes_file_path)
        if not (os.path.exists(abs_path) and os.path.isfile(abs_path)):
            log.error(f"Exclusion file not found or not a regular file: {abs_path}")
            raise RatError(f"Exclusion file is not a regular file: {excludes_file_path}({abs_path})")
        excludes_file = os.path.relpath(abs_path, scan_root)
        log.info(f"Using exclusion file: {excludes_file}")
    command = _build_rat_command(rat_jar_path, xml_output_path, excludes_file, apply_extended_std)
    log.info(f"Running Apache RAT: {' '.join(command)}")
    return command, xml_output_path


def _is_inside_directory(path: str, directory: str) -> bool:
    """Check whether path is inside directory, or is the directory itself."""
    if directory == "":
        return True
    if path == directory:
        return True
    return path.startswith(directory + os.sep)


def _sanitise_command_for_storage(command: list[str]) -> list[str]:
    """Replace absolute paths with filenames for known arguments."""
    path_args = {"-jar", "--output-file"}
    result: list[str] = []
    for i, arg in enumerate(command):
        if (i > 0) and (command[i - 1] in path_args) and os.path.isabs(arg):
            result.append(os.path.basename(arg))
        else:
            result.append(arg)
    return result


def _summary_message(valid: bool, unapproved_licenses: int, unknown_licenses: int) -> str:
    message = "All files have approved licenses"
    if not valid:
        message = "Found "
        if unapproved_licenses > 0:
            message += f"{util.plural(unapproved_licenses, 'file')} with unapproved licenses"
            if unknown_licenses > 0:
                message += " and "
        if unknown_licenses > 0:
            message += f"{util.plural(unknown_licenses, 'file')} with unknown licenses"
    return message


def _synchronous(
    artifact_path: str,
    policy_excludes: list[str],
    rat_jar_path: str = _CONFIG.APACHE_RAT_JAR_PATH,
    max_extract_size: int = _CONFIG.MAX_EXTRACT_SIZE,
    chunk_size: int = _CONFIG.EXTRACT_CHUNK_SIZE,
) -> checkdata.Rat:
    """Verify license headers using Apache RAT."""
    log.info(f"Verifying licenses with Apache RAT for {artifact_path}")
    log.info(f"PATH environment variable: {os.environ.get('PATH', 'PATH not found')}")

    java_check = _synchronous_check_java_installed()
    if java_check is not None:
        return java_check

    # Verify RAT JAR exists and is accessible
    rat_jar_path, jar_error = _synchronous_check_jar_exists(rat_jar_path)
    if jar_error:
        return jar_error

    try:
        # Create a temporary directory for extraction
        # TODO: We could extract to somewhere in "state/" instead
        with tempfile.TemporaryDirectory(prefix="rat_verify_") as temp_dir:
            log.info(f"Created temporary directory: {temp_dir}")
            return _synchronous_extract(
                artifact_path, temp_dir, max_extract_size, chunk_size, policy_excludes, rat_jar_path
            )
    except Exception as e:
        import traceback

        log.exception("Error running Apache RAT")
        return checkdata.Rat(
            message=f"Failed to run Apache RAT: {e!s}",
            errors=[str(e), traceback.format_exc()],
        )


def _synchronous_check_jar_exists(rat_jar_path: str) -> tuple[str, checkdata.Rat | None]:
    """Verify that the Apache RAT JAR file exists and is accessible."""
    # Check that the RAT JAR exists
    if not os.path.exists(rat_jar_path):
        log.error(f"Apache RAT JAR not found at: {rat_jar_path}")
        # Try a few common locations:
        # ./rat.jar
        # ./state/rat.jar
        # ../rat.jar
        # ../state/rat.jar
        # NOTE: We're also doing something like this in task_verify_rat_license
        # Should probably decide one place to do it, and do it well
        alternative_paths = [
            os.path.join(os.getcwd(), os.path.basename(rat_jar_path)),
            os.path.join(os.getcwd(), "state", os.path.basename(rat_jar_path)),
            os.path.join(os.path.dirname(os.getcwd()), os.path.basename(rat_jar_path)),
            os.path.join(os.path.dirname(os.getcwd()), "state", os.path.basename(rat_jar_path)),
        ]

        for alt_path in alternative_paths:
            if os.path.exists(alt_path):
                log.info(f"Found alternative RAT JAR at: {alt_path}")
                rat_jar_path = alt_path
                break

        # Double check whether we found the JAR
        if not os.path.exists(rat_jar_path):
            log.error("Tried alternative paths but Apache RAT JAR still not found")
            log.error(f"Current directory: {os.getcwd()}")
            log.error(f"Directory contents: {os.listdir(os.getcwd())}")
            if os.path.exists("state"):
                log.error(f"State directory contents: {os.listdir('state')}")

            return rat_jar_path, checkdata.Rat(
                message=f"Apache RAT JAR not found at: {rat_jar_path}",
                errors=[f"Missing JAR: {rat_jar_path}"],
            )
    else:
        log.info(f"Found Apache RAT JAR at: {rat_jar_path}")

    return rat_jar_path, None


def _synchronous_check_java_installed() -> checkdata.Rat | None:
    # Check that Java is installed
    # TODO: Run this only once, when the server starts
    try:
        java_version = subprocess.check_output(
            ["java", *_JAVA_MEMORY_ARGS, "-version"], stderr=subprocess.STDOUT, text=True
        )
        log.info(f"Java version: {java_version.splitlines()[0]}")
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        log.error(f"Java is not properly installed or not in PATH: {e}")

        # Try to get some output even if the command failed
        try:
            # Use run instead of check_output to avoid exceptions
            java_result = subprocess.run(
                ["java", *_JAVA_MEMORY_ARGS, "-version"],
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
                text=True,
                check=False,
            )
            log.info(f"Java command return code: {java_result.returncode}")
            log.info(f"Java command output: {java_result.stdout or java_result.stderr}")

            # Try to find where Java might be located
            which_java = subprocess.run(["which", "java"], capture_output=True, text=True, check=False)
            which_java_result = which_java.stdout.strip() if (which_java.returncode == 0) else "not found"
            log.info(f"Result for which java: {which_java_result}")
        except Exception as inner_e:
            log.error(f"Additional error while trying to debug java: {inner_e}")

        return checkdata.Rat(
            message="Java is not properly installed or not in PATH",
            errors=[f"Java error: {e}"],
        )


def _synchronous_extract(
    artifact_path: str,
    temp_dir: str,
    max_extract_size: int,
    chunk_size: int,
    policy_excludes: list[str],
    rat_jar_path: str,
) -> checkdata.Rat:
    # Extract the archive to the temporary directory
    log.info(f"Extracting {artifact_path} to {temp_dir}")
    extracted_size, exclude_file_paths = archives.extract(
        artifact_path,
        temp_dir,
        max_size=max_extract_size,
        chunk_size=chunk_size,
        track_files={_RAT_EXCLUDES_FILENAME},
    )
    log.info(f"Extracted {extracted_size} bytes")
    log.info(f"Found {len(exclude_file_paths)} {_RAT_EXCLUDES_FILENAME} file(s): {exclude_file_paths}")

    # Validate that we found at most one exclusion file
    if len(exclude_file_paths) > 1:
        log.error(f"Multiple {_RAT_EXCLUDES_FILENAME} files found: {exclude_file_paths}")
        return checkdata.Rat(
            message=f"Multiple {_RAT_EXCLUDES_FILENAME} files not allowed (found {len(exclude_file_paths)})",
            errors=[f"Found {len(exclude_file_paths)} {_RAT_EXCLUDES_FILENAME} files"],
        )

    # Narrow to single path after validation
    archive_excludes_path: str | None = exclude_file_paths[0] if exclude_file_paths else None

    excludes_source, effective_excludes_path = _synchronous_extract_excludes_source(
        archive_excludes_path, policy_excludes, temp_dir
    )

    try:
        scan_root = _synchronous_extract_scan_root(archive_excludes_path, temp_dir)
    except RatError as e:
        return checkdata.Rat(
            message=f"Failed to determine scan root: {e}",
            errors=[str(e)],
            excludes_source=excludes_source,
        )

    # Execute RAT and get results or error
    # Extended std exclusions apply when there's no archive .rat-excludes
    apply_extended_std = excludes_source != "archive"
    try:
        command, xml_output_path = _get_command_and_xml_output_path(
            temp_dir, effective_excludes_path, apply_extended_std, scan_root, rat_jar_path
        )
    except RatError as e:
        return checkdata.Rat(
            message=f"Failed to build RAT command: {e}",
            errors=[str(e)],
        )
    error_result, xml_output_path = _check_core_logic_execute_rat(command, scan_root, temp_dir, xml_output_path)
    if error_result is not None:
        return error_result

    # Parse the XML output
    log.info(f"Parsing RAT XML output: {xml_output_path}")
    # Make sure xml_output_path is not None before parsing
    if xml_output_path is None:
        raise ValueError("XML output path is None")

    result = _synchronous_extract_parse_output(xml_output_path, scan_root)
    log.info(f"Successfully parsed RAT output with {util.plural(result.total_files, 'file')}")

    # The unknown_license_files and unapproved_files contain FileEntry objects
    # The path is relative to scan_root, so we prepend the scan_root relative path
    scan_root_rel = os.path.relpath(scan_root, temp_dir)
    if scan_root_rel != ".":
        for file in result.unknown_license_files:
            file.name = os.path.join(scan_root_rel, os.path.normpath(file.name))
        for file in result.unapproved_files:
            file.name = os.path.join(scan_root_rel, os.path.normpath(file.name))

    result.excludes_source = excludes_source
    result.extended_std_applied = apply_extended_std
    result.command = _sanitise_command_for_storage(command)
    return result


def _synchronous_extract_excludes_source(
    archive_excludes_path: str | None, policy_excludes: list[str], temp_dir: str
) -> tuple[str, str | None]:
    # Determine excludes_source and effective excludes file
    excludes_source: str
    effective_excludes_path: str | None

    if archive_excludes_path is not None:
        excludes_source = "archive"
        effective_excludes_path = archive_excludes_path
        log.info(f"Using archive {_RAT_EXCLUDES_FILENAME}: {archive_excludes_path}")
    elif policy_excludes:
        excludes_source = "policy"
        policy_excludes_file = os.path.join(temp_dir, _POLICY_EXCLUDES_FILENAME)
        with open(policy_excludes_file, "w") as f:
            f.write("\n".join(policy_excludes))
        effective_excludes_path = os.path.relpath(policy_excludes_file, temp_dir)
        log.info(f"Using policy excludes written to: {policy_excludes_file}")
    else:
        excludes_source = "none"
        effective_excludes_path = None
        log.info("No excludes: using defaults only")
    return excludes_source, effective_excludes_path


def _synchronous_extract_parse_output(xml_file: str, base_dir: str) -> checkdata.Rat:
    """Parse the XML output from Apache RAT safely."""
    try:
        return _synchronous_extract_parse_output_core(xml_file, base_dir)
    except Exception as e:
        log.error(f"Error parsing RAT output: {e}")
        return checkdata.Rat(
            message=f"Failed to parse Apache RAT output: {e!s}",
            errors=[f"XML parsing error: {e!s}"],
        )


def _synchronous_extract_parse_output_core(xml_file: str, base_dir: str) -> checkdata.Rat:
    """Parse the XML output from Apache RAT."""
    tree = ElementTree.parse(xml_file)
    root = tree.getroot()

    total_files = 0
    approved_licenses = 0
    unapproved_licenses = 0
    unknown_licenses = 0

    unapproved_files: list[checkdata.RatFileEntry] = []
    unknown_license_files: list[checkdata.RatFileEntry] = []

    # Process each resource
    for resource in root.findall(".//resource"):
        total_files += 1

        # Get the name attribute value
        name = resource.get("name", "")

        # Remove base_dir prefix for cleaner display
        if name.startswith(base_dir):
            name = name[len(base_dir) :].lstrip("/")

        # Get license information
        license_elem = resource.find("license")

        if license_elem is None:
            resource_type = resource.get("type", "")
            if resource_type in {"NOTICE", "BINARY", "IGNORED", "ARCHIVE"}:
                approved_licenses += 1
            else:
                unknown_licenses += 1
                unknown_license_files.append(checkdata.RatFileEntry(name=name, license="Unknown license"))
        else:
            approval = license_elem.get("approval", "false")
            is_approved = approval == "true"
            license_name = license_elem.get("name", "Unknown")

            if is_approved:
                approved_licenses += 1
            elif license_name == "Unknown license":
                unknown_licenses += 1
                unknown_license_files.append(checkdata.RatFileEntry(name=name, license=license_name))
            else:
                unapproved_licenses += 1
                unapproved_files.append(checkdata.RatFileEntry(name=name, license=license_name))

    # Calculate overall validity
    valid = (unapproved_licenses == 0) and (unknown_licenses == 0)

    # Prepare a summary message of just the right length
    message = _summary_message(valid, unapproved_licenses, unknown_licenses)

    # We limit the number of files we report to 100
    return checkdata.Rat(
        valid=valid,
        message=message,
        total_files=total_files,
        approved_licenses=approved_licenses,
        unapproved_licenses=unapproved_licenses,
        unknown_licenses=unknown_licenses,
        unapproved_files=unapproved_files[:100],
        unknown_license_files=unknown_license_files[:100],
    )


def _synchronous_extract_scan_root(archive_excludes_path: str | None, temp_dir: str) -> str:
    # Determine scan root based on archive .rat-excludes location
    if archive_excludes_path is not None:
        scan_root = os.path.dirname(os.path.join(temp_dir, archive_excludes_path))

        # Verify that scan_root is inside temp_dir
        abs_scan_root = os.path.abspath(scan_root)
        abs_temp_dir = os.path.abspath(temp_dir)
        scan_root_is_inside = (abs_scan_root == abs_temp_dir) or abs_scan_root.startswith(abs_temp_dir + os.sep)
        if not scan_root_is_inside:
            log.error(f"Scan root {scan_root} is outside temp_dir {temp_dir}")
            raise RatError("Invalid archive structure: exclusion file path escapes extraction directory")

        log.info(f"Using {_RAT_EXCLUDES_FILENAME} directory as scan root: {scan_root}")

        untracked_count = _count_files_outside_directory(temp_dir, scan_root)
        if untracked_count > 0:
            log.error(f"Found {untracked_count} file(s) outside {_RAT_EXCLUDES_FILENAME} directory")
            raise RatError(f"Files exist outside {_RAT_EXCLUDES_FILENAME} directory ({untracked_count} found)")
    else:
        scan_root = temp_dir
        log.info(f"No archive {_RAT_EXCLUDES_FILENAME} found, using temp_dir as scan root: {scan_root}")

    return scan_root
