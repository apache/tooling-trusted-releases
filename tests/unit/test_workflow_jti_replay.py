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

import pytest
import sqlalchemy.exc

import atr.models.github as github
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.ssh as ssh

# Comfortably beyond the key's own twenty-minute lifetime
_DISTANT_EXPIRY = 2**31 - 1


@pytest.mark.asyncio
async def test_recorded_token_expiry_follows_the_token_rather_than_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh.util, "key_ssh_fingerprint", lambda _key: "SHA256:fingerprint")
    data = _data()
    writer = _writer(data)
    payload = _payload(exp=_DISTANT_EXPIRY)

    _, key_expires = await writer.add_workflow_key(
        "test-user", 12345, safe.ProjectKey("alpha-one"), "ssh-ed25519 AAAA", payload
    )

    record = _added_of_type(data, sql.WorkflowJti)[0]
    assert record.expires == _DISTANT_EXPIRY
    assert record.expires > key_expires


@pytest.mark.asyncio
async def test_token_presented_a_second_time_mints_no_further_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh.util, "key_ssh_fingerprint", lambda _key: "SHA256:fingerprint")
    data = _data()
    data.flush = mock.AsyncMock(side_effect=_integrity_error())
    writer = _writer(data)

    with pytest.raises(storage.AccessError, match="already been used"):
        await writer.add_workflow_key("test-user", 12345, safe.ProjectKey("alpha-one"), "ssh-ed25519 AAAA", _payload())

    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()
    assert not _added_of_type(data, sql.WorkflowSSHKey)


@pytest.mark.asyncio
async def test_token_presented_once_is_recorded_and_mints_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh.util, "key_ssh_fingerprint", lambda _key: "SHA256:fingerprint")
    data = _data()
    writer = _writer(data)

    fingerprint, expires = await writer.add_workflow_key(
        "test-user", 12345, safe.ProjectKey("alpha-one"), "ssh-ed25519 AAAA", _payload()
    )

    assert fingerprint == "SHA256:fingerprint"
    assert expires > 0
    data.rollback.assert_not_awaited()
    data.commit.assert_awaited_once()
    recorded = _added_of_type(data, sql.WorkflowJti)
    assert [record.jti for record in recorded] == ["test-jti"]
    assert _added_of_type(data, sql.WorkflowSSHKey)


def _added_of_type(data: mock.MagicMock, model: type) -> list:
    return [call.args[0] for call in data.add.call_args_list if isinstance(call.args[0], model)]


def _data() -> mock.MagicMock:
    data = mock.MagicMock()
    data.add = mock.MagicMock()
    data.execute = mock.AsyncMock()
    data.flush = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    return data


def _integrity_error() -> sqlalchemy.exc.IntegrityError:
    return sqlalchemy.exc.IntegrityError("INSERT INTO workflowjti", {}, Exception("UNIQUE constraint failed"))


def _payload(**overrides: object) -> github.TrustedPublisherPayload:
    defaults: dict[str, object] = {
        "actor": "test-user",
        "actor_id": 12345,
        "aud": "https://atr.example/",
        "base_ref": "",
        "check_run_id": "1",
        "enterprise": "the-asf",
        "enterprise_id": "212555",
        "event_name": "workflow_dispatch",
        "head_ref": "",
        "iat": 0,
        "iss": "https://token.actions.githubusercontent.com",
        "job_workflow_ref": "apache/test/.github/workflows/x.yml@refs/heads/main",
        "job_workflow_sha": "0" * 40,
        "jti": "test-jti",
        "ref": "refs/heads/main",
        "ref_protected": "false",
        "ref_type": "branch",
        "repository": "apache/test",
        "repository_owner": "apache",
        "repository_visibility": "public",
        "run_attempt": "1",
        "run_number": "1",
        "runner_environment": "github-hosted",
        "sha": "0" * 40,
        "sub": "repo:apache/test:ref:refs/heads/main",
        "workflow": "test",
        "workflow_ref": "apache/test/.github/workflows/x.yml@refs/heads/main",
        "workflow_sha": "0" * 40,
    }
    defaults.update(overrides)
    return github.TrustedPublisherPayload.model_validate(defaults)


def _writer(data: mock.MagicMock) -> ssh.CommitteeParticipant:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "tester"
    return ssh.CommitteeParticipant(write, mock.MagicMock(), data, "alpha")
