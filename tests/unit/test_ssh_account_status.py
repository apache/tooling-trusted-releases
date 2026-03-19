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

import unittest.mock as mock
from typing import TYPE_CHECKING

import asyncssh
import pytest

import atr.ssh as ssh

if TYPE_CHECKING:
    from pytest import MonkeyPatch


@pytest.mark.asyncio
@pytest.mark.usefixtures("_patch_ldap_active")
async def test_begin_auth_allows_active_user(monkeypatch: "MonkeyPatch"):
    server = _make_server()
    server._conn = _make_conn()
    mock_data = mock.MagicMock()
    mock_data.ssh_key.return_value.all = mock.AsyncMock(return_value=[])
    mock_session = mock.AsyncMock()
    mock_session.__aenter__.return_value = mock_data
    monkeypatch.setattr("atr.db.session", lambda: mock_session)
    result = await server.begin_auth("alice")
    assert result is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("_patch_ldap_disabled")
async def test_begin_auth_rejects_disabled_user():
    server = _make_server()
    server._conn = _make_conn()
    with pytest.raises(asyncssh.PermissionDenied, match="Account disabled"):
        await server.begin_auth("banned-user")


@pytest.mark.asyncio
async def test_begin_auth_skips_ldap_for_github(monkeypatch: "MonkeyPatch"):
    is_active_mock = mock.AsyncMock(return_value=False)
    monkeypatch.setattr("atr.ldap.is_active", is_active_mock)
    server = _make_server()
    server._conn = _make_conn()
    result = await server.begin_auth("github")
    assert result is True
    is_active_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_patch_ldap_active")
async def test_step_02_allows_active_account():
    server = _make_server()
    process = _make_process(username="alice", command="not-rsync")
    with pytest.raises(ssh.RsyncArgsError, match="The first two arguments must be rsync"):
        await ssh._step_02_handle_safely(process, server)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_patch_ldap_disabled")
async def test_step_02_rejects_disabled_account():
    server = _make_server()
    server._github_asf_uid = None
    process = _make_process(username="banned-user", command="rsync --server --sender -vlogDtpre.iLsfxCIvu . /proj/v1/")
    with pytest.raises(ssh.RsyncArgsError, match="Account disabled"):
        await ssh._step_02_handle_safely(process, server)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_patch_ldap_active")
async def test_validate_public_key_allows_active_workflow_user(monkeypatch: "MonkeyPatch"):
    server = _make_server()
    key = mock.MagicMock(spec=asyncssh.SSHKey)
    key.get_fingerprint.return_value = "SHA256:abc"
    mock_workflow_key = mock.MagicMock()
    mock_workflow_key.asf_uid = "alice"
    mock_workflow_key.revoked = False
    mock_workflow_key.expires = 9999999999
    mock_workflow_key.github_payload = {
        "actor": "alice",
        "actor_id": 1,
        "aud": "atr-test-v1",
        "base_ref": "",
        "check_run_id": "",
        "enterprise": "the-asf",
        "enterprise_id": "212555",
        "event_name": "push",
        "head_ref": "",
        "iat": 1000000000,
        "iss": "https://token.actions.githubusercontent.com",
        "job_workflow_ref": "apache/test/.github/workflows/ci.yml@refs/heads/main",
        "job_workflow_sha": "abc123",
        "jti": "x",
        "ref": "refs/heads/main",
        "ref_protected": "true",
        "ref_type": "branch",
        "repository": "apache/test",
        "repository_owner": "apache",
        "repository_visibility": "public",
        "run_attempt": "1",
        "run_number": "1",
        "runner_environment": "github-hosted",
        "sha": "abc123",
        "sub": "repo:apache/test:ref:refs/heads/main",
        "workflow": "CI",
        "workflow_ref": "apache/test/.github/workflows/ci.yml@refs/heads/main",
        "workflow_sha": "abc123",
    }
    mock_data = mock.MagicMock()
    mock_data.workflow_ssh_key.return_value.get = mock.AsyncMock(return_value=mock_workflow_key)
    mock_session = mock.AsyncMock()
    mock_session.__aenter__.return_value = mock_data
    monkeypatch.setattr("atr.db.session", lambda: mock_session)
    result = await server.validate_public_key("github", key)
    assert result is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("_patch_ldap_disabled")
