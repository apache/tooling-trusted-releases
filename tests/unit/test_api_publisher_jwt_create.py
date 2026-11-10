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
import inspect
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.api as api
import atr.jwtoken as jwtoken
import atr.models.safe as safe
import atr.storage as storage


@pytest.mark.asyncio
async def test_publisher_jwt_create_exchanges_as_a_committee_member_of_the_compose_project(monkeypatch) -> None:
    payload = SimpleNamespace(actor="alice-gh", actor_id=7)
    project = SimpleNamespace(safe_key=safe.ProjectKey("example"), committee=SimpleNamespace(key="example"))
    lookup = mock.AsyncMock(return_value=("alice", project))
    wacm = mock.MagicMock()
    wacm.tokens.issue_workflow_jwt = mock.AsyncMock(
        return_value=jwtoken.ProjectToken(jwt="token", jti="jti", expires=99)
    )
    write = mock.MagicMock()
    write.return_value.__aenter__.return_value = wacm
    monkeypatch.setattr(
        api.api.auth, "trusted_publisher_context", lambda: SimpleNamespace(payload=payload, asf_uid="alice")
    )
    monkeypatch.setattr(api.interaction, "trusted_project_for_payload", lookup)
    monkeypatch.setattr(storage, "write_as_committee_member", write)
    limit = mock.AsyncMock()
    monkeypatch.setattr(api.ratelimit, "check", limit)
    source_commit = safe.CommitHash("d" * 40)

    response, status = await inspect.unwrap(api.publisher_jwt_create)(
        "publisher/jwt/create", SimpleNamespace(source_commit=source_commit, declare_sources=False)
    )

    assert status == 200
    assert response == {
        "endpoint": "/publisher/jwt/create",
        "asfuid": "alice",
        "project": "example",
        "jwt": "token",
        "expires": 99,
    }
    lookup.assert_awaited_once_with(payload, "alice", api.interaction.TrustedProjectPhase.COMPOSE)
    write.assert_called_once_with("example", "alice")
    limit.assert_awaited_once_with("publisher/jwt/create", "example:alice", 30, datetime.timedelta(hours=1))
    wacm.tokens.issue_workflow_jwt.assert_awaited_once_with(
        "alice-gh", 7, safe.ProjectKey("example"), payload, source_commit=source_commit, declare_sources=False
    )
