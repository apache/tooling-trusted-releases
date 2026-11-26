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
import unittest.mock as mock
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pydantic
import pytest
import quart
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.vote as vote
import atr.htm as htm
import atr.models.results as results
import atr.models.sql as sql
import atr.sessions as sessions
import atr.shared as shared


@pytest.fixture
def render_app() -> quart.Quart:
    app = quart.Quart(__name__)
    app.secret_key = "test-secret"
    app.config["TESTING"] = True
    return app


@pytest.fixture
async def sqlite_sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)

    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    yield sessionmaker
    await engine.dispose()


@pytest.mark.asyncio
async def test_ballot_receipt_message_ids_returns_all_receipts_for_vote(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        receipt_one = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="receipt-one@apache.org",
        )
        receipt_two = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.NO,
            receipt_message_id="receipt-two@apache.org",
        )
        receipt_other_voter = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="someone",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="receipt-other-voter@apache.org",
        )
        empty_receipt = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="empty",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="",
        )
        other_seq = _ballot(
            release_key="project-1.0.0",
            vote_seq=2,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="receipt-other-seq@apache.org",
        )
        other_release = _ballot(
            release_key="other-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="receipt-other-release@apache.org",
        )
        data.add_all([receipt_one, receipt_two, receipt_other_voter, empty_receipt, other_seq, other_release])
        await data.commit()

        found = await interaction.ballot_receipt_message_ids("project-1.0.0", 1, data)

    assert found == {
        "receipt-one@apache.org",
        "receipt-two@apache.org",
        "receipt-other-voter@apache.org",
    }


@pytest.mark.asyncio
async def test_ballots_for_resolution_returns_latest_per_round_and_voter(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        older = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="older@apache.org",
        )
        newest = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.NO,
            receipt_message_id="newest@apache.org",
        )
        other_round = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="other-round@apache.org",
        )
        other_round.vote_round = 2
        other_voter = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="someone",
            choice=sql.VoteChoice.YES,
            receipt_message_id="other-voter@apache.org",
        )
        other_seq = _ballot(
            release_key="project-1.0.0",
            vote_seq=2,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="other-seq@apache.org",
        )
        data.add_all([older, newest, other_round, other_voter, other_seq])
        await data.commit()

        found = await interaction.ballots_for_resolution("project-1.0.0", 1, data)

    assert {ballot.receipt_message_id for ballot in found} == {
        "newest@apache.org",
        "other-round@apache.org",
        "other-voter@apache.org",
    }


@pytest.mark.asyncio
async def test_email_rendering_keeps_hidden_vote_defaults_and_no_trusted_state(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    latest_ballot_get = mock.AsyncMock(return_value=None)
    monkeypatch.setattr(vote.interaction, "latest_ballot_for_voter", latest_ballot_get)

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page,
            _release(sql.VoteMode.EMAIL),
            _session(),
            None,
            "dev@project.apache.org",
            None,
        )

    html = str(page.collect())
    assert "You already cast a vote" not in html
    assert "receipt will be queued" not in html
    assert "Your vote will be sent to" in html
    assert 'name="vote_seq" id="vote_seq" value="1"' in html
    assert 'name="vote_mode" id="vote_mode" value="email"' in html
    assert "<textarea" in html
    assert "checked" not in html
    latest_ballot_get.assert_awaited_once_with("project-1.0.0", 1, "voter", vote_mode=sql.VoteMode.EMAIL)


@pytest.mark.asyncio
async def test_email_rendering_restores_rejected_decision(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    form_data = {"csrf_token": "token", "decision": "-1", "comment": "", "vote_seq": "1", "vote_mode": "email"}
    with pytest.raises(pydantic.ValidationError) as excinfo:
        form.validate(shared.vote.CastVoteForm, form_data)
    flash_data = form.flash_error_data(shared.vote.CastVoteForm, excinfo.value.errors(), form_data)
    monkeypatch.setattr(sessions, "form_error_pop", mock.AsyncMock(return_value=flash_data))

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page,
            _release(sql.VoteMode.EMAIL),
            _session(),
            None,
            "dev@project.apache.org",
            None,
        )

    html = str(page.collect())
    assert 'id="decision_2" value="-1" autocomplete="off" checked' in html
    assert html.count(" checked") == 1
    assert "Please give a reason when voting 0 or -1." in html


