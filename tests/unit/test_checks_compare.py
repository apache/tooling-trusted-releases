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
import pathlib
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping

import aiofiles.os
import dulwich.objects
import dulwich.refs
import pytest

import atr.models.github
import atr.models.sql
import atr.tasks.checks
import atr.tasks.checks.compare


class CheckoutRecorder:
    def __init__(self, return_value: str | None = None) -> None:
        self.checkout_dir: pathlib.Path | None = None
        self.return_value = return_value

    async def __call__(
        self,
        payload: atr.models.github.TrustedPublisherPayload,
        checkout_dir: pathlib.Path,
    ) -> str | None:
        self.checkout_dir = checkout_dir
        assert await aiofiles.os.path.exists(checkout_dir)
        if self.return_value is not None:
            return self.return_value
        return str(checkout_dir)


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


class DecompressRecorder:
    def __init__(self, return_value: bool = True) -> None:
        self.archive_path: pathlib.Path | None = None
        self.extract_dir: pathlib.Path | None = None
        self.max_extract_size: int | None = None
        self.chunk_size: int | None = None
        self.return_value = return_value

    async def __call__(
        self,
        archive_path: pathlib.Path,
        extract_dir: pathlib.Path,
        max_extract_size: int,
        chunk_size: int,
    ) -> bool:
        self.archive_path = archive_path
        self.extract_dir = extract_dir
        self.max_extract_size = max_extract_size
        self.chunk_size = chunk_size
        assert await aiofiles.os.path.exists(extract_dir)
        return self.return_value


class ExtractErrorRaiser:
    def __call__(self, *args: object, **kwargs: object) -> tuple[int, list[str]]:
        raise atr.tasks.checks.compare.archives.ExtractionError("Extraction error")


class ExtractRecorder:
    def __init__(self, extracted_size: int = 123) -> None:
        self.calls: list[tuple[str, str, int, int]] = []
        self.extracted_size = extracted_size

    def __call__(self, archive_path: str, extract_dir: str, max_size: int, chunk_size: int) -> tuple[int, list[str]]:
        self.calls.append((archive_path, extract_dir, max_size, chunk_size))
        return self.extracted_size, []


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
        self, project_name: str, version_name: str, revision_number: str
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

    async def __call__(self) -> atr.tasks.checks.Recorder:
        return self._recorder


