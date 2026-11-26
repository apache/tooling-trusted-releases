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
import unittest.mock as mock
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

import atr.db.interaction as interaction
import atr.models.sql as sql
import atr.models.tabulate as models_tabulate
import atr.tabulate as tabulate
import atr.user as user
import atr.util as util


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
async def test_email_for_confirmation_clamps_future_date_without_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        del strict, source
        yield (
            "doc-future",
            {
                "from_raw": "Personal <personal@example.org>",
                "list_raw": "dev.project.apache.org",
                "cc": "",
                "epoch": "4102444800",
                "subject": "Re: [VOTE] Release project 1.0.0",
                "body": "+1\n",
                "message-id": "<future@example.org>",
                "mid": "archive-future",
                "date": "Fri, 01 Jan 2099 00:00:00 +0000",
                "received": [],
                "received_spf": [],
            },
        )

    monkeypatch.setattr(
        tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=tabulate.cache.EmailUidLookup({}))
    )
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)

    before = datetime.datetime.now(datetime.UTC)
    result = await tabulate.email_for_confirmation("0123456789abcdef0123456789abcdef", "archive-future")
    after = datetime.datetime.now(datetime.UTC)

    assert before <= result.email_timestamp <= after
    assert result.voter_asf_uid is None
    assert result.voter_email == "personal@example.org"
    assert result.voter_fullname == "Personal"


@pytest.mark.asyncio
async def test_email_for_confirmation_rejects_root_receipt_and_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "0123456789abcdef0123456789abcdef"

    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        del strict, source
        root = _vote_message(
            from_raw="RM <rm@apache.org>",
            message_id="<root@apache.org>",
            archive_mid=thread_id,
            epoch="1",
        )
        root["subject"] = "[VOTE] Release project 1.0.0"
        yield ("doc-root", root)
        yield (
            "doc-receipt",
            _vote_message(
                from_raw="Voter <voter@apache.org>",
                message_id="<receipt-1@apache.org>",
                archive_mid="archive-receipt",
                epoch="2",
            ),
        )

    monkeypatch.setattr(
        tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=tabulate.cache.EmailUidLookup({}))
    )
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)

    with pytest.raises(interaction.InteractionError, match="opening vote message"):
        await tabulate.email_for_confirmation(thread_id, thread_id)

    with pytest.raises(interaction.InteractionError, match="receipt"):
        await tabulate.email_for_confirmation(
            thread_id,
            "archive-receipt",
            excluded_message_ids={" <receipt-1@apache.org> "},
        )

    with pytest.raises(interaction.InteractionError, match="not found"):
        await tabulate.email_for_confirmation(thread_id, "archive-missing")


@pytest.mark.asyncio
async def test_email_for_confirmation_unmapped_sender_and_fullname_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        del strict, source
        yield (
            "doc-1",
            {
                "from_raw": "<contrib@example.org>",
                "list_raw": "dev.project.apache.org",
                "cc": "",
                "epoch": "1767225600",
                "subject": "Re: [VOTE] Release project 1.0.0",
                "body": "+1\n",
                "message-id": "<contrib@example.org>",
                "mid": "archive-contrib",
                "date": "Thu, 01 Jan 2026 00:00:00 +0000",
                "received": [],
                "received_spf": [],
            },
        )

    monkeypatch.setattr(
        tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=tabulate.cache.EmailUidLookup({}))
    )
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)

    result = await tabulate.email_for_confirmation("0123456789abcdef0123456789abcdef", "archive-contrib")

    assert result.voter_asf_uid is None
    assert result.voter_email == "contrib@example.org"
    assert result.voter_fullname == "contrib"
    assert result.email_timestamp == datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.UTC)


@pytest.mark.asyncio
async def test_email_for_confirmation_uses_topmost_received_header_from_raw_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_email = (
        b"From: Alice <alice@apache.org>\r\n"
        b"Subject: Re: [VOTE] Release project 1.0.0\r\n"
        b"Message-ID: <reply-1@apache.org>\r\n"
        b"Date: Wed, 01 Jan 2099 12:00:00 +0000\r\n"
        b"Received: from mx.apache.org by lists.apache.org with ESMTPS; Thu, 01 Jan 2026 10:30:00 +0200\r\n"
        b"Received: from client.local by mx.apache.org with SMTP; Thu, 01 Jan 2026 07:00:00 +0000\r\n"
        b"\r\n"
        b"+1\r\n"
    )
    parsed_msg = util._thread_message_from_source("archive-reply-1", raw_email)

    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        assert strict is True
        assert source is True
        yield ("archive-reply-1", parsed_msg)

    monkeypatch.setattr(
        tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=tabulate.cache.EmailUidLookup({}))
    )
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)

    result = await tabulate.email_for_confirmation("0123456789abcdef0123456789abcdef", "archive-reply-1")

    assert result.asf_eid == "archive-reply-1"
    assert result.voter_asf_uid == "alice"
    assert result.voter_email == "alice@apache.org"
    assert result.voter_fullname == "Alice"
    assert result.email_timestamp == datetime.datetime(2026, 1, 1, 8, 30, tzinfo=datetime.UTC)


