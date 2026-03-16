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
import dataclasses
import json
import os
import pathlib
import shutil
import subprocess
import time
from collections.abc import Mapping
from typing import Any, Final

import aiofiles
import aiofiles.os
import dulwich.client
import dulwich.objects
import dulwich.objectspec
import dulwich.porcelain
import dulwich.refs
import pydantic

import atr.attestable as attestable
import atr.config as config
import atr.log as log
import atr.models.github as github_models
import atr.models.results as results
import atr.models.safe as safe
import atr.paths as paths
import atr.tasks.checks as checks
import atr.util as util

_CONFIG: Final = config.get()
_DEFAULT_EMAIL: Final[str] = "atr@localhost"
_DEFAULT_USER: Final[str] = "atr"
_PERMITTED_ADDED_PATHS: Final[dict[str, list[str]]] = {
    "PKG-INFO": ["pyproject.toml"],
}
# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = ["github_tp_sha"]
CHECK_VERSION: Final[str] = "2"


@dataclasses.dataclass
class ArchiveRootResult:
    root: str | None
    extra_entries: list[str]


class DetermineWantsForSha:
    def __init__(self, sha: str) -> None:
        self.sha = sha

    def __call__(
        self,
        refs: Mapping[dulwich.refs.Ref, dulwich.objects.ObjectID],
        depth: int | None = None,
    ) -> list[dulwich.objects.ObjectID]:
        return [dulwich.objects.ObjectID(self.sha.encode("ascii"))]


@dataclasses.dataclass
class TreeComparisonResult:
    invalid: set[str]
    repo_only: set[str]


async def source_trees(args: checks.FunctionArguments) -> results.Results | None:  # noqa: C901
    recorder = await args.recorder()
    is_source = await recorder.primary_path_is_source()
    if not is_source:
        log.info(
            "Skipping compare.source_trees because the input is not a source artifact",
            project=args.project_key,
            version=args.version_key,
            revision=args.revision_number,
            path=args.primary_rel_path,
        )
        return None

    payload = await _load_tp_payload(args.project_key, args.version_key, args.revision_number)
    checkout_dir: str | None = None
    archive_dir: str | None = None
    if payload is not None:
        if not (primary_abs_path := await recorder.abs_path()):
            return None
        extracted_dir = await checks.resolve_archive_dir(args)
        if extracted_dir is None:
            await recorder.failure(
                "Extracted archive tree is not available",
                {"rel_path": args.primary_rel_path},
            )
            return None
        tmp_dir = paths.get_tmp_dir()
        await aiofiles.os.makedirs(tmp_dir, exist_ok=True)
        async with util.async_temporary_directory(prefix="trees-", dir=tmp_dir) as temp_dir:
            github_dir = temp_dir / "github"
            await aiofiles.os.makedirs(github_dir, exist_ok=True)
            checkout_dir = await _checkout_github_source(payload, github_dir)
            if checkout_dir is None:
                await recorder.failure(
                    "Failed to clone GitHub repository for comparison",
                    {"repo_url": f"https://github.com/{payload.repository}.git", "sha": payload.sha},
                )
                return None
            archive_root_result = await _find_archive_root(primary_abs_path, extracted_dir)
            if archive_root_result.root is None:
                await recorder.failure(
                    "Could not determine archive root directory for comparison",
                    {"archive_path": str(primary_abs_path), "extract_dir": str(extracted_dir)},
                )
                return None
            if archive_root_result.extra_entries:
                await recorder.failure(
                    "Archive contains entries outside the root directory",
                    {
                        "archive_path": str(primary_abs_path),
                        "root": archive_root_result.root,
                        "extra_entries": sorted(archive_root_result.extra_entries),
                    },
                )
                return None
            archive_content_dir = extracted_dir / archive_root_result.root
            archive_dir = str(archive_content_dir)
            try:
                comparison = await _compare_trees(github_dir, archive_content_dir)
            except RuntimeError as exc:
                await recorder.failure(
                    "Failed to compare source tree against GitHub checkout",
                    {"error": str(exc)},
                )
                return None
            invalid_filtered: set[str] = set()
            for path in comparison.invalid:
                required = _PERMITTED_ADDED_PATHS.get(path)
                if required is None:
                    invalid_filtered.add(path)
                elif not all((archive_content_dir / r).is_file() for r in required):
                    invalid_filtered.add(path)
            if invalid_filtered:
                invalid_list = sorted(invalid_filtered)
                await recorder.failure(
                    "Source archive contains files not in GitHub checkout or with different content",
                    {"invalid_count": len(invalid_list), "invalid_paths": invalid_list},
                )
                return None
            repo_only_list = sorted(comparison.repo_only)
            await recorder.success(
                "Source archive is a valid subset of GitHub checkout",
                {
                    "repo_only_count": len(repo_only_list),
                    "repo_only_paths_sample": repo_only_list[:5],
                },
            )
    payload_summary = _payload_summary(payload)
    log.info(
        "Ran compare.source_trees successfully",
        project=args.project_key,
        version=args.version_key,
        revision=args.revision_number,
        path=args.primary_rel_path,
        github_payload=payload_summary,
        github_checkout=checkout_dir,
        archive_checkout=archive_dir,
    )
    return None