class RecorderStub(atr.tasks.checks.Recorder):
    def __init__(self, is_source: bool) -> None:
        super().__init__(
            checker="compare.source_trees",
            inputs_hash=None,
            project_name="project",
            version_name="version",
            revision_number="00001",
            primary_rel_path="artifact.tar.gz",
            member_rel_path=None,
            afresh=False,
        )
        self.failure_calls: list[tuple[str, object]] = []
        self.success_calls: list[tuple[str, object]] = []
        self._is_source = is_source

    async def primary_path_is_source(self) -> bool:
        return self._is_source

    async def failure(
        self, message: str, data: object, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> atr.models.sql.CheckResult:
        self.failure_calls.append((message, data))
        return atr.models.sql.CheckResult(
            release_name=self.release_name,
            revision_number=self.revision_number,
            checker=self.checker,
            primary_rel_path=primary_rel_path or self.primary_rel_path,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=atr.models.sql.CheckResultStatus.FAILURE,
            message=message,
            data=data,
            cached=False,
        )

    async def success(
        self, message: str, data: object, primary_rel_path: str | None = None, member_rel_path: str | None = None
    ) -> atr.models.sql.CheckResult:
        self.success_calls.append((message, data))
        return atr.models.sql.CheckResult(
            release_name=None,
            revision_number=None,
            checker=self.checker,
            primary_rel_path=primary_rel_path or self.primary_rel_path,
            member_rel_path=member_rel_path,
            created=datetime.datetime.now(datetime.UTC),
            status=atr.models.sql.CheckResultStatus.SUCCESS,
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
    def __init__(self, value: pathlib.Path) -> None:
        self.value = value

    def __call__(self) -> pathlib.Path:
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
    payload = _make_payload()
    checkout_dir = tmp_path / "checkout"
    clone_recorder = CloneRecorder()

    monkeypatch.setattr(atr.tasks.checks.compare, "_clone_repo", clone_recorder)

    result = await atr.tasks.checks.compare._checkout_github_source(payload, checkout_dir)

    assert result == str(checkout_dir)
    assert len(clone_recorder.calls) == 1
    repo_url, sha, called_dir = clone_recorder.calls[0]
    assert repo_url == "https://github.com/apache/test.git"
    assert sha == "0000000000000000000000000000000000000000"
    assert called_dir == checkout_dir


def test_clone_repo_fetches_requested_sha(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    checkout_dir = tmp_path / "checkout"
    git_dir = checkout_dir / ".git"
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
    checkout_dir = tmp_path / "checkout"
    git_dir = checkout_dir / ".git"
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


def test_compare_trees_rsync_archive_has_extra_file(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    repo_dir = tmp_path / "repo"
    archive_dir = tmp_path / "archive"
    _make_tree(repo_dir, ["a.txt"])
    _make_tree(archive_dir, ["a.txt", "b.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout="",
        stderr="*deleting b.txt\n",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == {"b.txt"}
    assert result.repo_only == set()


def test_compare_trees_rsync_content_differs(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    repo_dir = tmp_path / "repo"
    archive_dir = tmp_path / "archive"
    _make_tree(repo_dir, ["a.txt", "b.txt"])
    _make_tree(archive_dir, ["a.txt", "b.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=">fc......... b.txt\n",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == {"b.txt"}
    assert result.repo_only == set()


def test_compare_trees_rsync_distinct_files(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    repo_dir = tmp_path / "repo"
    archive_dir = tmp_path / "archive"
    _make_tree(repo_dir, ["a.txt"])
    _make_tree(archive_dir, ["b.txt"])
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


def test_compare_trees_rsync_ignores_timestamp_only(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    repo_dir = tmp_path / "repo"
    archive_dir = tmp_path / "archive"
    _make_tree(repo_dir, ["a.txt"])
    _make_tree(archive_dir, ["a.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=".f..t...... a.txt\n",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == set()
    assert result.repo_only == set()


def test_compare_trees_rsync_repo_has_extra_file(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    repo_dir = tmp_path / "repo"
    archive_dir = tmp_path / "archive"
    _make_tree(repo_dir, ["a.txt", "b.txt"])
    _make_tree(archive_dir, ["a.txt"])
    completed = subprocess.CompletedProcess(
        args=["rsync"],
        returncode=0,
        stdout=">f+++++++++ b.txt\n",
        stderr="",
    )
    run_recorder = RunRecorder(completed)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", run_recorder)

    result = atr.tasks.checks.compare._compare_trees_rsync(repo_dir, archive_dir)

    assert result.invalid == set()
    assert result.repo_only == {"b.txt"}


def test_compare_trees_rsync_trees_match(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    repo_dir = tmp_path / "repo"
    archive_dir = tmp_path / "archive"
    _make_tree(repo_dir, ["a.txt", "b.txt"])
    _make_tree(archive_dir, ["a.txt", "b.txt"])
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


@pytest.mark.asyncio
async def test_decompress_archive_calls_extract(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "artifact.tar.gz"
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    extract_recorder = ExtractRecorder()

    monkeypatch.setattr(atr.tasks.checks.compare.archives, "extract", extract_recorder)

    result = await atr.tasks.checks.compare._decompress_archive(archive_path, extract_dir, 10, 20)

    assert result is True
    assert extract_recorder.calls == [(str(archive_path), str(extract_dir), 10, 20)]


@pytest.mark.asyncio
async def test_decompress_archive_handles_extraction_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    archive_path = tmp_path / "artifact.tar.gz"
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()

    monkeypatch.setattr(atr.tasks.checks.compare.archives, "extract", ExtractErrorRaiser())

    result = await atr.tasks.checks.compare._decompress_archive(archive_path, extract_dir, 10, 20)

    assert result is False


@pytest.mark.asyncio
async def test_find_archive_root_accepts_any_single_directory(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0.tar.gz"
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir(parents=True)
    root_dir = extract_dir / "package"
    root_dir.mkdir()

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "package"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_detects_extra_file_entries(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0.tar.gz"
    extract_dir = tmp_path / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.mkdir(parents=True)
    (extract_dir / "extra.txt").write_text("extra")
    (extract_dir / "README").write_text("readme")

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert sorted(result.extra_entries) == ["README", "extra.txt"]


@pytest.mark.asyncio
async def test_find_archive_root_finds_expected_root(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0.tar.gz"
    extract_dir = tmp_path / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.mkdir(parents=True)

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_finds_root_with_source_suffix(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0-source.tar.gz"
    extract_dir = tmp_path / "extracted"
    root_dir = extract_dir / "my-project-1.0.0-source"
    root_dir.mkdir(parents=True)

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0-source"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_finds_root_without_source_suffix(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0-source.tar.gz"
    extract_dir = tmp_path / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.mkdir(parents=True)

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_ignores_macos_metadata(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0.tar.gz"
    extract_dir = tmp_path / "extracted"
    root_dir = extract_dir / "my-project-1.0.0"
    root_dir.mkdir(parents=True)
    metadata_file = extract_dir / "._my-project-1.0.0"
    metadata_file.write_text("metadata")

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root == "my-project-1.0.0"
    assert result.extra_entries == []


@pytest.mark.asyncio
async def test_find_archive_root_returns_none_when_multiple_directories(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0.tar.gz"
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir(parents=True)
    (extract_dir / "dir1").mkdir()
    (extract_dir / "dir2").mkdir()

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root is None


@pytest.mark.asyncio
async def test_find_archive_root_returns_none_when_no_directories(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "my-project-1.0.0.tar.gz"
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir(parents=True)
    (extract_dir / "file.txt").write_text("content")

    result = await atr.tasks.checks.compare._find_archive_root(archive_path, extract_dir)

    assert result.root is None


@pytest.mark.asyncio
async def test_source_trees_creates_temp_workspace_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    decompress = DecompressRecorder()
    find_root = FindArchiveRootRecorder("artifact")
    compare = CompareRecorder(repo_only={"extra1.txt", "extra2.txt"})
    tmp_root = tmp_path / "temporary-root"

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks.compare, "_decompress_archive", decompress)
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert checkout.checkout_dir is not None
    checkout_dir = checkout.checkout_dir
    assert checkout_dir.name == "github"
    assert decompress.extract_dir is not None
    assert decompress.extract_dir.name == "archive"
    assert checkout_dir.parent.parent == tmp_root
    assert checkout_dir.parent.name.startswith("trees-")
    assert await aiofiles.os.path.exists(tmp_root)
    assert not await aiofiles.os.path.exists(checkout_dir.parent)
    assert len(recorder.success_calls) == 1
    message, data = recorder.success_calls[0]
    assert message == "Source archive is a valid subset of GitHub checkout"
    assert isinstance(data, dict)
    assert data["repo_only_count"] == 2
    assert data["repo_only_paths_sample"] == ["extra1.txt", "extra2.txt"]


@pytest.mark.asyncio
async def test_source_trees_payload_none_skips_temp_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(None))
    monkeypatch.setattr(
        atr.tasks.checks.compare,
        "_checkout_github_source",
        RaiseAsync("_checkout_github_source should not be called"),
    )
    monkeypatch.setattr(
        atr.tasks.checks.compare,
        "_decompress_archive",
        RaiseAsync("_decompress_archive should not be called"),
    )
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", RaiseSync("get_tmp_dir should not be called"))

    await atr.tasks.checks.compare.source_trees(args)


@pytest.mark.asyncio
async def test_source_trees_permits_pkg_info_when_pyproject_toml_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    find_root = FindArchiveRootRecorder("artifact")
    compare = CompareRecorder(invalid={"PKG-INFO"})
    tmp_root = tmp_path / "temporary-root"

    async def decompress_with_pyproject(
        archive_path: pathlib.Path,
        extract_dir: pathlib.Path,
        max_extract_size: int,
        chunk_size: int,
    ) -> bool:
        archive_content = extract_dir / "artifact"
        archive_content.mkdir(parents=True, exist_ok=True)
        (archive_content / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        return True

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks.compare, "_decompress_archive", decompress_with_pyproject)
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.failure_calls) == 0
    assert len(recorder.success_calls) == 1


@pytest.mark.asyncio
async def test_source_trees_records_failure_when_archive_has_invalid_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    decompress = DecompressRecorder()
    find_root = FindArchiveRootRecorder("artifact")
    compare = CompareRecorder(invalid={"bad1.txt", "bad2.txt"}, repo_only={"ok.txt"})
    tmp_root = tmp_path / "temporary-root"

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks.compare, "_decompress_archive", decompress)
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.failure_calls) == 1
    assert len(recorder.success_calls) == 0
    message, data = recorder.failure_calls[0]
    assert message == "Source archive contains files not in GitHub checkout or with different content"
    assert isinstance(data, dict)
    assert data["invalid_count"] == 2
    assert data["invalid_paths"] == ["bad1.txt", "bad2.txt"]


@pytest.mark.asyncio
async def test_source_trees_records_failure_when_archive_root_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    decompress = DecompressRecorder()
    find_root = FindArchiveRootRecorder(root=None)
    tmp_root = tmp_path / "temporary-root"

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks.compare, "_decompress_archive", decompress)
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.failure_calls) == 1
    message, data = recorder.failure_calls[0]
    assert message == "Could not determine archive root directory for comparison"
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_source_trees_records_failure_when_decompress_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    decompress = DecompressRecorder(return_value=False)
    tmp_root = tmp_path / "temporary-root"

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks.compare, "_decompress_archive", decompress)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.failure_calls) == 1
    message, data = recorder.failure_calls[0]
    assert message == "Failed to extract source archive for comparison"
    assert isinstance(data, dict)
    assert data["archive_path"] == str(await recorder.abs_path())
    assert data["extract_dir"] == str(decompress.extract_dir)


@pytest.mark.asyncio
async def test_source_trees_records_failure_when_extra_entries_in_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    decompress = DecompressRecorder()
    find_root = FindArchiveRootRecorder(root="artifact", extra_entries=["README.txt", "extra.txt"])
    tmp_root = tmp_path / "temporary-root"

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks.compare, "_decompress_archive", decompress)
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.failure_calls) == 1
    message, data = recorder.failure_calls[0]
    assert message == "Archive contains entries outside the root directory"
    assert isinstance(data, dict)
    assert data["root"] == "artifact"
    assert data["extra_entries"] == ["README.txt", "extra.txt"]


@pytest.mark.asyncio
async def test_source_trees_reports_repo_only_sample_limited_to_five(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    recorder = RecorderStub(True)
    args = _make_args(recorder)
    payload = _make_payload()
    checkout = CheckoutRecorder()
    decompress = DecompressRecorder()
    find_root = FindArchiveRootRecorder("artifact")
    repo_only_files = {f"file{i}.txt" for i in range(10)}
    compare = CompareRecorder(repo_only=repo_only_files)
    tmp_root = tmp_path / "temporary-root"

    monkeypatch.setattr(atr.tasks.checks.compare, "_load_tp_payload", PayloadLoader(payload))
    monkeypatch.setattr(atr.tasks.checks.compare, "_checkout_github_source", checkout)
    monkeypatch.setattr(atr.tasks.checks.compare, "_decompress_archive", decompress)
    monkeypatch.setattr(atr.tasks.checks.compare, "_find_archive_root", find_root)
    monkeypatch.setattr(atr.tasks.checks.compare, "_compare_trees", compare)
    monkeypatch.setattr(atr.tasks.checks.compare.paths, "get_tmp_dir", ReturnValue(tmp_root))

    await atr.tasks.checks.compare.source_trees(args)

    assert len(recorder.success_calls) == 1
    _message, data = recorder.success_calls[0]
    assert isinstance(data, dict)
    assert data["repo_only_count"] == 10
    assert len(data["repo_only_paths_sample"]) == 5


@pytest.mark.asyncio
async def test_source_trees_skips_when_not_source(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = RecorderStub(False)
    args = _make_args(recorder)

    monkeypatch.setattr(
        atr.tasks.checks.compare, "_load_tp_payload", RaiseAsync("_load_tp_payload should not be called")
    )

    await atr.tasks.checks.compare.source_trees(args)


def _make_args(recorder: atr.tasks.checks.Recorder) -> atr.tasks.checks.FunctionArguments:
    return atr.tasks.checks.FunctionArguments(
        recorder=RecorderFactory(recorder),
        asf_uid="test",
        project_name="project",
        version_name="version",
        revision_number="00001",
        primary_rel_path="artifact.tar.gz",
        extra_args={},
    )


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
        "exp": 1,
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
