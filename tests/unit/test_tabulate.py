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
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.tabulate as tabulate


@pytest.mark.asyncio
async def test_vote_committee_returns_project_committee_for_non_podling_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committee = SimpleNamespace(is_podling=False)
    release = _make_release(committee=committee, podling_thread_id=None)

    monkeypatch.setattr(tabulate.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(tabulate.db, "session", _unexpected_db_session)

    result = await tabulate.vote_committee("threadid", release)

    assert result is committee


@pytest.mark.asyncio
async def test_vote_committee_returns_project_committee_for_podling_round_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committee = SimpleNamespace(is_podling=True)
    release = _make_release(committee=committee, podling_thread_id=None)

    monkeypatch.setattr(tabulate.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(tabulate.db, "session", _unexpected_db_session)

    result = await tabulate.vote_committee("threadid", release)

    assert result is committee


@pytest.mark.asyncio
async def test_vote_committee_returns_incubator_for_podling_round_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committee = SimpleNamespace(is_podling=True)
    incubator = SimpleNamespace(key="incubator", is_podling=False)
    release = _make_release(committee=committee, podling_thread_id="round-one-thread")
    query = mock.AsyncMock()
    query.get = mock.AsyncMock(return_value=incubator)
    data = mock.MagicMock()
    data.committee = mock.MagicMock(return_value=query)

    monkeypatch.setattr(tabulate.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(tabulate.db, "session", lambda: _mock_db_session(data))

    result = await tabulate.vote_committee("threadid", release)

    assert result is incubator
    data.committee.assert_called_once_with(key="incubator")
    query.get.assert_awaited_once()


def test_vote_resolution_body_votes_formats_plural_binding_summary() -> None:
    summary = {
        "binding_votes": 9,
        "binding_votes_yes": 8,
        "binding_votes_no": 0,
        "binding_votes_abstain": 1,
    }

    body_lines = list(tabulate._vote_resolution_body_votes({}, summary))

    assert body_lines[2] == "Of these binding votes, 8 were +1, 0 were -1, and 1 was 0."


def test_vote_resolution_body_votes_formats_singular_binding_summary() -> None:
    summary = {
        "binding_votes": 9,
        "binding_votes_yes": 8,
        "binding_votes_no": 1,
        "binding_votes_abstain": 0,
    }

    body_lines = list(tabulate._vote_resolution_body_votes({}, summary))

    assert body_lines[2] == "Of these binding votes, 8 were +1, 1 was -1, and 0 were 0."


def _make_release(*, committee: SimpleNamespace, podling_thread_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        project=SimpleNamespace(committee=committee),
        podling_thread_id=podling_thread_id,
    )


@contextlib.asynccontextmanager
async def _mock_db_session(data: mock.MagicMock):
    yield data


def _unexpected_db_session() -> None:
    raise AssertionError("db.session should not be called")
