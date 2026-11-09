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

import datetime
import os
import pathlib
import shutil
import subprocess
import unittest.mock as mock
from collections.abc import Callable, Iterable, Mapping

import aiofiles.os
import dulwich.client
import dulwich.objects
import dulwich.refs
import dulwich.repo
import pytest

import atr.attestable
import atr.models.attestable
import atr.models.github
import atr.models.safe
import atr.models.safe as safe
import atr.models.sql
import atr.tasks.checks
import atr.tasks.checks.compare
import atr.tasks.task as task


class CheckoutRecorder:
    def __init__(self, return_value: str | None = None) -> None:
        self.checkout_dir: safe.StatePath | None = None
        self.repository: str | None = None
        self.sha: str | None = None
        self.return_value = return_value

    async def __call__(self, repository: str, sha: str, checkout_dir: safe.StatePath) -> str | None:
        self.checkout_dir = checkout_dir
        self.repository = repository
        self.sha = sha
        assert await aiofiles.os.path.exists(checkout_dir)
        if self.return_value is not None:
            return self.return_value
        return str(checkout_dir)


class CheckoutFailure:
    async def __call__(self, repository: str, sha: str, checkout_dir: safe.StatePath) -> str | None:
        return None


class CloneRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, pathlib.Path]] = []

    def __call__(self, repo_url: str, sha: str, checkout_dir: pathlib.Path) -> None:
        self.calls.append((repo_url, sha, checkout_dir))


class CommitStub:
    def __init__(self, tree: object) -> None:
        self.tree = tree


class CompareRecorder:
    def __init__(
        self,
        invalid: set[str] | None = None,
        repo_only: set[str] | None = None,
    ) -> None:
        self.calls: list[tuple[pathlib.Path, pathlib.Path]] = []
        self.invalid = invalid or set()
        self.repo_only = repo_only or set()

    async def __call__(
        self, repo_dir: pathlib.Path, archive_dir: pathlib.Path
    ) -> atr.tasks.checks.compare.TreeComparisonResult:
        self.calls.append((repo_dir, archive_dir))
        return atr.tasks.checks.compare.TreeComparisonResult(set(self.invalid), set(self.repo_only))


class ArchiveDirResolver:
    def __init__(self, cache_dir: safe.StatePath | None) -> None:
        self.cache_dir = cache_dir

    async def __call__(self, args: object) -> pathlib.Path | None:
        return self.cache_dir


class FindArchiveRootRecorder:
    def __init__(self, root: str | None = "artifact", extra_entries: list[str] | None = None) -> None:
        self.calls: list[tuple[pathlib.Path, pathlib.Path]] = []
        self.root = root
        self.extra_entries = extra_entries or []

    async def __call__(
        self, archive_path: pathlib.Path, extract_dir: pathlib.Path
    ) -> atr.tasks.checks.compare.ArchiveRootResult:
        self.calls.append((archive_path, extract_dir))
        return atr.tasks.checks.compare.ArchiveRootResult(root=self.root, extra_entries=self.extra_entries)


class GitClientStub:
    def __init__(self) -> None:
        self.closed = False
        self.fetch_calls: list[tuple[str, object, int | None]] = []
        self.wants: list[dulwich.objects.ObjectID] | None = None

    def fetch(
        self,
        path: str,
        repo: object,
        determine_wants: Callable[
            [Mapping[dulwich.refs.Ref, dulwich.objects.ObjectID], int | None],
            list[dulwich.objects.ObjectID],
        ],
        depth: int | None = None,
    ) -> None:
        self.fetch_calls.append((path, repo, depth))
        if self.wants is None:
            self.wants = determine_wants({}, depth)

    def close(self) -> None:
        self.closed = True


class InitRecorder:
    def __init__(self, repo: object) -> None:
        self.calls: list[str] = []
        self.repo = repo

    def __call__(self, path: str) -> object:
        self.calls.append(path)
        return self.repo


class ParseCommitRecorder:
    def __init__(self, commit: CommitStub, raise_exc: Exception | None = None) -> None:
        self.calls: list[tuple[object, str]] = []
        self.commit = commit
        self.raise_exc = raise_exc

    def __call__(self, repo: object, sha: str) -> CommitStub:
        self.calls.append((repo, sha))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.commit