@pytest.mark.parametrize("line", ["+0.5", "-0.5", "+.5", "+0.9 from me", "so here is my +0.5 :)", "+1.5"])
def test_vote_castings_counts_a_fractional_vote_as_abstain(line: str) -> None:
    assert tabulate._vote_castings(line) == [(models_tabulate.Vote.ABSTAIN, line)]


def test_vote_castings_quotes_the_fractional_line_of_a_real_email() -> None:
    body = "\n".join(
        [
            "I have some concerns regarding this release",
            "",
            "so here is my +0.5 :)",
            "",
            "hashes/signatures + local build are OK",
            "",
            "PS voting via email due to ATR only have +1/0/-1",
            "",
            "On Tue, 17 Nov 2026 at 14:17, Somebody via dev",
            "> +1",
        ]
    )

    assert tabulate._vote_castings(body) == [(models_tabulate.Vote.ABSTAIN, "so here is my +0.5 :)")]


@pytest.mark.parametrize("line", ["+0.5, would be +1 with the NOTICE fixed", "ATR only has +1/0/-1", "+1/-1"])
def test_vote_castings_skips_a_line_naming_several_votes(line: str) -> None:
    assert tabulate._vote_castings(line) == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("+1 (binding)", models_tabulate.Vote.YES),
        ("+1.", models_tabulate.Vote.YES),
        ("- cargo +1.86.0 test --workspace", models_tabulate.Vote.YES),
        ("-1, the LICENSE is missing", models_tabulate.Vote.NO),
        ("+0", models_tabulate.Vote.ABSTAIN),
    ],
)
def test_vote_castings_still_reads_whole_votes(line: str, expected: models_tabulate.Vote) -> None:
    assert tabulate._vote_castings(line) == [(expected, line)]


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


def test_vote_result_subject_formats_result_before_vote() -> None:
    release = SimpleNamespace(project=SimpleNamespace(display_name="Project"), version="1.0.0")

    assert tabulate.vote_result_subject(release, "passed") == "[RESULT] [VOTE] Release Project 1.0.0 PASSED"


def test_vote_tally_block_includes_provenance_and_uses_ppmc_binding_label() -> None:
    summary = interaction.CountedVoteSummary(binding_votes_yes=3, non_binding_votes_no=1)
    ballots = [
        interaction.CountedVoteDetail(
            cast_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            choice=sql.VoteChoice.YES,
            is_binding=True,
            receipt_message_id="r1@apache.org",
            source="atr",
            status_label="PPMC Member (binding)",
            voter_asf_uid="ppmc",
            voter_fullname="PPMC Voter",
            vote_round=1,
        ),
        interaction.CountedVoteDetail(
            asf_eid="eid-1",
            cast_at=datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
            choice=sql.VoteChoice.NO,
            confirmed_by="ppmc",
            is_binding=False,
            source="email",
            status_label="Contributor (non-binding)",
            voter_asf_uid=None,
            voter_email="contrib@example.org",
            voter_fullname="Contributor",
            vote_round=1,
        ),
    ]

    tally = tabulate.vote_tally_block(summary, *user.binding_terminology(1), ballots=ballots)

    assert tally.splitlines() == [
        "+1 ppmc (PPMC-binding)",
        "-1 contrib@example.org (non-binding, email confirmed)",
        "",
        "There were 3 PPMC-binding votes.",
        "PPMC-binding votes: +1: 3, 0: 0, -1: 0.",
        "Non-binding votes: +1: 0, 0: 0, -1: 1.",
    ]


@pytest.mark.asyncio
async def test_votes_applies_interpretation_to_its_email_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        del strict, source
        for voter in ("one", "two"):
            message = _vote_message(
                from_raw=f"Voter <{voter}@apache.org>",
                message_id=f"<{voter}@apache.org>",
                archive_mid=f"archive-{voter}",
                epoch="1",
            )
            message["body"] = "+1 (binding)\n\n- cargo +1.86.0 test --workspace\n"
            yield f"archive-doc-{voter}", message

    monkeypatch.setattr(tabulate.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(
        tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=tabulate.cache.EmailUidLookup({}))
    )
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)

    _start_unixtime, tabulated_votes = await tabulate.votes(
        None,
        "0123456789abcdef0123456789abcdef",
        interpretations={"archive-one": sql.VoteChoice.YES},
    )

    assert tabulated_votes["one"].vote == models_tabulate.Vote.YES
    assert tabulated_votes["one"].confirmed is True
    assert tabulated_votes["two"].vote == models_tabulate.Vote.UNKNOWN
    assert tabulated_votes["two"].confirmed is False


