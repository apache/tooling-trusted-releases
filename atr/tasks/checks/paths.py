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
import atr.attestable as attestable
import atr.classify as classify
import atr.construct as construct
import atr.db as db
import atr.log as log
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.user as user
import atr.util as util

_ALLOWED_TOP_LEVEL_NAMES: Final = ("CHANGES", "LICENSE", "NOTICE", "README", "SECURITY")
_ALLOWED_TOP_LEVEL_SUFFIXES: Final = ("", ".adoc", ".md", ".rst", ".txt")
_ALLOWED_TOP_LEVEL: Final = frozenset(
    (name + suffix) for name in _ALLOWED_TOP_LEVEL_NAMES for suffix in _ALLOWED_TOP_LEVEL_SUFFIXES
)
_DOC_TREE_MAX_FILES: Final = 512
# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = ["binary_artifact_paths", "source_artifact_paths", "download_path_suffix"]
INPUT_EXTRA_ARGS: Final[list[str]] = ["is_podling", "all_files"]
CHECK_VERSION: Final[str] = "7"


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

    recorder_problems = await checks.Recorder.create(
        checker=checks.function_key(check) + "_errors",
        checker_version=CHECK_VERSION,
        inputs_hash=base_recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=None,
        afresh=True,
    )
    recorder_suggestions = await checks.Recorder.create(
        checker=checks.function_key(check) + "_warnings",
        checker_version=CHECK_VERSION,
        inputs_hash=base_recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=None,
        afresh=True,
    )
    recorder_notes = await checks.Recorder.create(
        checker=checks.function_key(check) + "_success",
        checker_version=CHECK_VERSION,
        inputs_hash=base_recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=None,
        afresh=True,
    )
    recorder_source = await checks.Recorder.create(
        checker=checks.function_key(check) + "_source",
        checker_version=CHECK_VERSION,
        inputs_hash=base_recorder.input_hash or "",
        project_key=args.project_key,
        version_key=args.version_key,
        revision_number=args.revision_number,
        primary_rel_path=None,
        afresh=True,
    )

    # As primary_rel_path is None, the base path is the release candidate draft directory
    if not (base_path := await recorder_notes.abs_path()):
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
            recorder_problems,
            recorder_suggestions,
            recorder_notes,
            relative_paths_set,
            is_podling,
        )

    await _check_source_artifact_present(args, recorder_source, relative_paths, base_path)
    await _check_documentation_tree(recorder_problems, relative_paths)
    await _check_download_suffix_duplication(args, recorder_suggestions, relative_paths)

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


async def _check_documentation_tree(
    recorder_problems: checks.Recorder,
    relative_paths: list[safe.RelPath],
) -> None:
    bundled = [path for path in relative_paths if _is_bundled_doc(path)]
    if len(bundled) <= _DOC_TREE_MAX_FILES:
        return
    await recorder_problems.blocker(
        f"Release bundles {len(bundled)} files, which exceeds the limit of {_DOC_TREE_MAX_FILES}. "
        "If these files are documentation, please publish them to the project website instead.",
        {
            "doc_file_count": len(bundled),
            "limit": _DOC_TREE_MAX_FILES,
            "examples": [str(path) for path in bundled[:5]],
        },
        primary_rel_path=None,
    )


async def _check_download_suffix_duplication(
    args: checks.FunctionArguments,
    recorder_suggestions: checks.Recorder,
    relative_paths: list[safe.RelPath],
) -> None:
    # A file whose first path component repeats a component of the policy's resolved download path
    # suffix doubles that component in the published path - the suffix maven-3/3.10.0-rc-1 plus a
    # 3.10.0-rc-1/... rel path lands at .../3.10.0-rc-1/3.10.0-rc-1/... Warn so the redundant top
    # directory (or the {{VERSION}} in the template) gets dropped before the release ships
    async with db.session() as data:
        release = await data.release(
            key=str(sql.release_key(args.project_key, args.version_key)),
            _committee=True,
            _project_release_policy=True,
        ).demand(RuntimeError(f"Release {args.project_key} {args.version_key} not found"))

    committee = release.committee
    suffix = construct.resolve_download_path_suffix(
        template=release.project.policy_download_path_suffix,
        project_key=release.project.key,
        version=str(release.version),
        is_top_level=(committee is not None) and (release.project.key == committee.key),
    )
    if suffix is None:
        return

    suffix_components = frozenset(suffix.as_path().parts)
    clashes: dict[str, int] = {}
    for relative_path in relative_paths:
        parts = relative_path.as_path().parts
        if parts and (parts[0] in suffix_components):
            clashes[parts[0]] = clashes.get(parts[0], 0) + 1

    for component in sorted(clashes):
        await recorder_suggestions.suggestion(
            f"{clashes[component]} file(s) start with '{component}/', which the download path suffix"
            " already contains. Consider removing this prefix.",
            {
                "component": component,
                "suffix": str(suffix),
                "count": clashes[component],
                "details": (
                    f"The published path would repeat '{component}': the suffix '{suffix}' plus a "
                    f"'{component}/...' file lands at '.../{component}/{component}/...'. Consider dropping the "
                    f"top-level '{component}/' directory from the release, or remove that component from the download "
                    "path suffix when publishing."
                ),
            },
            primary_rel_path=None,
        )


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
        warnings.append("The use of .sha1 is deprecated, please use .sha512")
    if ".sha" in suffixes:
        # Discouraged by RDP
        warnings.append("The use of .sha is discouraged, please use .sha512")
    if ".sig" in suffixes:
        # Forbidden by RCP, forbidden by RDP
        blockers.append("Binary signature files (.sig) are forbidden, please use .asc")

    # "Signature and checksum files for verifying distributed artifacts should
    # not be provided, unless named as indicated above." (RDP)
    # Also .mds is allowed, but we'll ignore that for now
    # TODO: Is .mds supported in analysis.METADATA_SUFFIXES?
    is_cyclonedx_sbom = analysis.is_cyclonedx(relative_path.as_path().name)
    if (ext_metadata not in {".asc", ".sha256", ".sha512", ".md5", ".sha", ".sha1"}) and (not is_cyclonedx_sbom):
        warnings.append("The use of this metadata file is discouraged")

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
    recorder_problems: checks.Recorder,
    recorder_suggestions: checks.Recorder,
    recorder_notes: checks.Recorder,
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
    if path.name in analysis.DISALLOWED_FILENAMES:
        blocker_text = (
            "The KEYS file should be uploaded via the 'Keys' section, not included in the artifact bundle"
            if path.name == "KEYS"
            else f"Disallowed file: {path.name}"
        )
        await _record(
            recorder_problems,
            recorder_suggestions,
            recorder_notes,
            relative_path,
            errors,
            [blocker_text],
            warnings,
        )
        return
    elif path.suffix in analysis.DISALLOWED_SUFFIXES:
        await _record(
            recorder_problems,
            recorder_suggestions,
            recorder_notes,
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
    if (not ext_artifact) and (not ext_metadata) and analysis.is_sbom_metadata(path.name):
        ext_metadata = path.suffix

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
        recorder_problems,
        recorder_suggestions,
        recorder_notes,
        relative_path,
        errors,
        blockers,
        warnings,
    )