class RunRecorder:
    def __init__(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.calls: list[list[str]] = []
        self.completed = completed

    def __call__(
        self,
        args: list[str],
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return self.completed


class PayloadLoader:
    def __init__(self, payload: atr.models.github.TrustedPublisherPayload | None) -> None:
        self.payload = payload

    async def __call__(
        self, project_key: str, version_key: str, revision_number: str
    ) -> atr.models.github.TrustedPublisherPayload | None:
        return self.payload


class RaiseAsync:
    def __init__(self, message: str) -> None:
        self.message = message

    async def __call__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(self.message)


class RaiseSync:
    def __init__(self, message: str) -> None:
        self.message = message

    def __call__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(self.message)


class RecorderFactory:
    def __init__(self, recorder: atr.tasks.checks.Recorder) -> None:
        self._recorder = recorder

    async def __call__(self, _version: str | None) -> atr.tasks.checks.Recorder:
        return self._recorder


class RecorderStub(atr.tasks.checks.Recorder):
    def __init__(self, is_source: bool) -> None:
        super().__init__(
            checker="compare.source_trees",
            checker_version="1",
            inputs_hash=None,
            project_key=atr.models.safe.ProjectKey("project"),
            version_key=atr.models.safe.VersionKey("version"),
            revision_number=atr.models.safe.RevisionNumber("00001"),
            primary_rel_path="artifact.tar.gz",
            member_rel_path=None,
            afresh=False,
        )
        self.concern_calls: list[tuple[str, object]] = []
        self.exception_calls: list[tuple[str, object]] = []
        self.note_calls: list[tuple[str, object]] = []
        self._is_source = is_source

    async def primary_path_is_source(self) -> bool:
        return self._is_source

    async def concern(
        self, message: str, data: object, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> atr.models.sql.CheckResult:
        self.concern_calls.append((message, data))
        return atr.models.sql.CheckResult(
            release_key=self.release_key,
            revision_number=self.revision_number,
            checker=self.checker,
            checker_version=self.checker_version,
            primary_rel_path=primary_rel_path or self.primary_rel_path,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=atr.models.sql.CheckResultStatus.CONCERN,
            message=message,
            data=data,
            cached=False,
        )

    async def exception(
        self, message: str, data: object, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> atr.models.sql.CheckResult:
        self.exception_calls.append((message, data))
        return atr.models.sql.CheckResult(
            release_key=self.release_key,
            revision_number=self.revision_number,
            checker=self.checker,
            checker_version=self.checker_version,
            primary_rel_path=primary_rel_path or self.primary_rel_path,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=atr.models.sql.CheckResultStatus.EXCEPTION,
            message=message,
            data=data,
            cached=False,
        )

    async def note(
        self, message: str, data: object, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> atr.models.sql.CheckResult:
        self.note_calls.append((message, data))
        return atr.models.sql.CheckResult(
            release_key=None,
            revision_number=None,
            checker=self.checker,
            checker_version=self.checker_version,
            primary_rel_path=primary_rel_path or self.primary_rel_path,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=atr.models.sql.CheckResultStatus.NOTE,
            message=message,
            data=data,
            cached=False,
        )


class RepoStub:
    def __init__(self, controldir: pathlib.Path, worktree: object) -> None:
        self._controldir = controldir
        self._worktree = worktree

    def controldir(self) -> str:
        return str(self._controldir)

    def get_worktree(self) -> object:
        return self._worktree


class ReturnValue:
    def __init__(self, value: safe.StatePath) -> None:
        self.value = value

    def __call__(self) -> safe.StatePath:
        return self.value


class TransportRecorder:
    def __init__(self, client: object, path: str) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.client = client
        self.path = path

    def __call__(self, repo_url: str, operation: str | None = None) -> tuple[object, str]:
        self.calls.append((repo_url, operation))
        return self.client, self.path


class WorktreeStub:
    def __init__(self) -> None:
        self.reset_calls: list[object] = []

    def reset_index(self, tree: object | None = None) -> None:
        self.reset_calls.append(tree)


@pytest.mark.asyncio
async def test_checkout_github_source_uses_provided_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    payload = _make_payload()
    checkout_dir = temp_dir / "checkout"
    clone_recorder = CloneRecorder()

    monkeypatch.setattr(atr.tasks.checks.compare, "_clone_repo", clone_recorder)

    result = await atr.tasks.checks.compare._checkout_github_source(payload.repository, payload.sha, checkout_dir)

    assert result == str(checkout_dir)
    assert len(clone_recorder.calls) == 1
    repo_url, sha, called_dir = clone_recorder.calls[0]
    assert repo_url == "https://github.com/apache/test.git"
    assert sha == "0000000000000000000000000000000000000000"
    assert called_dir == checkout_dir


def test_clone_repo_fetches_requested_sha(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    checkout_dir = temp_dir / "checkout"
    git_dir = pathlib.Path(checkout_dir) / ".git"
    git_dir.mkdir(parents=True)
    worktree = WorktreeStub()
    repo = RepoStub(git_dir, worktree)
    init_recorder = InitRecorder(repo)
    git_client = GitClientStub()
    transport = TransportRecorder(git_client, "remote-path")
    tree_marker = object()
    parse_commit = ParseCommitRecorder(CommitStub(tree_marker))
    sha = "0000000000000000000000000000000000000000"

    monkeypatch.setattr(atr.tasks.checks.compare.dulwich.porcelain, "init", init_recorder)
    monkeypatch.setattr(atr.tasks.checks.compare.dulwich.client, "get_transport_and_path", transport)
    monkeypatch.setattr(atr.tasks.checks.compare.dulwich.objectspec, "parse_commit", parse_commit)

    atr.tasks.checks.compare._clone_repo("https://github.com/apache/test.git", sha, checkout_dir)

    assert init_recorder.calls == [str(checkout_dir)]
    assert transport.calls == [("https://github.com/apache/test.git", "pull")]
    assert git_client.fetch_calls == [("remote-path", repo, 1)]
    assert git_client.wants == [dulwich.objects.ObjectID(sha.encode("ascii"))]
    assert parse_commit.calls == [(repo, sha)]
    assert worktree.reset_calls == [tree_marker]
    assert not git_dir.exists()
    assert git_client.closed is True


def test_clone_repo_raises_when_commit_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    checkout_dir = temp_dir / "checkout"
    git_dir = pathlib.Path(checkout_dir) / ".git"
    git_dir.mkdir(parents=True)
    worktree = WorktreeStub()
    repo = RepoStub(git_dir, worktree)
    init_recorder = InitRecorder(repo)
    git_client = GitClientStub()
    transport = TransportRecorder(git_client, "remote-path")
    parse_commit = ParseCommitRecorder(CommitStub(object()), raise_exc=KeyError("missing"))
    sha = "1111111111111111111111111111111111111111"

    monkeypatch.setattr(atr.tasks.checks.compare.dulwich.porcelain, "init", init_recorder)
    monkeypatch.setattr(atr.tasks.checks.compare.dulwich.client, "get_transport_and_path", transport)
    monkeypatch.setattr(atr.tasks.checks.compare.dulwich.objectspec, "parse_commit", parse_commit)

    with pytest.raises(RuntimeError, match=r"Commit .* not found in shallow clone"):
        atr.tasks.checks.compare._clone_repo("https://github.com/apache/test.git", sha, checkout_dir)

    assert git_client.fetch_calls == [("remote-path", repo, 1)]
    assert git_client.wants == [dulwich.objects.ObjectID(sha.encode("ascii"))]
    assert parse_commit.calls == [(repo, sha)]
    assert worktree.reset_calls == []
    assert git_dir.exists()
    assert git_client.closed is True


@pytest.mark.parametrize("rel_path", ["b.txt", ".asf.yaml", ".cargo/config.toml"])
def test_compare_trees_rsync_archive_has_extra_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, rel_path: str
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, ["a.txt"])
    _make_tree(archive_dir.path, ["a.txt", rel_path])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout="",
        stderr=f"*deleting {rel_path}\n",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == {rel_path}
    assert result.repo_only == set()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is not available")
@pytest.mark.parametrize("name", ["member", ".member"])
@pytest.mark.parametrize(
    ("repo_entry", "archive_entry", "expected"),
    [
        ("-> a.txt", "-> a.txt", None),
        ("-> missing", "-> missing", None),
        ("-> a.txt", "-> ./a.txt", "invalid"),
        ("-> missing", "-> other", "invalid"),
        ("-> a.txt", None, "repo_only"),
        (None, "-> d", "invalid"),
        ("-> a.txt", "file", "invalid"),
        ("file", "-> a.txt", "invalid"),
        ("-> d", "dir", "invalid"),
        ("dir", "-> d", "invalid"),
    ],
)
def test_compare_trees_rsync_compares_symlinks(
    tmp_path: pathlib.Path, name: str, repo_entry: str | None, archive_entry: str | None, expected: str | None
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    for root, entry in [(repo_dir.path, repo_entry), (archive_dir.path, archive_entry)]:
        _make_tree(root, ["a.txt", "d/x"])
        if entry == "file":
            _make_tree(root, [name])
        elif entry == "dir":
            (root / name).mkdir()
        elif entry is not None:
            (root / name).symlink_to(entry.removeprefix("-> "))
    if repo_entry == archive_entry:
        os.utime(archive_dir.path / name, (0, 0), follow_symlinks=False)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == ({name} if (expected == "invalid") else set())
    assert result.repo_only == ({name} if (expected == "repo_only") else set())


@pytest.mark.parametrize("rel_path", ["b.txt", ".asf.yaml", ".cargo/config.toml"])
def test_compare_trees_rsync_content_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, rel_path: str
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, ["a.txt", rel_path])
    _make_tree(archive_dir.path, ["a.txt", rel_path])
    (archive_dir.path / rel_path).write_text("different content", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=f">fc......... {rel_path}\n",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == {rel_path}
    assert result.repo_only == set()


def test_compare_trees_rsync_distinct_files(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, ["a.txt"])
    _make_tree(archive_dir.path, ["b.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=">f+++++++++ a.txt\n",
        stderr="*deleting b.txt\n",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == {"b.txt"}
    assert result.repo_only == {"a.txt"}


def test_compare_trees_rsync_ignores_root_directory_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, ["a.txt"])
    _make_tree(archive_dir.path, ["a.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=".d..t...... ./\n",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == set()
    assert result.repo_only == set()


@pytest.mark.parametrize("rel_path", ["a.txt", ".asf.yaml", ".cargo/config.toml"])
def test_compare_trees_rsync_ignores_timestamp_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, rel_path: str
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, [rel_path])
    _make_tree(archive_dir.path, [rel_path])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=f".f..t...... {rel_path}\n",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == set()
    assert result.repo_only == set()


@pytest.mark.parametrize("absolute", [False, True])
def test_compare_trees_rsync_rejects_paths_outside_trees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, absolute: bool
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, [])
    _make_tree(archive_dir.path, [])
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the compared trees", encoding="utf-8")
    rel_path = str(outside) if absolute else "../outside.txt"
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=f">fc......... {rel_path}\n",
        stderr="",
    )

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", RunRecorder(completed))

    with pytest.raises(ValueError):
        atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)


@pytest.mark.parametrize("tree", ["repo", "archive"])
def test_compare_trees_rsync_rejects_symlinks_outside_trees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, tree: str
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, ["config.toml"])
    _make_tree(archive_dir.path, ["config.toml"])
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the compared trees", encoding="utf-8")
    link = tmp_path / tree / "config.toml"
    link.unlink()
    link.symlink_to(outside)
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=">fc......... config.toml\n",
        stderr="",
    )

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", RunRecorder(completed))

    with pytest.raises(ValueError, match="not within managed root"):
        atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)


@pytest.mark.parametrize("rel_path", ["b.txt", ".asf.yaml", ".cargo/config.toml"])
def test_compare_trees_rsync_repo_has_extra_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, rel_path: str
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, ["a.txt", rel_path])
    _make_tree(archive_dir.path, ["a.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=f">f+++++++++ {rel_path}\n",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == set()
    assert result.repo_only == {rel_path}


def test_compare_trees_rsync_trees_match(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    repo_dir = temp_dir / "repo"
    archive_dir = temp_dir / "archive"
    _make_tree(repo_dir.path, ["a.txt", "b.txt"])
    _make_tree(archive_dir.path, ["a.txt", "b.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout="",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == set()
    assert result.repo_only == set()


@pytest.mark.parametrize(
    ("name", "run_commit", "expected"),
    [("base", "tip", True), ("side", "tip", False), ("base", None, True), ("side", None, False)],
)
def test_fetch_history_needs_run_ancestor_or_branch_or_tag(
    tmp_path: pathlib.Path, name: str, run_commit: str | None, expected: bool
) -> None:
    origin = dulwich.repo.Repo.init_bare(str(tmp_path / "origin"), mkdir=True)
    base = _make_commit(origin, b"base", [])
    commits = {"base": base, "tip": _make_commit(origin, b"tip", [base]), "side": _make_commit(origin, b"side", [base])}
    origin.refs[b"refs/heads/main"] = commits["tip"]
    origin.refs[b"refs/pull/1/head"] = commits["side"]
    (tmp_path / "history").mkdir()
    tip = commits[run_commit].decode() if run_commit else None

    found = atr.tasks.checks.compare._fetch_history(
        str(tmp_path / "origin"), commits[name].decode(), tip, str(tmp_path / "history")
    )

    assert found is expected


@pytest.mark.asyncio
async def test_find_archive_root_accepts_any_single_directory(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0.tar.gz"
    extract_dir = temp_dir / "extracted"
    extract_dir.path.mkdir(parents=True)
    root_dir = extract_dir / "package"
    root_dir.path.mkdir()

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "package"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_detects_extra_file_entries(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0.tar.gz"
    extract_dir = temp_dir / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.path.mkdir(parents=True)
    (extract_dir / "extra.txt").path.write_text("extra")
    (extract_dir / "README").path.write_text("readme")

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert sorted(result.extra_entries) == ["README", "extra.txt"]


@pytest.mark.asyncio
async def test_find_archive_root_finds_expected_root(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0.tar.gz"
    extract_dir = temp_dir / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.path.mkdir(parents=True)

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_finds_root_with_source_suffix(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0-source.tar.gz"
    extract_dir = temp_dir / "extracted"
    root_dir = extract_dir / "my-project-1.0.0-source"
    root_dir.path.mkdir(parents=True)

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0-source"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_finds_root_without_source_suffix(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0-source.tar.gz"
    extract_dir = temp_dir / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.path.mkdir(parents=True)

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_ignores_macos_metadata(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0.tar.gz"
    extract_dir = temp_dir / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.path.mkdir(parents=True)
    metadata_file = pathlib.Path(extract_dir) / "._my-project-1.0.0"
    metadata_file.write_text("metadata")

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_returns_none_when_multiple_directories(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0.tar.gz"
    extract_dir = temp_dir / "extracted"
    extract_dir.path.mkdir(parents=True)
    (extract_dir / "dir1").path.mkdir()
    (extract_dir / "dir2").path.mkdir()

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root is None


@pytest.mark.asyncio
async def test_find_archive_root_returns_none_when_no_directories(tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / "my-project-1.0.0.tar.gz"
    extract_dir = temp_dir / "extracted"
    extract_dir.path.mkdir(parents=True)
    (extract_dir / "file.txt").path.write_text("content")

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root is None


@pytest.mark.asyncio
async def test_in_history_needs_apache_repository() -> None:
    assert not await atr.tasks.checks.compare._in_history("Berri-AI/test", "d" * 40, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, "c" * 40])
async def test_source_trees_creates_temp_workspace_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, override: str | None
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    cache_dir.path.mkdir()
    find_root = FindArchiveRootRecorder("artifact")
    compare = CompareRecorder(repo_only={"extra1.txt", "extra2.txt"})
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    recorded = (
        atr.models.attestable.AttestableV2(
            source=atr.models.attestable.SourceV2(repository=payload.repository, default=payload.sha, override=override)
        )
        if override
        else None
    )
    monkeypatch.setattr(atr.attestable, "load", mock.AsyncMock(return_value=recorded))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert checkout.checkout_dir is not None
    assert checkout.sha == (override or payload.sha)
    checkout_dir = checkout.checkout_dir
    assert checkout_dir.path.name == "github"
    assert checkout_dir.parent.path.parent == tmp_root.path
    assert checkout_dir.parent.path.name.startswith("trees-")
    assert await aiofiles.os.path.exists(tmp_root)
    assert not await aiofiles.os.path.exists(checkout_dir.parent)
    assert len(recorder.note_calls) == 1
    message, data = recorder.note_calls[0]
    assert message == "Source archive is a valid subset of GitHub checkout"
    assert isinstance(data, dict)
    assert data["repo_only_count"] == 2
    assert data["repo_only_paths_sample"] == ["extra1.txt", "extra2.txt"]


@pytest.mark.asyncio
async def test_source_trees_payload_none_skips_temp_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(None))
    monkeypatch.setattr(
        atr.tasks.checks.compare,
        "_checkout_github_source",
        RaiseAsync("_checkout_github_source should not be called"),
    )
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", RaiseAsync("resolve_archive_dir should not be called"))
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", RaiseSync("get_tmp_dir should not be called"))

    await atr.tasks.checks.compare.source_trees(args)
    assert recorder.concern_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidates", "override", "reason_start"),
    [
        (["apache/first", "apache/second"], None, "Several GitHub repositories"),
        (["apache/first", "apache/second"], "c" * 40, "Several GitHub repositories"),
        (["apache/first"], None, "No source commit"),
    ],
)
async def test_source_trees_concern_when_uncomparable(
    monkeypatch: pytest.MonkeyPatch, candidates: list[str], override: str | None, reason_start: str
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    # Either the repository or the sha is missing from the record, so there's nothing to clone
    recorded = atr.models.attestable.AttestableV2(
        source=atr.models.attestable.SourceV2(override=override, candidates=candidates)
    )

    monkeypatch.setattr(atr.attestable, "load", mock.AsyncMock(return_value=recorded))
    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(None))
    monkeypatch.setattr(
        atr.tasks.checks.compare,
        "_checkout_github_source",
        RaiseAsync("_checkout_github_source should not be called"),
    )

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.concern_calls) == 1
    message, data = recorder.concern_calls[0]
    assert message.startswith("Could not compare this archive against its GitHub source")
    assert isinstance(data, dict)
    assert data["reason"].startswith(reason_start)
    assert data["repositories"] == candidates
    assert data["commit"] == override


@pytest.mark.asyncio
async def test_source_trees_concern_when_declared_commit_outside_run_history(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    recorded = atr.models.attestable.AttestableV2(
        source=atr.models.attestable.SourceV2(repository="apache/test", default="a" * 40, declared="d" * 40)
    )
    in_history = mock.AsyncMock(return_value=False)

    monkeypatch.setattr(atr.attestable, "load", mock.AsyncMock(return_value=recorded))
    monkeypatch.setattr(atr.tasks.checks.compare, "_in_history", in_history)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_checkout_and_archive", RaiseAsync("unexpected comparison"))

    await atr.tasks.checks.compare.source_trees(args)

    in_history.assert_awaited_once_with("apache/test", "d" * 40, "a" * 40)
    assert [message for message, _ in recorder.concern_calls] == [
        "Source commit is not in the history of its GitHub repository"
    ]


@pytest.mark.asyncio
async def test_source_trees_stays_quiet_without_github_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    recorded = atr.models.attestable.AttestableV2(source=atr.models.attestable.SourceV2(candidates=[]))

    monkeypatch.setattr(atr.attestable, "load", mock.AsyncMock(return_value=recorded))
    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(None))

    await atr.tasks.checks.compare.source_trees(args)

    assert recorder.concern_calls == []


@pytest.mark.asyncio
async def test_source_trees_uses_declared_source(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    cache_dir.path.mkdir()
    find_root = FindArchiveRootRecorder("artifact")
    compare = CompareRecorder(repo_only=set())
    # Two candidates and no release level choice, but this archive has said where it came from
    declared = atr.models.attestable.ArchiveSourceV2(repository="apache/second", commit="d" * 40)
    recorded = atr.models.attestable.AttestableV2(
        paths={
            "artifact.tar.gz": atr.models.attestable.PathEntryV2(
                content_hash="h", classification="source", source=declared
            )
        },
        source=atr.models.attestable.SourceV2(candidates=["apache/first", "apache/second"]),
    )

    in_history = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(atr.attestable, "load", mock.AsyncMock(return_value=recorded))
    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(None))
    monkeypatch.setattr(atr.tasks.checks.compare, "_in_history", in_history)
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(temp_dir / "temporary-root"))

    await atr.tasks.checks.compare.source_trees(args)

    in_history.assert_awaited_once_with("apache/second", "d" * 40, None)
    assert (checkout.repository, checkout.sha) == ("apache/second", "d" * 40)
    assert recorder.concern_calls == []
    assert len(recorder.note_calls) == 1


@pytest.mark.asyncio
async def test_source_trees_permits_pkg_info_when_pyproject_toml_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    (cache_dir / "artifact").path.mkdir(parents=True)
    (cache_dir / "artifact" / "pyproject.toml").path.write_text("[project]\nname = 'test'\n")
    find_root = FindArchiveRootRecorder("artifact")
    compare = CompareRecorder(invalid={"PKG-INFO"})
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.concern_calls) == 0
    assert len(recorder.note_calls) == 1


@pytest.mark.asyncio
async def test_source_trees_permits_maven_resource_bundles_when_pom_xml_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    (cache_dir / "artifact").path.mkdir(parents=True)
    (cache_dir / "artifact" / "pom.xml").path.write_text("<project/>\n")
    find_root = FindArchiveRootRecorder("artifact")
    # The ASF parent POM adds all of these at once when the repository lacks them
    compare = CompareRecorder(invalid={"DEPENDENCIES", "LICENSE", "NOTICE", "DISCLAIMER"})
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.concern_calls) == 0
    assert len(recorder.note_calls) == 1


@pytest.mark.asyncio
async def test_source_trees_rejects_maven_resource_bundles_without_pom_xml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    (cache_dir / "artifact").path.mkdir(parents=True)
    find_root = FindArchiveRootRecorder("artifact")
    # Without a pom.xml witness the added file has nothing to vouch for it, so it stays a concern
    compare = CompareRecorder(invalid={"DEPENDENCIES"})
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.concern_calls) == 1
    assert len(recorder.note_calls) == 0


@pytest.mark.asyncio
async def test_checkout_returns_none_for_http_auth_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    def raise_unauthorized(repo_url: str, sha: str, checkout_dir: safe.StatePath) -> None:
        raise dulwich.client.HTTPUnauthorized("Basic", repo_url)

    monkeypatch.setattr(atr.tasks.checks.compare, "_clone_repo", raise_unauthorized)
    payload = _make_payload()

    checkout_dir = await atr.tasks.checks.compare._checkout_github_source(
        payload.repository, payload.sha, safe.StatePath(tmp_path)
    )

    assert checkout_dir is None


async def test_source_trees_raises_when_checkout_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    cache_dir = temp_dir / "cache"
    cache_dir.path.mkdir()
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", CheckoutFailure())
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    with pytest.raises(task.CheckRetryableError, match="Failed to clone GitHub repository for comparison") as excinfo:
        await atr.tasks.checks.compare.source_trees(args)

    assert recorder.exception_calls == []
    assert isinstance(excinfo.value.data, dict)
    assert excinfo.value.data["repo_url"] == "https://github.com/apache/test.git"


@pytest.mark.asyncio
async def test_source_trees_records_failure_when_archive_has_invalid_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    cache_dir.path.mkdir()
    find_root = FindArchiveRootRecorder("artifact")
    compare = CompareRecorder(invalid={"bad1.txt", "bad2.txt"}, repo_only={"ok.txt"})
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.concern_calls) == 1
    assert len(recorder.note_calls) == 0
    message, data = recorder.concern_calls[0]
    assert message == "Source archive contains files not in GitHub checkout or with different content"
    assert isinstance(data, dict)
    assert data["invalid_count"] == 2
    assert data["invalid_paths"] == ["bad1.txt", "bad2.txt"]


@pytest.mark.asyncio
async def test_source_trees_records_failure_when_archive_root_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    cache_dir.path.mkdir()
    find_root = FindArchiveRootRecorder(root=None)
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.concern_calls) == 1
    message, data = recorder.concern_calls[0]
    assert message == "Could not determine archive root directory for comparison"
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_source_trees_raises_when_cache_dir_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(None))
    monkeypatch.setattr(
        atr.tasks.checks.compare,
        "_checkout_github_source",
        RaiseAsync("_checkout_github_source should not be called"),
    )
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", RaiseSync("get_tmp_dir should not be called"))

    with pytest.raises(task.CheckRetryableError, match="Extracted archive tree is not available"):
        await atr.tasks.checks.compare.source_trees(args)

    assert recorder.exception_calls == []


@pytest.mark.asyncio
async def test_source_trees_records_failure_when_extra_entries_in_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    cache_dir.path.mkdir()
    find_root = FindArchiveRootRecorder(root="artifact", extra_entries=["README.txt", "extra.txt"])
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.concern_calls) == 1
    message, data = recorder.concern_calls[0]
    assert message == "Archive contains entries outside the root directory"
    assert isinstance(data, dict)
    assert data["root"] == "artifact"
    assert data["extra_entries"] == ["README.txt", "extra.txt"]


@pytest.mark.asyncio
async def test_source_trees_reports_repo_only_sample_limited_to_five(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    cache_dir = temp_dir / "cache"
    cache_dir.path.mkdir()
    find_root = FindArchiveRootRecorder("artifact")
    repo_only_files = {f"file{i}.txt" for i in range(10)}
    compare = CompareRecorder(repo_only=repo_only_files)
    tmp_root = temp_dir / "temporary-root"

    monkeypatch.setattr(atr.attestable, "github_tp_payload_read", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks, "resolve_archive_dir", ArchiveDirResolver(cache_dir))
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.note_calls) == 1
    _message, data = recorder.note_calls[0]
    assert isinstance(data, dict)
    assert data["repo_only_count"] == 10
    assert len(data["repo_only_paths_sample"]) == 5


@pytest.mark.asyncio
async def test_source_trees_skips_when_not_source(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecorderStub(False)
    args = _make_args(recorder)

    monkeypatch.setattr(
        atr.attestable, "github_tp_payload_read", RaiseAsync("github_tp_payload_read should not be called")
    )

    await atr.tasks.checks.compare.source_trees(args)


def _make_args(recorder: atr.tasks.checks.Recorder) -> atr.tasks.checks.FunctionArguments:
    return atr.tasks.checks.FunctionArguments(
        recorder=RecorderFactory(recorder),
        asf_uid="test",
        project_key=safe.ProjectKey("project"),
        version_key=safe.VersionKey("version"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=safe.RelPath("artifact.tar.gz"),
        extra_args={},
    )


def _make_commit(repo: dulwich.repo.Repo, message: bytes, parents: list[bytes]) -> bytes:
    tree = dulwich.objects.Tree()
    repo.object_store.add_object(tree)
    commit = dulwich.objects.Commit()
    commit.tree = tree.id
    commit.parents = parents
    commit.author = commit.committer = b"A <a@example.org>"
    commit.author_time = commit.commit_time = 0
    commit.author_timezone = commit.commit_timezone = 0
    commit.message = message
    repo.object_store.add_object(commit)
    return commit.id


def _make_payload(
    repository: str = "apache/test",
    ref: str = "refs/heads/main",
    sha: str = "0000000000000000000000000000000000000000",
) -> atr.models.github.TrustedPublisherPayload:
    payload = {
        "actor": "actor",
        "actor_id": "1",
        "aud": "audience",
        "base_ref": "",
        "check_run_id": "123",
        "enterprise": "",
        "enterprise_id": "",
        "event_name": "push",
        "exp": 99999999999,
        "head_ref": "",
        "iat": 1,
        "iss": "issuer",
        "job_workflow_ref": "refs/heads/main",
        "job_workflow_sha": "ffffffffffffffffffffffffffffffffffffffff",
        "jti": "token-id",
        "nbf": None,
        "ref": ref,
        "ref_protected": "false",
        "ref_type": "branch",
        "repository": repository,
        "repository_owner": "apache",
        "repository_visibility": "public",
        "run_attempt": "1",
        "run_number": "1",
        "runner_environment": "github-hosted",
        "sha": sha,
        "sub": "repo:apache/test:ref:refs/heads/main",
        "workflow": "build",
        "workflow_ref": "refs/heads/main",
        "workflow_sha": "ffffffffffffffffffffffffffffffffffffffff",
    }
    return atr.models.github.TrustedPublisherPayload.model_validate(payload)


def _make_tree(root: pathlib.Path, files: Iterable[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel_path in files:
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rel_path, encoding="utf-8")