@pytest.mark.asyncio
async def test_latest_ballot_for_voter_returns_newest_matching_ballot(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        older = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="older@apache.org",
        )
        newest = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.NO,
            receipt_message_id="newest@apache.org",
        )
        other_seq = _ballot(
            release_key="project-1.0.0",
            vote_seq=2,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="other-seq@apache.org",
        )
        other_voter = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="someone",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="other-voter@apache.org",
        )
        other_release = _ballot(
            release_key="other-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.ABSTAIN,
            receipt_message_id="other-release@apache.org",
        )
        data.add_all([older, newest, other_seq, other_voter, other_release])
        await data.commit()

        found = await interaction.latest_ballot_for_voter(
            "project-1.0.0", 1, "voter", data, vote_mode=sql.VoteMode.TRUSTED
        )

    assert found is not None
    assert found.receipt_message_id == "newest@apache.org"
    assert found.choice == sql.VoteChoice.NO


@pytest.mark.asyncio
async def test_latest_ballot_for_voter_uses_highest_id_not_timestamp(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        newest_timestamp = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.YES,
            receipt_message_id="newer-time@apache.org",
            created=datetime.datetime(2026, 1, 3, tzinfo=datetime.UTC),
        )
        highest_id = _ballot(
            release_key="project-1.0.0",
            vote_seq=1,
            voter_asf_uid="voter",
            choice=sql.VoteChoice.NO,
            receipt_message_id="highest-id@apache.org",
            created=datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
        )
        data.add_all([newest_timestamp, highest_id])
        await data.commit()

        found = await interaction.latest_ballot_for_voter(
            "project-1.0.0", 1, "voter", data, vote_mode=sql.VoteMode.TRUSTED
        )

    assert found is not None
    assert found.receipt_message_id == "highest-id@apache.org"


def test_message_id_source_archive_url_encodes_message_id_and_listid() -> None:
    url = shared.vote.message_id_source_archive_url(
        "CAA9ykM+bMPNk=BOF@apache.org",
        "user-tests@tooling.apache.org",
    )

    assert (
        url == "https://lists.apache.org/api/source.lua?"
        "id=%3CCAA9ykM%2BbMPNk%3DBOF@apache.org%3E&listid=%3Cuser-tests.tooling.apache.org%3E"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_ppmc_binding", "is_ipmc_binding", "expected", "unexpected"),
    [
        (
            True,
            True,
            [
                "3. Cast your PPMC vote",
                "4. Cast your IPMC vote",
                "<code>dev@project.apache.org</code>",
                "<code>general@incubator.apache.org</code>",
                'id="ppmc_vote_seq" value="1"',
                'id="ipmc_vote_seq" value="2"',
                'for="ppmc_comment"',
                'for="ipmc_comment"',
            ],
            ['id="vote_seq"', 'id="comment"', "IPMC-binding"],
        ),
        (
            True,
            False,
            [
                "3. Cast your PPMC vote",
                "4. Cast your IPMC vote",
                "You are not a Committee PMC member. Your vote will be recorded as <strong>non-binding</strong>",
                'id="ppmc_vote_seq" value="1"',
                'id="ipmc_vote_seq" value="2"',
            ],
            ['id="vote_seq"'],
        ),
        (
            False,
            True,
            ["3. Cast your IPMC vote", "<code>general@incubator.apache.org</code>", 'id="vote_seq" value="2"'],
            ["<code>dev@project.apache.org</code>", "4. Cast your IPMC vote"],
        ),
        (
            False,
            False,
            [
                "3. Cast your PPMC vote",
                "4. Cast your IPMC vote",
                "your vote is <strong>non-binding</strong> for podling approval",
                "Your vote will be recorded as <strong>non-binding</strong>",
                'id="ppmc_vote_seq" value="1"',
                'id="ipmc_vote_seq" value="2"',
            ],
            ['id="vote_seq"'],
        ),
    ],
)
async def test_podling_first_round_rendering_offers_the_votes_the_voter_is_binding_in(
    render_app: quart.Quart,
    monkeypatch: pytest.MonkeyPatch,
    is_ppmc_binding: bool,
    is_ipmc_binding: bool,
    expected: list[str],
    unexpected: list[str],
) -> None:
    await _patch_render_dependencies(monkeypatch)
    _patch_binding(monkeypatch, is_ppmc_binding, is_ipmc_binding)
    monkeypatch.setattr(vote, "_get_archive_url", mock.AsyncMock(return_value=None))
    early_task = _completed_vote_task(email_to="general@incubator.apache.org", vote_seq=2)
    release = _release(sql.VoteMode.EMAIL)
    release.committee.is_podling = True

    async with render_app.test_request_context("/vote/project/1.0.0"):
        overlap_rounds = await vote._overlap_vote_rounds(release, _session(), early_task)
        page = htm.Block()
        await vote._render_section_vote(
            page, release, _session(), vote.UserCategory.PMC_MEMBER, None, None, early_task, overlap_rounds
        )

    html = str(page.collect())
    for fragment in expected:
        assert fragment in html
    for fragment in unexpected:
        assert fragment not in html