async def _checkout_github_source(
    payload: github_models.TrustedPublisherPayload, checkout_dir: pathlib.Path
) -> str | None:
    repo_url = f"https://github.com/{payload.repository}.git"
    started_ns = time.perf_counter_ns()
    try:
        await asyncio.to_thread(_clone_repo, repo_url, payload.sha, checkout_dir)
    except Exception:
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
        log.exception(
            "Failed to clone GitHub repo for compare.source_trees",
            repo_url=repo_url,
            sha=payload.sha,
            checkout_dir=str(checkout_dir),
            clone_ms=elapsed_ms,
            git_author_name=os.environ.get("GIT_AUTHOR_NAME"),
            git_author_email=os.environ.get("GIT_AUTHOR_EMAIL"),
            git_committer_name=os.environ.get("GIT_COMMITTER_NAME"),
            git_committer_email=os.environ.get("GIT_COMMITTER_EMAIL"),
            user=os.environ.get("USER"),
            logname=os.environ.get("LOGNAME"),
            email=os.environ.get("EMAIL"),
        )
        return None
    elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
    log.debug(
        "Cloned GitHub repo for compare.source_trees",
        repo_url=repo_url,
        sha=payload.sha,
        checkout_dir=str(checkout_dir),
        clone_ms=elapsed_ms,
    )
    return str(checkout_dir)


def _clone_repo(repo_url: str, sha: str, checkout_dir: pathlib.Path) -> None:
    _ensure_clone_identity_env()
    repo = dulwich.porcelain.init(str(checkout_dir))
    git_client, path = dulwich.client.get_transport_and_path(repo_url, operation="pull")
    try:
        determine_wants = DetermineWantsForSha(sha)
        git_client.fetch(path, repo, determine_wants=determine_wants, depth=1)
    finally:
        with contextlib.suppress(Exception):
            git_client.close()
    try:
        commit = dulwich.objectspec.parse_commit(repo, sha)
        repo.get_worktree().reset_index(tree=commit.tree)
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Commit {sha} not found in shallow clone") from exc
    git_dir = pathlib.Path(repo.controldir())
    if git_dir.exists():
        shutil.rmtree(git_dir)


async def _compare_trees(repo_dir: pathlib.Path, archive_dir: pathlib.Path) -> TreeComparisonResult:
    return await asyncio.to_thread(_compare_trees_rsync, repo_dir, archive_dir)