async def test_validate_public_key_rejects_disabled_workflow_user(monkeypatch: "MonkeyPatch"):
    server = _make_server()
    key = mock.MagicMock(spec=asyncssh.SSHKey)
    key.get_fingerprint.return_value = "SHA256:abc"
    mock_workflow_key = mock.MagicMock()
    mock_workflow_key.asf_uid = "banned-user"
    mock_workflow_key.expires = 9999999999
    mock_data = mock.MagicMock()
    mock_data.workflow_ssh_key.return_value.get = mock.AsyncMock(return_value=mock_workflow_key)
    mock_session = mock.AsyncMock()
    mock_session.__aenter__.return_value = mock_data
    monkeypatch.setattr("atr.db.session", lambda: mock_session)
    result = await server.validate_public_key("github", key)
    assert result is False


@pytest.mark.asyncio
async def test_validate_public_key_closes_db_session_before_ldap(monkeypatch: "MonkeyPatch"):
    server = _make_server()
    key = mock.MagicMock(spec=asyncssh.SSHKey)
    key.get_fingerprint.return_value = "SHA256:abc"

    mock_workflow_key = mock.MagicMock()
    mock_workflow_key.asf_uid = "alice"
    mock_workflow_key.revoked = False
    mock_workflow_key.expires = 9999999999
    mock_workflow_key.github_payload = {
        "actor": "alice",
        "actor_id": 1,
        "aud": "atr-test-v1",
        "base_ref": "",
        "check_run_id": "",
        "enterprise": "the-asf",
        "enterprise_id": "212555",
        "event_name": "push",
        "head_ref": "",
        "iat": 1000000000,
        "iss": "https://token.actions.githubusercontent.com",
        "job_workflow_ref": "apache/test/.github/workflows/ci.yml@refs/heads/main",
        "job_workflow_sha": "abc123",
        "jti": "x",
        "ref": "refs/heads/main",
        "ref_protected": "true",
        "ref_type": "branch",
        "repository": "apache/test",
        "repository_owner": "apache",
        "repository_visibility": "public",
        "run_attempt": "1",
        "run_number": "1",
        "runner_environment": "github-hosted",
        "sha": "abc123",
        "sub": "repo:apache/test:ref:refs/heads/main",
        "workflow": "CI",
        "workflow_ref": "apache/test/.github/workflows/ci.yml@refs/heads/main",
        "workflow_sha": "abc123",
    }

    session_closed = False

    def mark_session_closed() -> None:
        nonlocal session_closed
        session_closed = True

    async def is_active(asf_uid: str) -> bool:
        assert asf_uid == "alice"
        assert session_closed
        return True

    monkeypatch.setattr("atr.ldap.is_active", is_active)
    monkeypatch.setattr("atr.db.session", lambda: _WorkflowKeySession(mock_workflow_key, mark_session_closed))

    result = await server.validate_public_key("github", key)

    assert result is True
    assert session_closed is True


def _make_conn(authorized_keys: list[str] | None = None) -> mock.MagicMock:
    conn = mock.MagicMock(spec=asyncssh.SSHServerConnection)
    conn.get_extra_info.return_value = ("127.0.0.1", 22)
    return conn


def _make_process(username: str = "alice", command: str = "") -> mock.MagicMock:
    process = mock.MagicMock(spec=asyncssh.SSHServerProcess)
    process.get_extra_info.return_value = username
    process.command = command
    process.is_closing.return_value = False
    process.stderr = mock.MagicMock()
    process.stderr.is_closing.return_value = False
    return process


def _make_server() -> ssh.SSHServer:
    server = ssh.SSHServer.__new__(ssh.SSHServer)
    server._github_asf_uid = None
    server._github_payload = None
    return server


class _WorkflowKeySession:
    def __init__(self, workflow_key: mock.MagicMock, on_exit):
        self._workflow_key = workflow_key
        self._on_exit = on_exit

    async def __aenter__(self) -> mock.MagicMock:
        data = mock.MagicMock()
        data.workflow_ssh_key.return_value.get = mock.AsyncMock(return_value=self._workflow_key)
        return data

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._on_exit()


@pytest.fixture
def _patch_ldap_active(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(return_value=True))


@pytest.fixture
def _patch_ldap_disabled(monkeypatch: "MonkeyPatch"):
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(return_value=False))
