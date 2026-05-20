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
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

import atr.models.tabulate as models_tabulate
import atr.tabulate as tabulate


@pytest.mark.parametrize(
    ("binding_plus_one", "binding_minus_one", "expected"),
    [
        (2, 0, False),
        (3, 3, False),
        (3, 2, True),
        (4, 0, True),
    ],
)
def test_binding_vote_passes(binding_plus_one: int, binding_minus_one: int, expected: bool) -> None:
    assert tabulate.binding_vote_passes(binding_plus_one, binding_minus_one) is expected


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


def test_vote_outcome_format_fails_when_binding_minus_one_equals_plus_one() -> None:
    """_vote_outcome_format returns (False, ...) when binding -1 >= binding +1."""
    passed, _outcome = tabulate._vote_outcome_format(None, 3, 3)

    assert passed is False


def test_vote_outcome_format_fails_when_fewer_than_three_binding_plus_one() -> None:
    """_vote_outcome_format returns (False, ...) for fewer than 3 binding +1."""
    passed, _outcome = tabulate._vote_outcome_format(None, 2, 0)

    assert passed is False


def test_vote_outcome_format_passes_with_three_binding_plus_one() -> None:
    """_vote_outcome_format returns (True, ...) for 3 binding +1 and 0 binding -1."""
    passed, _outcome = tabulate._vote_outcome_format(None, 3, 0)

    assert passed is True


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


def test_vote_resolution_body_votes_uses_formal_label() -> None:
    summary = {
        "binding_votes": 3,
        "binding_votes_yes": 3,
        "binding_votes_no": 0,
        "binding_votes_abstain": 0,
    }

    body_lines = list(tabulate._vote_resolution_body_votes({}, summary, "Formal", "Informal"))

    assert body_lines[0] == "There were 3 formal votes."
    assert body_lines[2] == "Of these formal votes, 3 were +1, 0 were -1, and 0 were 0."


@pytest.mark.asyncio
async def test_votes_excludes_receipts_by_rfc_message_id_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        del strict
        assert source is True
        yield (
            "archive-doc-receipt",
            _vote_message(
                from_raw="Receipt Voter <receipt@example.com>",
                message_id="<receipt-mid@apache.org>",
                archive_mid="archive-receipt",
                epoch="1",
            ),
        )
        yield (
            "archive-doc-keep",
            _vote_message(
                from_raw="Other Voter <other@example.com>",
                message_id="<other-mid@apache.org>",
                archive_mid="archive-other",
                epoch="2",
            ),
        )
        yield (
            "archive-doc-mid-only",
            _vote_message(
                from_raw="Archive Mid Voter <midonly@example.com>",
                message_id="<different-mid@apache.org>",
                archive_mid="receipt-mid@apache.org",
                epoch="3",
            ),
        )

    monkeypatch.setattr(tabulate.config, "is_dev_environment", lambda: False)
    lookup = tabulate.cache.EmailUidLookup(
        {
            tabulate.cache._email_uid_hash("receipt@example.com"): "receipt-voter",
            tabulate.cache._email_uid_hash("other@example.com"): "other-voter",
            tabulate.cache._email_uid_hash("midonly@example.com"): "mid-only-voter",
        }
    )
    monkeypatch.setattr(tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=lookup))
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)

    _start_unixtime, tabulated_votes = await tabulate.votes(
        None,
        "0123456789abcdef0123456789abcdef",
        excluded_message_ids={"receipt-mid@apache.org"},
    )

    assert "receipt-voter" not in tabulated_votes
    assert tabulated_votes["other-voter"].vote == models_tabulate.Vote.YES
    assert tabulated_votes["mid-only-voter"].asf_eid == "receipt-mid@apache.org"


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


def _vote_message(
    *,
    from_raw: str,
    message_id: str,
    archive_mid: str,
    epoch: str,
) -> dict[str, object]:
    return {
        "from_raw": from_raw,
        "list_raw": "dev.project.apache.org",
        "cc": "",
        "epoch": epoch,
        "subject": "Re: [VOTE] Release project 1.0.0",
        "body": "+1\n",
        "message-id": message_id,
        "mid": archive_mid,
        "date": "2026-01-01T00:00:00Z",
        "id": f"doc-{epoch}",
    }