@pytest.mark.asyncio
async def test_podling_first_round_rendering_restores_a_rejected_ipmc_ballot_only_in_the_ipmc_form(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    _patch_binding(monkeypatch, True, True)
    saved = {"!vote_seq": {"original": "2"}, "!decision": {"original": "-1"}}
    monkeypatch.setattr(sessions, "form_error_pop", mock.AsyncMock(return_value=saved))
    monkeypatch.setattr(vote, "_get_archive_url", mock.AsyncMock(return_value=None))
    early_task = _completed_vote_task(email_to="general@incubator.apache.org", vote_seq=2)
    release = _release(sql.VoteMode.EMAIL)
    release.committee.is_podling = True

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_section_vote(
            page, release, _session(), vote.UserCategory.PMC_MEMBER, None, None, early_task, [1, 2]
        )

    ppmc, ipmc = str(page.collect()).split("4. Cast your IPMC vote")
    assert 'autocomplete="off" checked' not in ppmc
    assert 'id="ipmc_decision_2" value="-1" autocomplete="off" checked' in ipmc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("send_status", "warnings", "expected"),
    [
        (
            sql.TaskStatus.COMPLETED,
            [],
            ["You already voted through ATR", "Sent: ", "View your reply in the list archive"],
        ),
        (
            sql.TaskStatus.COMPLETED,
            ["failed to send to private@project.apache.org: 550 rejected"],
            ["You already voted through ATR", "Sent: ", "View your reply in the list archive"],
        ),
        (
            sql.TaskStatus.COMPLETED,
            ["failed to send to dev@project.apache.org: 550 rejected"],
            ["Your last vote through ATR was not sent", "so this vote does not count"],
        ),
        (sql.TaskStatus.QUEUED, [], ["You already voted through ATR", "Being sent, since "]),
        (sql.TaskStatus.FAILED, [], ["Your last vote through ATR was not sent", "so this vote does not count"]),
    ],
)
async def test_email_rendering_shows_the_voters_email_ballot(
    render_app: quart.Quart,
    monkeypatch: pytest.MonkeyPatch,
    send_status: sql.TaskStatus,
    warnings: list[str],
    expected: list[str],
) -> None:
    await _patch_render_dependencies(monkeypatch)
    ballot = _ballot(
        release_key="project-1.0.0",
        vote_seq=1,
        voter_asf_uid="voter",
        choice=sql.VoteChoice.NO,
        receipt_message_id="reply@apache.org",
        vote_mode=sql.VoteMode.EMAIL,
    )
    latest_ballot_for_voter = mock.AsyncMock(return_value=ballot)
    monkeypatch.setattr(vote.interaction, "latest_ballot_for_voter", latest_ballot_for_voter)
    send_result = results.MessageSend(kind="message_send", mid="reply@apache.org", mail_send_warnings=warnings)
    send_task = SimpleNamespace(
        status=send_status,
        task_args={"email_to": "dev@project.apache.org"},
        result=send_result if (send_status == sql.TaskStatus.COMPLETED) else None,
    )
    message_send_task = mock.AsyncMock(return_value=send_task)
    monkeypatch.setattr(vote.interaction, "message_send_task", message_send_task)

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page, _release(sql.VoteMode.EMAIL), _session(), None, "dev@project.apache.org", None
        )

    html = str(page.collect())
    assert latest_ballot_for_voter.call_args.kwargs == {"vote_mode": sql.VoteMode.EMAIL}
    assert message_send_task.call_args.args == ("project", "1.0.0", "reply@apache.org")
    assert "Choice: <strong>-1</strong>" in html
    assert "Vote again" in html
    assert "You already cast a vote" not in html
    for fragment in expected:
        assert fragment in html


