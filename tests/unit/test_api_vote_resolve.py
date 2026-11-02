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
import inspect
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.api as api
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage


class MockQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    async def get(self) -> object:
        return self._value


class MockDBSession:
    def __init__(self, release: object) -> None:
        self._release = release

    def release(self, **kwargs: object) -> MockQuery:
        return MockQuery(self._release)


@pytest.mark.asyncio
async def test_publisher_vote_resolve_expects_email_vote_for_solr_mcp(monkeypatch) -> None:
    _patch_release(monkeypatch, sql.VoteMode.EMAIL)
    warm = mock.MagicMock()
    warm.vote.resolve = mock.AsyncMock(return_value=(None, None, "", None))
    write = mock.MagicMock()
    write.return_value.__aenter__.return_value = warm
    context = SimpleNamespace(payload={}, asf_uid="user")
    project = SimpleNamespace(safe_key=safe.ProjectKey("solr-mcp"))
    monkeypatch.setattr(api.api.auth, "trusted_publisher_context", lambda: context)
    monkeypatch.setattr(api.interaction, "trusted_project_for_payload", mock.AsyncMock(return_value=("user", project)))
    monkeypatch.setattr(storage, "write_as_project_release_manager", write)

    await inspect.unwrap(api.publisher_vote_resolve)(
        "publisher/vote/resolve", SimpleNamespace(version=safe.VersionKey("1.0.0"), resolution="passed")
    )

    assert warm.vote.resolve.await_args.kwargs["expected_vote_mode"] == sql.VoteMode.EMAIL


@pytest.mark.asyncio
async def test_vote_resolve_release_accepts_email_vote_for_solr_mcp(monkeypatch) -> None:
    release = _patch_release(monkeypatch, sql.VoteMode.EMAIL)

    assert await api._vote_resolve_release(safe.ProjectKey("solr-mcp"), safe.VersionKey("1.0.0"), "user") is release


@pytest.mark.asyncio
async def test_vote_resolve_release_accepts_trusted_vote(monkeypatch) -> None:
    release = _patch_release(monkeypatch, sql.VoteMode.TRUSTED)

    assert await api._vote_resolve_release(safe.ProjectKey("example"), safe.VersionKey("1.0.0"), "user") is release


@pytest.mark.asyncio
async def test_vote_resolve_release_rejects_email_vote(monkeypatch) -> None:
    _patch_release(monkeypatch, sql.VoteMode.EMAIL)

    with pytest.raises(storage.AccessError, match="only available in Trusted Vote mode") as exc_info:
        await api._vote_resolve_release(safe.ProjectKey("example"), safe.VersionKey("1.0.0"), "user")

    assert exc_info.value.status == 403


def _patch_release(monkeypatch: pytest.MonkeyPatch, vote_mode: sql.VoteMode) -> SimpleNamespace:
    release = SimpleNamespace(effective_vote_mode=vote_mode, is_embargoed=False)

    @contextlib.asynccontextmanager
    async def fake_session():
        yield MockDBSession(release)

    monkeypatch.setattr(db, "session", fake_session)
    return release