def _compare_trees_rsync(repo_dir: pathlib.Path, archive_dir: pathlib.Path) -> TreeComparisonResult:  # noqa: C901
    if shutil.which("rsync") is None:
        raise RuntimeError("rsync is not available on PATH")
    command = [
        "rsync",
        "-a",
        "--delete",
        "--dry-run",
        "--itemize-changes",
        "--out-format=%i %n",
        "--no-motd",
        "--checksum",
        f"{repo_dir}{os.sep}",
        f"{archive_dir}{os.sep}",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rsync failed with code {result.returncode}: {result.stderr.strip()}")
    invalid: set[str] = set()
    repo_only: set[str] = set()
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        line = line.strip()
        if not line:
            continue
        rel_path: str | None = None
        is_repo_only = False
        is_content_diff = False
        if line.startswith("*deleting ") or line.startswith("deleting "):
            prefix = "*deleting " if line.startswith("*deleting ") else "deleting "
            rel_path = line.removeprefix(prefix).strip().rstrip("/")
            is_content_diff = True
        else:
            parts = line.split(" ", 1)
            if len(parts) == 2:
                flags = parts[0]
                rel_path = parts[1].rstrip("/")
                if (len(flags) >= 3) and (flags[1] == "f"):
                    if (flags[0] == ">") and (flags[2] == "+"):
                        is_repo_only = True
                    elif (flags[0] in (">", "<", "c")) and (flags[2] in ("c", "s", "+")):
                        is_content_diff = True
        if not rel_path:
            continue
        full_repo = repo_dir / rel_path
        full_archive = archive_dir / rel_path
        if full_repo.is_file() or full_archive.is_file():
            if is_repo_only:
                repo_only.add(rel_path)
            elif is_content_diff:
                invalid.add(rel_path)
    return TreeComparisonResult(invalid, repo_only)


def _ensure_clone_identity_env() -> None:
    os.environ["USER"] = _DEFAULT_USER
    os.environ["EMAIL"] = _DEFAULT_EMAIL


async def _find_archive_root(archive_path: pathlib.Path, extract_dir: pathlib.Path) -> ArchiveRootResult:
    entries = await aiofiles.os.listdir(extract_dir)
    directories: list[str] = []
    for entry in entries:
        if entry.startswith("._"):
            continue
        entry_path = extract_dir / entry
        if await aiofiles.os.path.isdir(entry_path):
            directories.append(entry)
    if len(directories) != 1:
        log.warning(
            "Expected exactly one root directory in archive for compare.source_trees",
            archive_path=str(archive_path),
            extract_dir=str(extract_dir),
            directories=directories[:10],
        )
        return ArchiveRootResult(root=None, extra_entries=[])
    found_root = directories[0]
    extra_entries = [e for e in entries if (e != found_root) and (not e.startswith("._"))]
    log.debug(
        "Found archive root directory for compare.source_trees",
        archive_path=str(archive_path),
        extract_dir=str(extract_dir),
        root=found_root,
        extra_entries=extra_entries,
    )
    return ArchiveRootResult(root=found_root, extra_entries=extra_entries)


async def _load_tp_payload(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber
) -> github_models.TrustedPublisherPayload | None:
    payload_path = attestable.github_tp_payload_path(project_key, version_key, revision_number)
    if not await aiofiles.os.path.isfile(payload_path):
        return None
    try:
        async with aiofiles.open(payload_path, encoding="utf-8") as f:
            data = json.loads(await f.read())
        if not isinstance(data, dict):
            log.warning(f"TP payload was not a JSON object in {payload_path}")
            return None
        return github_models.TrustedPublisherPayload.model_validate(data)
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"Failed to read TP payload from {payload_path}: {e}")
        return None
    except pydantic.ValidationError as e:
        log.warning(f"Failed to validate TP payload from {payload_path}: {e}")
        return None


def _payload_summary(payload: github_models.TrustedPublisherPayload | None) -> dict[str, Any]:
    if payload is None:
        return {"present": False}
    return {
        "present": True,
        "repository": payload.repository,
        "ref": payload.ref,
        "sha": payload.sha,
        "workflow_ref": payload.workflow_ref,
        "actor": payload.actor,
        "actor_id": payload.actor_id,
    }