async def _check_source_artifact_present(
    args: checks.FunctionArguments,
    recorder_source: checks.Recorder,
    relative_paths: list[safe.RelPath],
    base_path: safe.StatePath,
) -> None:
    release_key_str = str(sql.release_key(args.project_key, args.version_key))
    revision_seq = int(str(args.revision_number))
    async with db.session() as data:
        classifications = await data.release_file_classifications_at(release_key_str, revision_seq)

    missing_paths = [p for p in relative_paths if str(p) not in classifications]
    if missing_paths:
        attestable_data = await attestable.load(args.project_key, args.version_key, args.revision_number)
        if attestable_data is not None:
            policy = attestable_data.policy or {}
            source_matcher, binary_matcher = classify.matchers_from_policy(
                policy.get("source_artifact_paths", []),
                policy.get("binary_artifact_paths", []),
                base_path,
            )
            for path in missing_paths:
                path_str = str(path)
                cls = attestable.path_classification(attestable_data, path_str)
                if cls is not None:
                    classifications[path_str] = cls
                else:
                    classifications[path_str] = (
                        await classify.classify(
                            path, base_path, source_matcher=source_matcher, binary_matcher=binary_matcher
                        )
                    ).value
        else:
            async with db.session() as data:
                project = await data.project(key=str(args.project_key), _release_policy=True).demand(
                    RuntimeError(f"Project {args.project_key} not found")
                )
            source_matcher, binary_matcher = classify.matchers_from_policy(
                project.policy_source_artifact_paths,
                project.policy_binary_artifact_paths,
                base_path,
            )
            for path in missing_paths:
                classifications[str(path)] = (
                    await classify.classify(
                        path, base_path, source_matcher=source_matcher, binary_matcher=binary_matcher
                    )
                ).value

    source_artifacts = sorted(
        path for path, cls in classifications.items() if (cls == "source") and analysis.is_artifact(path)
    )

    if not source_artifacts:
        await recorder_source.blocker(
            "Release must contain at least one source release artifact",
            {},
            primary_rel_path=None,
        )


def _is_bundled_doc(relative_path: safe.RelPath) -> bool:
    if len(relative_path.as_path().parts) <= 1:
        return False
    path_str = str(relative_path)
    search = re.search(analysis.extension_pattern(), path_str)
    if search and (search.group("artifact") or search.group("metadata")):
        return False
    return not any(path_str.endswith(suffix) for suffix in analysis.STANDALONE_METADATA_SUFFIXES)


async def _record(
    recorder_problems: checks.Recorder,
    recorder_suggestions: checks.Recorder,
    recorder_notes: checks.Recorder,
    relative_path: safe.RelPath,
    errors: list[str],
    blockers: list[str],
    warnings: list[str],
) -> None:
    for error in errors:
        await recorder_problems.concern(f"{relative_path}: {error}", {}, primary_rel_path=relative_path)
    for item in blockers:
        await recorder_problems.blocker(f"{relative_path}: {item}", {}, primary_rel_path=relative_path)
    for warning in warnings:
        await recorder_suggestions.suggestion(f"{relative_path}: {warning}", {}, primary_rel_path=relative_path)
    if not (errors or blockers or warnings):
        await recorder_notes.note(
            f"{relative_path}: Path structure and naming conventions conform to policy",
            {},
            primary_rel_path=relative_path,
        )