@pytest.mark.asyncio
@pytest.mark.parametrize(("current_vote_seq", "restored"), [(1, True), (2, False)])
async def test_cast_vote_form_restores_a_rejected_ballot_only_for_the_same_vote(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch, current_vote_seq: int, restored: bool
) -> None:
    await _patch_render_dependencies(monkeypatch)
    saved = {"!vote_seq": {"original": "1"}, "!decision": {"original": "-1"}}
    monkeypatch.setattr(sessions, "form_error_pop", mock.AsyncMock(return_value=saved))

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._append_cast_vote_form(page, _release(sql.VoteMode.EMAIL, current_vote_seq), "Binding")

    html = str(page.collect())
    assert ('value="-1" autocomplete="off" checked' in html) is restored
    assert f'id="vote_seq" value="{current_vote_seq}"' in html


@pytest.mark.asyncio
async def test_podling_first_round_signed_out_rendering_links_each_vote_thread(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    monkeypatch.setattr(vote, "_get_archive_url", mock.AsyncMock(return_value="https://lists.example.invalid/ipmc"))
    early_task = _completed_vote_task(email_to="general@incubator.apache.org", vote_seq=2)
    release = _release(sql.VoteMode.EMAIL)
    release.committee.is_podling = True

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_section_vote(
            page,
            release,
            None,
            vote.UserCategory.UNAUTHENTICATED,
            "https://lists.example.invalid/ppmc",
            None,
            early_task,
            [],
        )

    html = str(page.collect())
    assert 'href="https://lists.example.invalid/ppmc"' in html
    assert 'href="https://lists.example.invalid/ipmc"' in html


@pytest.mark.asyncio
async def test_trusted_rendering_shows_automatic_publish_location(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    task = _completed_vote_task()
    task.task_args = {**task.task_args, "automatic_publish_when_resolved": True, "download_path_suffix": "sub/1.0.0"}

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page,
            _release(sql.VoteMode.TRUSTED),
            _session(),
            "https://lists.apache.org/thread/abc",
            "dev@project.apache.org",
            task,
        )

    html = str(page.collect())
    assert "If this vote passes and every vote is +1, ATR will publish the release to" in html
    assert "/project/sub/1.0.0/</code>" in html


@pytest.mark.asyncio
async def test_trusted_rendering_with_current_vote_seq_none_does_not_query_ballots(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    latest_ballot_get = mock.AsyncMock()
    monkeypatch.setattr(vote.interaction, "latest_ballot_for_voter", latest_ballot_get)

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page,
            _release(sql.VoteMode.TRUSTED, current_vote_seq=None),
            _session(),
            None,
            "dev@project.apache.org",
            None,
        )

    html = str(page.collect())
    assert "ATR ballots are unavailable until the vote-start email has a message ID." in html
    assert 'name="vote_seq" id="vote_seq"' in html
    assert 'name="vote_seq" id="vote_seq" value=' not in html
    assert "<textarea" not in html
    latest_ballot_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_trusted_rendering_with_start_mid_and_existing_ballot_places_recast_note_before_delivery(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    latest_ballot = _ballot(
        release_key="project-1.0.0",
        vote_seq=1,
        voter_asf_uid="voter",
        choice=sql.VoteChoice.YES,
        receipt_message_id="receipt@apache.org",
        created=datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC),
    )
    latest_ballot.id = 1
    monkeypatch.setattr(vote.interaction, "latest_ballot_for_voter", mock.AsyncMock(return_value=latest_ballot))

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page,
            _release(sql.VoteMode.TRUSTED),
            _session(),
            None,
            "dev@project.apache.org",
            _completed_vote_task(),
        )

    html = str(page.collect())
    recast_note = (
        "Submitting again records a new ballot. New ballots during the voting period always replace old ballots."
    )
    assert "You already cast a vote" in html
    assert recast_note in html
    assert "A new submission will be recorded by ATR" in html
    assert "Resubmit vote" in html
    assert (
        "https://lists.apache.org/api/source.lua?id=%3Creceipt@apache.org%3E&amp;listid=%3Cdev.project.apache.org%3E"
    ) in html
    assert html.index("Receipt message ID") < html.index(recast_note)
    assert html.index(recast_note) < html.index("A new submission will be recorded by ATR")


