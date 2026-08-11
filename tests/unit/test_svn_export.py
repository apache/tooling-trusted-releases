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

import pathlib
import shutil
import unittest.mock as mock

import pytest

import atr.config as config
import atr.svn as svn

requires_svn = pytest.mark.skipif(
    (shutil.which("svn") is None) or (shutil.which("svnadmin") is None),
    reason="The Subversion command line tools are not available",
)


@requires_svn
async def test_export_pinned_revision_of_deleted_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_TOKEN", "dummy", raising=False)
    repository = tmp_path / "repository"
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", f"file://{repository}", raising=False)
    await svn.run_command("svnadmin", "create", str(repository))
    url = f"file://{repository}/release"
    source = tmp_path / "source"
    source.mkdir()
    (source / "artifact.tar.gz").write_bytes(b"artifact bytes")
    await svn.run_command("svn", "import", str(source), url, "--non-interactive", "-m", "Import")
    await svn.run_command("svn", "rm", url, "--non-interactive", "-m", "Remove")
    destination = tmp_path / "export"

    await svn.export(url, 1, destination)

    assert (destination / "artifact.tar.gz").read_bytes() == b"artifact bytes"


async def test_export_pins_revision(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_TOKEN", "dummy", raising=False)
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", "file:///repo", raising=False)
    run_command = mock.AsyncMock(return_value="")
    monkeypatch.setattr(svn, "run_command", run_command)
    destination = tmp_path / "dest"

    await svn.export("file:///repo/example", 42, destination)

    run_command.assert_awaited_once_with(
        "svn",
        "export",
        "--non-interactive",
        "--ignore-externals",
        "--ignore-keywords",
        "-r",
        "42",
        "--username",
        svn.ASF_TOOL,
        "--password-from-stdin",
        "--",
        "file:///repo/example@42",
        str(destination),
        timeout_seconds=svn.EXPORT_TIMEOUT_SECONDS,
        stdin_bytes=b"dummy",
    )


async def test_export_requires_token(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_TOKEN", None, raising=False)
    run_command = mock.AsyncMock(return_value="")
    monkeypatch.setattr(svn, "run_command", run_command)

    with pytest.raises(ValueError, match="SVN_TOKEN"):
        await svn.export("file:///repo/example", 1, tmp_path / "dest")

    run_command.assert_not_awaited()


async def test_export_without_revision(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_TOKEN", "dummy", raising=False)
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", "file:///repo", raising=False)
    run_command = mock.AsyncMock(return_value="")
    monkeypatch.setattr(svn, "run_command", run_command)
    destination = tmp_path / "dest"

    await svn.export("file:///repo/example", None, destination, timeout_seconds=10.0)

    run_command.assert_awaited_once_with(
        "svn",
        "export",
        "--non-interactive",
        "--ignore-externals",
        "--ignore-keywords",
        "--username",
        svn.ASF_TOOL,
        "--password-from-stdin",
        "--",
        "file:///repo/example@",
        str(destination),
        timeout_seconds=10.0,
        stdin_bytes=b"dummy",
    )


async def test_list_files_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_TOKEN", "dummy", raising=False)
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", "file:///repo", raising=False)
    run_command = mock.AsyncMock(return_value="a.tar.gz\nsub/\nsub/b.tar.gz\n")
    monkeypatch.setattr(svn, "run_command", run_command)

    assert await svn.list_files("file:///repo/example") == ["a.tar.gz", "sub/b.tar.gz"]

    run_command.assert_awaited_once_with(
        "svn",
        "list",
        "--recursive",
        "--username",
        svn.ASF_TOOL,
        "--password-from-stdin",
        "--non-interactive",
        "file:///repo/example",
        timeout_seconds=svn.LIST_TIMEOUT_SECONDS,
        stdin_bytes=b"dummy",
    )


async def test_list_files_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_TOKEN", None, raising=False)
    run_command = mock.AsyncMock(return_value="")
    monkeypatch.setattr(svn, "run_command", run_command)

    with pytest.raises(ValueError, match="SVN_TOKEN"):
        await svn.list_files("file:///repo/example")

    run_command.assert_not_awaited()
