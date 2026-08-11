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
import types
import unittest.mock as mock

import pytest

import atr.svn as svn

requires_svn = pytest.mark.skipif(
    (shutil.which("svn") is None) or (shutil.which("svnadmin") is None),
    reason="The Subversion command line tools are not available",
)


async def test_info_authenticated_allows_dist_root_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svn.config,
        "get",
        lambda: types.SimpleNamespace(SVN_TOKEN="dummy", SVN_PUBLISH_URL="file:///repo"),
    )
    run_command = mock.AsyncMock(return_value="")
    monkeypatch.setattr(svn, "run_command", run_command)

    await svn.info_authenticated("https://dist.apache.org/repos/dist/release/tooling")

    run_command.assert_awaited_once()


@requires_svn
async def test_info_authenticated_reads_local_repository(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    monkeypatch.setattr(
        svn.config,
        "get",
        lambda: types.SimpleNamespace(SVN_TOKEN="dummy", SVN_PUBLISH_URL=f"file://{repository}"),
    )
    await svn.run_command("svnadmin", "create", str(repository))

    output = await svn.info_authenticated(f"file://{repository}")

    assert f"file://{repository}" in output


async def test_info_authenticated_rejects_untrusted_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svn.config,
        "get",
        lambda: types.SimpleNamespace(SVN_TOKEN="dummy", SVN_PUBLISH_URL="https://dist.apache.org/repos/dist/atr"),
    )
    run_command = mock.AsyncMock(return_value="")
    monkeypatch.setattr(svn, "run_command", run_command)
    untrusted = [
        "https://dist.apache.org.example.net/repos/dist/atr/project",
        "https://dist.apache.org/other/atr/project",
        "https://user@dist.apache.org/repos/dist/atr/project",
        "http://dist.apache.org/repos/dist/atr/project",
        "https://dist.apache.org/repos/distant",
        "https://dist.apache.org/repos/dist/atr/project?query=1",
        "https://dist.apache.org/repos/dist/atr/project#fragment",
    ]

    for url in untrusted:
        with pytest.raises(ValueError, match="Refusing to send SVN credentials"):
            await svn.info_authenticated(url)

    run_command.assert_not_awaited()


async def test_info_authenticated_sends_token_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svn.config,
        "get",
        lambda: types.SimpleNamespace(SVN_TOKEN="dummy", SVN_PUBLISH_URL="https://svn.example.invalid/repos"),
    )
    run_command = mock.AsyncMock(return_value="")
    monkeypatch.setattr(svn, "run_command", run_command)

    await svn.info_authenticated("https://svn.example.invalid/repos/example")

    run_command.assert_awaited_once_with(
        "svn",
        "info",
        "--username",
        "atr",
        "--password-from-stdin",
        "--non-interactive",
        "https://svn.example.invalid/repos/example",
        timeout_seconds=svn.INFO_TIMEOUT_SECONDS,
        stdin_bytes=b"dummy",
    )
