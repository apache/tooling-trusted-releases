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
import os
import pathlib
import shutil
import subprocess
import time
from collections.abc import Mapping
from typing import Final

import aiofiles
import aiofiles.os
import dulwich.client
import dulwich.errors
import dulwich.objects
import dulwich.objectspec
import dulwich.porcelain
import dulwich.refs

import atr.config as config
import atr.log as log
import atr.models.attestable
import atr.models.results as results
import atr.models.safe as safe
import atr.paths as paths
import atr.sandbox as sandbox
import atr.source as source
import atr.tasks.checks as checks
import atr.tasks.task as task
import atr.util as util

_CONFIG: Final = config.get()
_DEFAULT_EMAIL: Final[str] = "atr@localhost"
_DEFAULT_USER: Final[str] = "atr"
_PERMITTED_ADDED_PATHS: Final[dict[str, list[str]]] = {
    "PKG-INFO": ["pyproject.toml"],
    "DEPENDENCIES": ["pom.xml"],
    "DEPENDENCIES.txt": ["pom.xml"],
    "LICENSE": ["pom.xml"],
    "LICENSE.txt": ["pom.xml"],
    "NOTICE": ["pom.xml"],
    "NOTICE.txt": ["pom.xml"],
    "DISCLAIMER": ["pom.xml"],
    "DISCLAIMER.txt": ["pom.xml"],
}
# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = ["github_tp_sha"]
CHECK_VERSION: Final[str] = "9"


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


async def source_trees(args: checks.FunctionArguments) -> results.Results | None:
    recorder = await args.recorder(CHECK_VERSION)
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

    payload = await source.read(args.project_key, args.version_key, args.revision_number)
    repository, sha = payload.repository, payload.sha
    tip = sha if payload.override else payload.default
    if not (repository and sha):
        # With several repositories the release level record is empty, and each archive says for itself
        declared = await source.archive(
            args.project_key, args.version_key, args.revision_number, str(args.primary_rel_path)
        )
        if declared is None:
            # A GitHub project we can't compare gets a concern rather than silence, so the release
            # manager has to note it before the vote and voters can see the comparison is on them
            if reason := _uncompared_reason(payload):
                await recorder.concern(
                    "Could not compare this archive against its GitHub source. "
                    "Voters should check against the tagged source themselves",
                    {"reason": reason, "repositories": list(payload.candidates), "commit": sha or None},
                )
            return None
        repository, sha, tip = declared.repository, declared.commit, None
    if not await _in_history(repository, sha, tip):
        await recorder.concern(
            "Source commit is not in the history of its GitHub repository",
            {"repository": repository, "commit": sha, "run_commit": tip},
        )
        return None
    try:
        outcome = await _compare_checkout_and_archive(args, recorder, repository, sha)
    except OSError as e:
        raise task.CheckRetryableError(
            "Error reading the extracted archive tree",
            {"error": str(e)},
        ) from e
    if outcome is None:
        return None
    checkout_dir, archive_dir = outcome
    log.info(
        "Ran compare.source_trees successfully",
        project=args.project_key,
        version=args.version_key,
        revision=args.revision_number,
        path=args.primary_rel_path,
        repository=repository,
        sha=sha,
        github_checkout=checkout_dir,
        archive_checkout=archive_dir,
    )
    return None


def _branch_and_tag_wants(
    refs: Mapping[dulwich.refs.Ref, dulwich.objects.ObjectID], depth: int | None = None
) -> list[dulwich.objects.ObjectID]:
    return [value for ref, value in refs.items() if ref.startswith((b"refs/heads/", b"refs/tags/"))]


