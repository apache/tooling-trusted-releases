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
import sys
import unittest.mock as mock

import pytest

import atr.config as config
import atr.svn as svn


def _svn_info() -> svn.SvnInfo:
    return svn.SvnInfo(
        path="project",
        name="project",
        url="https://example.invalid/project",
        relative_url="^/project",
        repository_root="https://example.invalid",
        revision="43",
        last_changed_author="alice",
        last_changed_rev="42",
        last_changed_date="2026-05-01 00:00:00 +0000",
    )


def test_error_message_falls_back_to_sanitised_first_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config.get(), "SVN_PUBLISH_URL", "https://internal.example.invalid/repos/dist/atr", raising=False
    )
    exc = svn.CommandExecutionError(
        1, "svn: E999999: Strange failure at 'https://internal.example.invalid/repos/dist/atr/project'\nmore detail"
    )
    message = svn.error_message(exc)
    assert "internal.example.invalid" not in message
    assert "Strange failure" in message
    assert "more detail" not in message


def test_error_message_maps_connection_error() -> None:
    exc = svn.CommandExecutionError(1, "svn: E170013: Unable to connect to a repository at URL 'https://example'")
    message = svn.error_message(exc)
    assert "could not be reached" in message
    assert "E170013" in message
    assert "see https://status.apache.org/" in message


def test_error_message_reports_timeout() -> None:
    message = svn.error_message(svn.CommandTimeoutError(240.0))
    assert "timed out after 240 seconds" in message
    assert "see https://status.apache.org/" in message


def test_error_message_uses_specific_stacked_error() -> None:
    exc = svn.CommandExecutionError(
        1,
        "svn: E170013: Unable to connect to a repository\nsvn: E215004: Authentication failed",
    )
    message = svn.error_message(exc)
    assert "Authentication" in message
    assert "E215004" in message
    assert "status.apache.org" not in message


async def test_publish_revision_matches_exact_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    log_output = """
    <log>
      <logentry revision="42">
        <author>alice</author>
        <date>2026-05-01T00:00:00.000000Z</date>
        <paths><path action="A" kind="dir">/project</path></paths>
        <msg>Publish project-1.0.0</msg>
      </logentry>
    </log>
    """
    run = mock.AsyncMock(side_effect=[log_output, "atr"])
    monkeypatch.setattr(svn, "_run_svn_command", run)

    matches = await svn.publish_revision_matches(_svn_info(), "alice", "Publish project-1.0.0")

    assert matches
    assert run.await_count == 2


async def test_publish_revision_rejects_provenance_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    valid_log = """
    <log>
      <logentry revision="42">
        <author>alice</author>
        <date>2026-05-01T00:00:00.000000Z</date>
        <paths><path action="A" kind="dir">/project</path></paths>
        <msg>Publish project-1.0.0</msg>
      </logentry>
    </log>
    """
    invalid_logs = [
        valid_log.replace("<author>alice</author>", "<author>bob</author>"),
        valid_log.replace("Publish project-1.0.0", "Publish another release"),
        valid_log.replace('action="A"', 'action="M"'),
        valid_log.replace('revision="42"', 'revision="41"'),
    ]
    for log_output in invalid_logs:
        monkeypatch.setattr(svn, "_run_svn_command", mock.AsyncMock(return_value=log_output))
        assert not await svn.publish_revision_matches(_svn_info(), "alice", "Publish project-1.0.0")

    run = mock.AsyncMock(side_effect=[valid_log, "another-tool"])
    monkeypatch.setattr(svn, "_run_svn_command", run)
    assert not await svn.publish_revision_matches(_svn_info(), "alice", "Publish project-1.0.0")


async def test_svn_info_from_url_allows_missing_name(monkeypatch: pytest.MonkeyPatch) -> None:
    output = """
Path: project
URL: https://example.invalid/project
Relative URL: ^/project
Repository Root: https://example.invalid
Revision: 42
Last Changed Author: alice
Last Changed Rev: 42
Last Changed Date: 2026-05-01 00:00:00 +0000
""".strip()
    monkeypatch.setattr(svn, "_run_svn_info", mock.AsyncMock(return_value=output))

    info = await svn.SvnInfo.from_url("https://example.invalid/project")

    assert info.name is None


async def test_run_command_times_out() -> None:
    with pytest.raises(svn.CommandTimeoutError):
        await svn.run_command("sleep", "5", timeout_seconds=0.1)


async def test_run_command_timeout_drains_output() -> None:
    child = "import os, time; os.write(1, b'x' * 1_000_000); time.sleep(5)"
    with pytest.raises(svn.CommandTimeoutError):
        await asyncio.wait_for(
            svn.run_command(sys.executable, "-c", child, timeout_seconds=0.1),
            timeout=2,
        )
