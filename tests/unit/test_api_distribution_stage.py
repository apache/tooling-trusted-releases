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
import contextlib
import inspect
import unittest.mock as mock
from types import SimpleNamespace
from typing import Any

import pytest
import quart
import werkzeug.exceptions as exceptions

import atr.api as api
import atr.blueprints.api_auth as api_auth
import atr.models.api as models_api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage

_PROJECT = safe.ProjectKey("example")


def test_args_carry_the_project_where_a_project_token_is_checked() -> None:
    app = quart.Quart(__name__)

    async def authorize(project: str) -> None:
        async with app.test_request_context("/"):
            quart.g.jwt_claims = {"sub": "alice", "atr_prj": "example"}
            api_auth.authorize_project(api_auth.Auth.BEARER_PROJECT, [_args(project=project)])

    asyncio.run(authorize("example"))
    with pytest.raises(api_auth.errors.ASFQuartException):
        asyncio.run(authorize("other"))


@pytest.mark.asyncio
async def test_embargoed_release_is_left_to_the_writer_for_a_personal_token(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _patch(monkeypatch, _release(expedited=True))

    await _call({"sub": "alice"})

    writer.record_from_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_embargoed_and_voting_releases_get_one_answer_for_a_project_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = set()
    for release in (None, _release(expedited=True), _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE)):
        writer = _patch(monkeypatch, release)
        with pytest.raises(exceptions.HTTPException) as refused:
            await _call({"sub": "alice", "atr_prj": "example"})
        answers.add((refused.value.code, refused.value.description))
        writer.record_from_data.assert_not_awaited()

    assert len(answers) == 1


@pytest.mark.asyncio
async def test_record_is_always_a_staging_one(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _patch(monkeypatch, _release())

    response, status = await _call({"sub": "alice", "atr_prj": "example"})

    assert status == 200
    assert response == {"endpoint": "/distribution/stage", "success": True}
    release_key, staging, dd = writer.record_from_data.await_args.args
    assert (str(release_key), staging) == ("example-1.0.0", True)
    assert (str(dd.package), str(dd.version)) == ("example", "1.0.0")


@pytest.mark.asyncio
async def test_writer_refusal_keeps_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = _patch(monkeypatch, _release())
    writer.record_from_data.side_effect = storage.AccessError(
        "A staging distribution can only be recorded during compose", status=409
    )

    with pytest.raises(exceptions.Conflict, match="only be recorded during compose"):
        await _call({"sub": "alice", "atr_prj": "example"})


def _args(**fields: Any) -> models_api.DistributionStageArgs:
    return models_api.DistributionStageArgs.model_validate(
        {
            "project": "example",
            "version": "1.0.0",
            "platform": "PYPI",
            "distribution_package": "example",
            "distribution_version": "1.0.0",
            "details": False,
            **fields,
        }
    )


async def _call(claims: dict[str, str]) -> Any:
    app = quart.Quart(__name__)
    async with app.test_request_context("/"):
        quart.g.jwt_claims = claims
        return await inspect.unwrap(api.distribution_stage)("distribution/stage", _args())


def _patch(monkeypatch: pytest.MonkeyPatch, release: sql.Release | None) -> mock.MagicMock:
    data = mock.MagicMock()
    data.release.return_value.get = mock.AsyncMock(return_value=release)
    session = mock.AsyncMock()
    session.__aenter__.return_value = data
    monkeypatch.setattr(api.db, "session", mock.MagicMock(return_value=session))
    monkeypatch.setattr(api, "_jwt_asf_uid", lambda: "alice")
    writer = mock.MagicMock()
    writer.record_from_data = mock.AsyncMock(return_value=(mock.MagicMock(), True, mock.MagicMock()))

    @contextlib.asynccontextmanager
    async def write_as(_project_key: safe.ProjectKey, _asf_uid: str):
        yield SimpleNamespace(distributions=writer)

    monkeypatch.setattr(api.storage, "write_as_project_release_manager", write_as)
    return writer


def _release(
    phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, expedited: bool = False
) -> sql.Release:
    return sql.Release(key="example-1.0.0", project_key="example", version="1.0.0", phase=phase, expedited=expedited)