@pytest.mark.asyncio
async def test_trusted_rendering_with_start_mid_enables_form(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    monkeypatch.setattr(vote.interaction, "latest_ballot_for_voter", mock.AsyncMock(return_value=None))

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page,
            _release(sql.VoteMode.TRUSTED),
            _session(),
            "https://lists.apache.org/thread/abc",
            "dev@project.apache.org",
            _completed_vote_task(),
        )

    html = str(page.collect())
    assert "receipt will be queued" in html
    assert "view thread" in html
    assert "You already cast a vote" not in html
    assert "Submit vote" in html
    assert "Resubmit vote" not in html
    assert "<textarea" in html
    assert 'name="vote_seq" id="vote_seq" value="1"' in html
    assert 'name="vote_mode" id="vote_mode" value="trusted"' in html
    assert 'type="submit" class="btn btn-primary" disabled' not in html
    assert 'name="decision" id="decision_0" value="+1" autocomplete="off" disabled' not in html


@pytest.mark.asyncio
async def test_trusted_rendering_without_start_mid_disables_form_but_shows_latest_ballot(
    render_app: quart.Quart, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_render_dependencies(monkeypatch)
    latest_ballot = _ballot(
        release_key="project-1.0.0",
        vote_seq=1,
        voter_asf_uid="voter",
        choice=sql.VoteChoice.YES,
        receipt_message_id="receipt@apache.org",
        created=datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC),
    )
    latest_ballot.id = 1
    latest_ballot_get = mock.AsyncMock(return_value=latest_ballot)
    monkeypatch.setattr(vote.interaction, "latest_ballot_for_voter", latest_ballot_get)

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(
            page,
            _release(sql.VoteMode.TRUSTED),
            _session(),
            None,
            "dev@project.apache.org",
            None,
        )

    html = str(page.collect())
    assert "You already cast a vote" in html
    assert "2026-01-02 03:04 UTC" in html
    assert (
        "Submitting again records a new ballot. New ballots during the voting period always replace old ballots."
        in html
    )
    assert "ATR ballots are unavailable until the vote-start email has a message ID." in html
    assert "Resubmit vote" in html
    assert 'name="decision"' in html
    assert "disabled" in html
    assert "<textarea" not in html
    assert 'name="vote_seq" id="vote_seq" value="1"' in html
    assert 'name="vote_mode" id="vote_mode" value="trusted"' in html
    latest_ballot_get.assert_awaited_once_with("project-1.0.0", 1, "voter", vote_mode=sql.VoteMode.TRUSTED)


@pytest.mark.asyncio
@pytest.mark.parametrize("vote_mode", [sql.VoteMode.EMAIL, sql.VoteMode.TRUSTED])
@pytest.mark.parametrize(
    ("is_podling", "podling_thread_id", "binding_by_round", "label"),
    [
        (True, None, {1: True, 2: True}, "PPMC-binding"),
        (True, None, {1: False, 2: True}, "Non-binding"),
        (True, "thread-abc", {2: True}, "Binding"),
        (False, None, {None: False}, "Non-binding"),
    ],
)
async def test_vote_decision_labels_reflect_membership(
    render_app: quart.Quart,
    monkeypatch: pytest.MonkeyPatch,
    vote_mode: sql.VoteMode,
    is_podling: bool,
    podling_thread_id: str | None,
    binding_by_round: dict[int | None, bool],
    label: str,
) -> None:
    await _patch_render_dependencies(monkeypatch)
    monkeypatch.setattr(
        vote.user,
        "is_binding_for_release",
        mock.AsyncMock(side_effect=lambda _committee, _uid, vote_round: (binding_by_round[vote_round], "Project")),
    )
    release = _release(vote_mode)
    release.committee.is_podling = is_podling
    release.podling_thread_id = podling_thread_id

    async with render_app.test_request_context("/vote/project/1.0.0"):
        page = htm.Block()
        await vote._render_vote_authenticated(page, release, _session(), None, "dev@project.apache.org", None)

    html = str(page.collect())
    assert f"+1 ({label})" in html
    assert f"-1 ({label})" in html
    assert "Incubator approval" not in html
    if is_podling and (podling_thread_id is None):
        ppmc_word = "PPMC-binding" if binding_by_round[1] else "non-binding"
        assert f"your vote is <strong>{ppmc_word}</strong> for podling approval." in html