async def _checkout_github_source(repository: str, sha: str, checkout_dir: safe.StatePath) -> str | None:
    repo_url = f"https://github.com/{repository}.git"
    started_ns = time.perf_counter_ns()
    try:
        await asyncio.wait_for(asyncio.to_thread(_clone_repo, repo_url, sha, checkout_dir), timeout=360)
    except TimeoutError:
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
        log.exception(
            "Timeout cloning GitHub repo for compare.source_trees",
            repo_url=repo_url,
            sha=sha,
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
    except (
        OSError,
        RuntimeError,
        dulwich.client.HTTPProxyUnauthorized,
        dulwich.client.HTTPUnauthorized,
        dulwich.errors.ChecksumMismatch,
        dulwich.errors.GitProtocolError,
        dulwich.errors.NotGitRepository,
    ):
        elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
        log.exception(
            "Failed to clone GitHub repo for compare.source_trees",
            repo_url=repo_url,
            sha=sha,
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
        sha=sha,
        checkout_dir=str(checkout_dir),
        clone_ms=elapsed_ms,
    )
    return str(checkout_dir)


def _clone_repo(repo_url: str, sha: str, checkout_dir: safe.StatePath) -> None:
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


async def _compare_checkout_and_archive(  # noqa: C901
    args: checks.FunctionArguments,
    recorder: checks.Recorder,
    repository: str,
    sha: str,
) -> tuple[str, str] | None:
    if not (primary_abs_path := await recorder.abs_path()):
        return None
    extracted_dir = await checks.resolve_archive_dir(args)
    if extracted_dir is None:
        raise task.CheckRetryableError(
            "Extracted archive tree is not available",
            {"rel_path": str(args.primary_rel_path)},
        )
    tmp_dir = paths.get_tmp_dir()
    await aiofiles.os.makedirs(tmp_dir, exist_ok=True)
    async with util.async_temporary_directory(prefix="trees-", dir=tmp_dir) as temp_dir:
        github_dir = safe.StatePath(temp_dir) / "github"
        await aiofiles.os.makedirs(github_dir, exist_ok=True)
        checkout_dir = await _checkout_github_source(repository, sha, github_dir)
        if checkout_dir is None:
            repo_url = f"https://github.com/{repository}.git"
            raise task.CheckRetryableError(
                "Failed to clone GitHub repository for comparison",
                {"repo_url": repo_url, "sha": sha},
            )
        archive_root_result = await _find_archive_root(primary_abs_path, extracted_dir)
        if archive_root_result.root is None:
            await recorder.concern(
                "Could not determine archive root directory for comparison",
                {"archive_path": str(primary_abs_path), "extract_dir": str(extracted_dir)},
            )
            return None
        if archive_root_result.extra_entries:
            await recorder.concern(
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
            raise task.CheckRetryableError(
                "Failed to compare source tree against GitHub checkout",
                {"error": str(exc)},
            ) from exc
        invalid_filtered: set[str] = set()
        for path in comparison.invalid:
            required = _PERMITTED_ADDED_PATHS.get(path)
            if required is None:
                invalid_filtered.add(path)
            elif not all((archive_content_dir / r).path.is_file() for r in required):
                invalid_filtered.add(path)
        if invalid_filtered:
            invalid_list = sorted(invalid_filtered)
            await recorder.concern(
                "Source archive contains files not in GitHub checkout or with different content",
                {"invalid_count": len(invalid_list), "invalid_paths": invalid_list},
            )
            return None
        repo_only_list = sorted(comparison.repo_only)
        await recorder.note(
            "Source archive is a valid subset of GitHub checkout",
            {
                "repo_only_count": len(repo_only_list),
                "repo_only_paths_sample": repo_only_list[:5],
            },
        )
        return checkout_dir, archive_dir


async def _compare_trees(repo_dir: safe.StatePath, archive_dir: safe.StatePath) -> TreeComparisonResult:
    return await asyncio.to_thread(_compare_trees_rsync, repo_dir, archive_dir)


def _compare_trees_rsync(repo_dir: safe.StatePath, archive_dir: safe.StatePath) -> TreeComparisonResult:  # noqa: C901
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
    # A dry-run diff, so rsync only reads the two trees - confine it to those at the kernel level.
    command = sandbox.rsync_command(command, ro_paths=[str(repo_dir), str(archive_dir)])
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
                if (len(flags) >= 3) and (flags[1] in ("d", "f", "L")):
                    if flags[2] == "+":
                        is_repo_only = True
                    elif (flags[0] in (">", "<", "c")) and (flags[2] in ("c", "s")):
                        is_content_diff = True
        # rsync itemises the top of the tree as "./" when its timestamp differs, which isn't a file
        if (not rel_path) or (rel_path == "."):
            continue
        # Source tree members can contain dotfiles, but must stay within their respective trees
        full_repo = safe.StatePath(repo_dir.path / rel_path, root=repo_dir.path)
        full_archive = safe.StatePath(archive_dir.path / rel_path, root=archive_dir.path)
        if is_repo_only and full_archive.path.exists(follow_symlinks=False):
            invalid.add(rel_path)
        elif is_repo_only and (not full_repo.path.is_dir(follow_symlinks=False)):
            repo_only.add(rel_path)
        elif is_content_diff and (not full_archive.path.is_dir(follow_symlinks=False)):
            invalid.add(rel_path)
    return TreeComparisonResult(invalid, repo_only)


def _ensure_clone_identity_env() -> None:
    os.environ["USER"] = _DEFAULT_USER
    os.environ["EMAIL"] = _DEFAULT_EMAIL


def _fetch_history(repo_url: str, sha: str, tip: str | None, history_dir: str) -> bool:
    repo = dulwich.porcelain.init(history_dir, bare=True)
    git_client, path = dulwich.client.get_transport_and_path(repo_url, operation="pull")
    determine_wants = DetermineWantsForSha(tip) if tip else _branch_and_tag_wants
    try:
        git_client.fetch(path, repo, determine_wants=determine_wants, filter_spec=b"tree:0")
    finally:
        with contextlib.suppress(Exception):
            git_client.close()
    return dulwich.objects.ObjectID(sha.encode("ascii")) in repo.object_store


async def _find_archive_root(archive_path: safe.StatePath, extract_dir: safe.StatePath) -> ArchiveRootResult:
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


async def _in_history(repository: str, sha: str, tip: str | None) -> bool:
    if sha == tip:
        return True
    if not repository.startswith("apache/"):
        return False
    tmp_dir = paths.get_tmp_dir()
    await aiofiles.os.makedirs(tmp_dir, exist_ok=True)
    async with util.async_temporary_directory(prefix="history-", dir=tmp_dir) as temp_dir:
        repo_url = f"https://github.com/{repository}.git"
        return await asyncio.to_thread(_fetch_history, repo_url, sha, tip, str(temp_dir))


def _uncompared_reason(payload: atr.models.attestable.SourceV2) -> str | None:
    # Projects without a GitHub repository were never promised a comparison
    if not payload.candidates:
        return None
    if len(payload.candidates) > 1:
        return "Several GitHub repositories are listed and none is declared for this archive. Declare one under Tools"
    return "No source commit has been recorded for this release"