@pytest.mark.asyncio
async def test_votes_does_not_treat_a_fractional_pmc_vote_as_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        del strict, source
        for voter in ("one", "two", "three"):
            message = _vote_message(
                from_raw=f"Voter <{voter}@apache.org>",
                message_id=f"<{voter}@apache.org>",
                archive_mid=f"archive-{voter}",
                epoch="1",
            )
            message["body"] = "so here is my +0.5 :)\n"
            yield f"archive-doc-{voter}", message

    monkeypatch.setattr(tabulate.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(
        tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=tabulate.cache.EmailUidLookup({}))
    )
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)
    committee = sql.Committee(key="example", committee_members=["one", "two"], committers=["one", "two", "three"])
    interp_two = sql.VoteInterpretation(
        release_key="example-1.0.0",
        vote_seq=1,
        asf_eid="archive-two",
        voter_asf_uid="two",
        voter_email="two@apache.org",
        voter_fullname="Voter",
        choice=sql.VoteChoice.YES,
        is_binding_at_confirm=True,
        email_timestamp=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        confirmed_by="one",
    )

    _start_unixtime, tabulated_votes = await tabulate.votes(
        committee,
        "0123456789abcdef0123456789abcdef",
        interpretations={"archive-two": interp_two},
    )

    assert tabulated_votes["one"].vote == models_tabulate.Vote.ABSTAIN
    assert tabulated_votes["one"].fraction == "+0.5"
    assert tabulated_votes["one"].status == models_tabulate.VoteStatus.FRACTIONAL
    assert tabulated_votes["one"].confirmed is False
    # Confirming an interpretation stands as a binding vote with confirmed metadata
    assert tabulated_votes["two"].fraction is None
    assert tabulated_votes["two"].vote == models_tabulate.Vote.YES
    assert tabulated_votes["two"].status == models_tabulate.VoteStatus.BINDING
    assert tabulated_votes["two"].confirmed is True
    assert tabulated_votes["two"].confirmed_by == "one"
    assert tabulated_votes["two"].asf_uid == "two"
    assert tabulated_votes["two"].unixtime == 1
    assert tabulated_votes["three"].fraction == "+0.5"
    assert tabulated_votes["three"].status == models_tabulate.VoteStatus.COMMITTER

    summary = tabulate.vote_summary(tabulated_votes)
    assert summary["binding_votes"] == 1
    assert summary["non_binding_votes_abstain"] == 2


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


@pytest.mark.asyncio
async def test_votes_overlays_confirmed_reply_without_heuristic_vote_and_skips_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "0123456789abcdef0123456789abcdef"

    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        del strict, source
        root = _vote_message(
            from_raw="Release Manager <rm@apache.org>",
            message_id="<root@apache.org>",
            archive_mid=thread_id,
            epoch="100",
        )
        root["subject"] = "[VOTE] Release project 1.0.0"
        root["body"] = "+1\n"
        yield ("doc-root", root)

        reply = _vote_message(
            from_raw="Alice <alice@apache.org>",
            message_id="<reply@apache.org>",
            archive_mid="archive-alice",
            epoch="200",
        )
        reply["body"] = "LGTM, ship it!"
        yield ("doc-alice", reply)

        result_msg = _vote_message(
            from_raw="Release Manager <rm@apache.org>",
            message_id="<result@apache.org>",
            archive_mid="archive-result",
            epoch="300",
        )
        result_msg["subject"] = "[RESULT] [VOTE] Release project 1.0.0 PASSED"
        result_msg["body"] = "+1\n"
        yield ("doc-result", result_msg)

    monkeypatch.setattr(tabulate.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(
        tabulate.cache, "email_uid_view_or_live", mock.AsyncMock(return_value=tabulate.cache.EmailUidLookup({}))
    )
    monkeypatch.setattr(tabulate.util, "thread_messages", _thread_messages)
    committee = sql.Committee(key="example", committee_members=["alice"], committers=["alice", "rm"])
    interp = sql.VoteInterpretation(
        release_key="example-1.0.0",
        vote_seq=1,
        asf_eid="archive-alice",
        voter_asf_uid="alice",
        voter_email="alice@apache.org",
        voter_fullname="Alice",
        choice=sql.VoteChoice.YES,
        is_binding_at_confirm=True,
        email_timestamp=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        confirmed_by="rm",
    )

    start_unixtime, tabulated_votes = await tabulate.votes(
        committee,
        thread_id,
        interpretations={"archive-alice": interp},
    )

    assert start_unixtime == 100
    assert list(tabulated_votes) == ["alice"]
    assert tabulated_votes["alice"].vote == models_tabulate.Vote.YES
    assert tabulated_votes["alice"].confirmed is True
    assert tabulated_votes["alice"].confirmed_by == "rm"
    assert tabulated_votes["alice"].asf_uid == "alice"
    assert tabulated_votes["alice"].unixtime == 200


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