def test_vote_task_warnings_renders_delivery_warnings() -> None:
    task = _completed_vote_task()
    task.result = task.result.model_copy(
        update={"mail_send_warnings": ["failed to send to dev@project.apache.org: connection refused"]}
    )

    page = htm.Block()
    vote._render_vote_task_warnings(page, task)

    html = str(page.collect())
    assert "The vote announcement email reported delivery problems:" in html
    assert "failed to send to dev@project.apache.org: connection refused" in html


def test_vote_task_warnings_renders_failed_task_error() -> None:
    task = sql.Task(
        status=sql.TaskStatus.FAILED,
        task_type=sql.TaskType.VOTE_INITIATE,
        task_args={},
        asf_uid="chair",
        project_key="project",
        version_key="1.0.0",
    )
    task.error = "Failed to send the vote email to any recipient: connection refused"

    page = htm.Block()
    vote._render_vote_task_warnings(page, task)

    html = str(page.collect())
    assert "Starting this vote failed: Failed to send the vote email to any recipient: connection refused" in html


def _ballot(
    *,
    release_key: str,
    vote_seq: int,
    voter_asf_uid: str,
    choice: sql.VoteChoice,
    receipt_message_id: str,
    created: datetime.datetime | None = None,
    vote_mode: sql.VoteMode = sql.VoteMode.TRUSTED,
) -> sql.BallotPaper:
    return sql.BallotPaper(
        release_key=release_key,
        vote_seq=vote_seq,
        vote_round=None,
        vote_mode=vote_mode,
        voter_asf_uid=voter_asf_uid,
        voter_fullname="Voter",
        choice=choice,
        comment="",
        is_binding_at_cast=True,
        revision_number_at_cast="00001",
        receipt_message_id=receipt_message_id,
        created=created or datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )


def _completed_vote_task(email_to: str = "dev@project.apache.org", vote_seq: int = 1) -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.COMPLETED,
        task_type=sql.TaskType.VOTE_INITIATE,
        task_args={
            "release_key": "project-1.0.0",
            "email_to": email_to,
            "vote_duration": 72,
            "initiator_id": "chair",
            "initiator_fullname": "Chair",
            "subject": "[VOTE] Release",
            "body": "Please vote",
            "vote_seq": vote_seq,
        },
        result=results.VoteInitiate(
            kind="vote_initiate",
            message="Vote announcement email sent successfully",
            email_to=email_to,
            vote_end="2026-01-04 00:00:00 UTC",
            subject="[VOTE] Release",
            mid="start-mid@apache.org",
            mail_send_warnings=[],
        ),
        asf_uid="chair",
        project_key="project",
        version_key="1.0.0",
    )


def _patch_binding(monkeypatch: pytest.MonkeyPatch, is_ppmc_binding: bool, is_ipmc_binding: bool) -> None:
    async def is_binding_for_release(_committee, _asf_uid, vote_round, **_kwargs):
        return (is_ppmc_binding if (vote_round == 1) else is_ipmc_binding), "Committee"

    monkeypatch.setattr(vote.user, "is_binding_for_release", is_binding_for_release)


async def _patch_render_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_form_errors(_path: str) -> dict[str, object]:
        return {}

    monkeypatch.setattr(sessions, "form_error_pop", no_form_errors)
    monkeypatch.setattr(vote.util, "as_url", lambda _endpoint, **_kwargs: "/vote/project/1.0.0")
    monkeypatch.setattr(vote.interaction, "latest_ballot_for_voter", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(vote.user, "is_binding_for_release", mock.AsyncMock(return_value=(True, "Project")))


def _release(vote_mode: sql.VoteMode, current_vote_seq: int | None = 1) -> SimpleNamespace:
    return SimpleNamespace(
        key="project-1.0.0",
        committee=SimpleNamespace(key="project", is_podling=False),
        current_vote_seq=current_vote_seq,
        effective_vote_mode=vote_mode,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        podling_thread_id=None,
        project=SimpleNamespace(
            key="project",
            is_archived=False,
            policy_vote_comment_template="Default comment",
            policy_vote_comment_template_url="",
            status=sql.ProjectStatus.ACTIVE,
        ),
        version="1.0.0",
    )


def _session() -> SimpleNamespace:
    return SimpleNamespace(uid="voter")
