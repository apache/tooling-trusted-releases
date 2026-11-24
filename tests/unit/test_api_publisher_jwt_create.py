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

import contextlib
import datetime
import inspect
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.api as api
import atr.jwtoken as jwtoken
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage


@pytest.mark.asyncio
async def test_publisher_jwt_create_exchanges_as_a_release_manager_of_the_given_project(monkeypatch) -> None:
    payload = SimpleNamespace(actor="alice-gh", actor_id=7)
    project = SimpleNamespace(safe_key=safe.ProjectKey("example"), committee=SimpleNamespace(key="example"))
    lookup = mock.AsyncMock(return_value=("alice", project))
    warm = mock.MagicMock()
    warm.tokens.issue_workflow_jwt = mock.AsyncMock(
        return_value=jwtoken.ProjectToken(jwt="token", jti="jti", expires=99)
    )
    write = mock.MagicMock()
    write.return_value.__aenter__.return_value = warm
    monkeypatch.setattr(
        api.api.auth, "trusted_publisher_context", lambda: SimpleNamespace(payload=payload, asf_uid="alice")
    )
    monkeypatch.setattr(api.interaction, "compose_project_for_payload", lookup)
    monkeypatch.setattr(storage, "write_as_project_release_manager", write)
    limit = mock.AsyncMock()
    monkeypatch.setattr(api.ratelimit, "check", limit)
    source_commit = safe.CommitHash("d" * 40)

    response, status = await inspect.unwrap(api.publisher_jwt_create)(
        "publisher/jwt/create",
        SimpleNamespace(project=safe.ProjectKey("example"), source_commit=source_commit, declare_sources=False),
    )

    assert status == 200
    assert response == {
        "endpoint": "/publisher/jwt/create",
        "asfuid": "alice",
        "project": "example",
        "jwt": "token",
        "expires": 99,
    }
    lookup.assert_awaited_once_with(payload, "alice", safe.ProjectKey("example"))
    write.assert_called_once_with(safe.ProjectKey("example"), "alice")
    limit.assert_awaited_once_with("publisher/jwt/create", "example:alice", 30, datetime.timedelta(hours=1))
    warm.tokens.issue_workflow_jwt.assert_awaited_once_with(
        "alice-gh", 7, safe.ProjectKey("example"), payload, source_commit=source_commit, declare_sources=False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("release_manager", "issued"),
    [pytest.param(False, False, id="committer"), pytest.param(True, True, id="release-manager")],
)
@pytest.mark.parametrize("route", ["publisher/jwt/create", "publisher/ssh/register"])
async def test_exchange_is_refused_to_a_committer_who_is_not_a_release_manager(
    monkeypatch, route: str, release_manager: bool, issued: bool
) -> None:
    # The storage layer decides this for real here. Alice commits to the project and is not on its PMC
    committee = sql.Committee(
        key="example", committers=["alice"], committee_members=[], release_managers=["alice"] if release_manager else []
    )
    project = sql.Project(key="example", name="Example", committee=committee)
    payload = SimpleNamespace(actor="alice-gh", actor_id=7)
    authorisation = mock.MagicMock(asf_uid="alice")
    authorisation.is_member_of.return_value = False
    data = mock.MagicMock()
    data.project.return_value.demand = mock.AsyncMock(return_value=project)
    token = jwtoken.ProjectToken(jwt="token", jti="jti", expires=99)
    issue_jwt = mock.AsyncMock(return_value=token)
    add_key = mock.AsyncMock(return_value=("SHA256:fingerprint", 99))

    @contextlib.asynccontextmanager
    async def write(_asf_uid: str):
        yield storage.Write(authorisation, data)

    monkeypatch.setattr(storage, "write", write)
    monkeypatch.setattr(storage.writers.tokens.ReleaseManager, "issue_workflow_jwt", issue_jwt)
    monkeypatch.setattr(storage.writers.ssh.ReleaseManager, "add_workflow_key", add_key)
    monkeypatch.setattr(
        api.api.auth, "trusted_publisher_context", lambda: SimpleNamespace(payload=payload, asf_uid="alice")
    )
    monkeypatch.setattr(api.interaction, "compose_project_for_payload", mock.AsyncMock(return_value=("alice", project)))
    monkeypatch.setattr(api.ratelimit, "check", mock.AsyncMock())
    handler = api.publisher_jwt_create if (route == "publisher/jwt/create") else api.publisher_ssh_register
    request = SimpleNamespace(
        project=safe.ProjectKey("example"), source_commit=None, declare_sources=True, ssh_key="ssh-ed25519 AAAA"
    )

    if issued:
        _response, status = await inspect.unwrap(handler)(route, request)
        assert status == 200
        assert issue_jwt.await_count + add_key.await_count == 1
        return
    with pytest.raises(storage.AccessError) as refused:
        await inspect.unwrap(handler)(route, request)
    assert refused.value.status == 403
    issue_jwt.assert_not_awaited()
    add_key.assert_not_awaited()
