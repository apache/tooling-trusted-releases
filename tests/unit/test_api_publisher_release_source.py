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

import inspect
import unittest.mock as mock
from types import SimpleNamespace

import pytest
import werkzeug.exceptions as exceptions

import atr.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage

_PROJECT = safe.ProjectKey("example")
_VERSION = safe.VersionKey("1.0.0")


@pytest.mark.asyncio
async def test_a_used_token_is_refused_before_anything_is_declared(monkeypatch) -> None:
    warm = _patch(monkeypatch, _release())
    warm.tokens.consume_workflow_jti.side_effect = storage.AccessError("already been used", status=409)

    with pytest.raises(exceptions.Conflict):
        await _declare()

    warm.release.set_archive_sources.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_embargoed_and_voting_releases_get_one_answer_before_the_token_is_spent(monkeypatch) -> None:
    # Were the token spent in only some of these, sending it a second time would tell them apart
    answers = set()
    for release in (None, _release(expedited=True), _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE)):
        warm = _patch(monkeypatch, release)
        with pytest.raises(exceptions.HTTPException) as refused:
            await _declare()
        answers.add((refused.value.code, refused.value.description))
        warm.tokens.consume_workflow_jti.assert_not_awaited()
        warm.release.set_archive_sources.assert_not_awaited()

    assert len(answers) == 1


@pytest.mark.asyncio
async def test_sources_are_declared_once_per_token_as_a_release_manager(monkeypatch) -> None:
    warm = _patch(monkeypatch, _release())
    write = storage.write_as_project_release_manager

    _response, status = await _declare()

    assert status == 201
    write.assert_called_once_with(_PROJECT, "alice")
    api.ratelimit.check.assert_awaited_once()
    assert api.ratelimit.check.await_args.args[:2] == ("publisher/release/source", "example:alice")
    warm.tokens.consume_workflow_jti.assert_awaited_once()
    warm.release.set_archive_sources.assert_awaited_once_with(_PROJECT, _VERSION, ["source"], None)


async def _declare() -> tuple[dict, int]:
    return await inspect.unwrap(api.publisher_release_source)(
        "publisher/release/source",
        SimpleNamespace(project=_PROJECT, version=_VERSION, sources=["source"], expected_revision=None),
    )


def _patch(monkeypatch, release: sql.Release | None) -> mock.MagicMock:
    payload = SimpleNamespace(actor="alice-gh", actor_id=7)
    project = SimpleNamespace(safe_key=_PROJECT, committee=SimpleNamespace(key="example"))
    warm = mock.MagicMock()
    warm.tokens.consume_workflow_jti = mock.AsyncMock()
    warm.release.set_archive_sources = mock.AsyncMock(return_value=SimpleNamespace(number="00002"))
    write = mock.MagicMock()
    write.return_value.__aenter__.return_value = warm
    monkeypatch.setattr(
        api.api.auth, "trusted_publisher_context", lambda: SimpleNamespace(payload=payload, asf_uid="alice")
    )
    monkeypatch.setattr(api.interaction, "compose_project_for_payload", mock.AsyncMock(return_value=("alice", project)))
    data = mock.MagicMock()
    data.release.return_value.get = mock.AsyncMock(return_value=release)
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr(api.db, "session", mock.MagicMock(return_value=session))
    monkeypatch.setattr(storage, "write_as_project_release_manager", write)
    monkeypatch.setattr(api.ratelimit, "check", mock.AsyncMock())
    monkeypatch.setattr(
        api.models.api, "PublisherReleaseSourceResults", lambda **kwargs: SimpleNamespace(model_dump=lambda mode: {})
    )
    return warm


def _release(
    phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, expedited: bool = False
) -> sql.Release:
    return sql.Release(key="example-1.0.0", project_key="example", version="1.0.0", phase=phase, expedited=expedited)
