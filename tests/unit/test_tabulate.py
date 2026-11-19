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
import time
import unittest.mock as mock
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

import atr.db.interaction as interaction
import atr.models.sql as sql
import atr.models.tabulate as models_tabulate
import atr.tabulate as tabulate
import atr.user as user


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


def test_trusted_tally_block_uses_ppmc_binding_label() -> None:
    summary = interaction.TrustedVoteSummary(binding_votes_yes=3, non_binding_votes_no=1)

    tally = tabulate.trusted_tally_block(summary, *user.binding_terminology(1))

    assert tally.splitlines() == [
        "There were 3 PPMC-binding votes.",
        "PPMC-binding votes: +1: 3, 0: 0, -1: 0.",
        "Non-binding votes: +1: 0, 0: 0, -1: 1.",
    ]


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


def test_vote_outcome_applies_the_default_minimum_to_a_saved_policy_without_one() -> None:
    release = SimpleNamespace(project=sql.Project(key="x", release_policy=sql.ReleasePolicy()))

    _passed, outcome = tabulate.vote_outcome(release, int(time.time()), {})

    assert "still open" in outcome


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

    body_lines = list(tabulate._vote_resolution_body_votes({}, summary, binding_member_label="PMC member"))

    assert body_lines[2] == "Of these binding votes, 8 were +1, 0 were -1, and 1 was 0."


def test_vote_resolution_body_votes_formats_singular_binding_summary() -> None:
    summary = {
        "binding_votes": 9,
        "binding_votes_yes": 8,
        "binding_votes_no": 1,
        "binding_votes_abstain": 0,
    }

    body_lines = list(tabulate._vote_resolution_body_votes({}, summary, binding_member_label="PMC member"))

    assert body_lines[2] == "Of these binding votes, 8 were +1, 1 was -1, and 0 were 0."


def test_vote_resolution_body_votes_uses_ppmc_binding_label() -> None:
    summary = {
        "binding_votes": 1,
        "binding_votes_yes": 1,
        "binding_votes_no": 0,
        "binding_votes_abstain": 0,
    }
    vote_email = models_tabulate.VoteEmail(
        name="PPMC Member",
        asf_uid_or_email="ppmc",
        from_email="ppmc@apache.org",
        status=models_tabulate.VoteStatus.BINDING,
        asf_eid="ppmc-eid",
        iso_datetime="2026-01-01T00:00:00Z",
        vote=models_tabulate.Vote.YES,
        quotation="+1",
        updated=False,
    )

    body_lines = list(
        tabulate._vote_resolution_body_votes(
            {"ppmc": vote_email},
            summary,
            *user.binding_terminology(1),
            binding_member_label=user.binding_member_label(1),
        )
    )

    assert body_lines == [
        "The PPMC-binding votes were cast as follows:",
        "",
        "+1 ppmc (PPMC-binding)",
        "",
        "There was 1 PPMC-binding vote.",
        "",
        "Of these PPMC-binding votes, 1 was +1, 0 were -1, and 0 were 0.",
        "",
    ]


def test_vote_result_subject_formats_result_before_vote() -> None:
    release = SimpleNamespace(project=SimpleNamespace(display_name="Project"), version="1.0.0")

    assert tabulate.vote_result_subject(release, "passed") == "[RESULT] [VOTE] Release Project 1.0.0 PASSED"


@pytest.mark.asyncio
async def test_votes_applies_interpretation_to_its_email_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
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
    assert tabulated_votes["two"].vote == models_tabulate.Vote.UNKNOWN


@pytest.mark.asyncio
async def test_votes_does_not_treat_a_fractional_pmc_vote_as_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _thread_messages(
        _thread_id: str, *, strict: bool = False, source: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
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

    _start_unixtime, tabulated_votes = await tabulate.votes(
        committee,
        "0123456789abcdef0123456789abcdef",
        interpretations={"archive-two": sql.VoteChoice.YES},
    )

    assert tabulated_votes["one"].vote == models_tabulate.Vote.ABSTAIN
    assert tabulated_votes["one"].fraction == "+0.5"
    assert tabulated_votes["two"].fraction is None
    assert tabulated_votes["three"].fraction == "+0.5"
    assert tabulated_votes["one"].status == models_tabulate.VoteStatus.FRACTIONAL
    # The release manager's reading of an email stands as a binding vote
    assert tabulated_votes["two"].vote == models_tabulate.Vote.YES
    assert tabulated_votes["two"].status == models_tabulate.VoteStatus.BINDING
    assert tabulated_votes["three"].status == models_tabulate.VoteStatus.COMMITTER

    summary = tabulate.vote_summary(tabulated_votes)
    assert summary["binding_votes"] == 1
    assert summary["non_binding_votes_abstain"] == 2
    body = list(tabulate._vote_resolution_body_votes(tabulated_votes, summary, binding_member_label="PMC member"))
    assert body[body.index("The contributor and non-binding votes were cast as follows:") + 2 :] == [
        "+0.5 one (PMC member, non-binding)",
        "",
    ]
    podling_tally = tabulate.email_tally_block(
        tabulated_votes,
        summary,
        "0123456789abcdef0123456789abcdef",
        *user.binding_terminology(1),
        binding_member_label=user.binding_member_label(1),
    )
    assert "+0.5 one (PPMC member, non-binding)" in podling_tally.splitlines()


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
