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

import pytest
import quart
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
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
    latest_ballot_get = mock.AsyncMock()
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
    latest_ballot_get.assert_not_awaited()


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

        found = await interaction.latest_ballot_for_voter("project-1.0.0", 1, "voter", data)

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

        found = await interaction.latest_ballot_for_voter("project-1.0.0", 1, "voter", data)

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
    assert "Trusted ballots are unavailable until the vote-start email has a message ID." in html
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
    assert "Trusted ballots are unavailable until the vote-start email has a message ID." in html
    assert "Resubmit vote" in html
    assert 'name="decision"' in html
    assert "disabled" in html
    assert "<textarea" not in html
    assert 'name="vote_seq" id="vote_seq" value="1"' in html
    assert 'name="vote_mode" id="vote_mode" value="trusted"' in html
    latest_ballot_get.assert_awaited_once_with("project-1.0.0", 1, "voter")


def _ballot(
    *,
    release_key: str,
    vote_seq: int,
    voter_asf_uid: str,
    choice: sql.VoteChoice,
    receipt_message_id: str,
    created: datetime.datetime | None = None,
) -> sql.BallotPaper:
    return sql.BallotPaper(
        release_key=release_key,
        vote_seq=vote_seq,
        vote_round=None,
        voter_asf_uid=voter_asf_uid,
        voter_fullname="Voter",
        choice=choice,
        comment="",
        is_binding_at_cast=True,
        revision_number_at_cast="00001",
        receipt_message_id=receipt_message_id,
        created=created or datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )


def _completed_vote_task() -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.COMPLETED,
        task_type=sql.TaskType.VOTE_INITIATE,
        task_args={
            "release_key": "project-1.0.0",
            "email_to": "dev@project.apache.org",
            "vote_duration": 72,
            "initiator_id": "chair",
            "initiator_fullname": "Chair",
            "subject": "[VOTE] Release",
            "body": "Please vote",
            "vote_seq": 1,
        },
        result=results.VoteInitiate(
            kind="vote_initiate",
            message="Vote announcement email sent successfully",
            email_to="dev@project.apache.org",
            vote_end="2026-01-04 00:00:00 UTC",
            subject="[VOTE] Release",
            mid="start-mid@apache.org",
            mail_send_warnings=[],
        ),
        asf_uid="chair",
        project_key="project",
        version_key="1.0.0",
    )


async def _patch_render_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_form_errors(_path: str) -> dict[str, object]:
        return {}

    monkeypatch.setattr(sessions, "form_error_pop", no_form_errors)
    monkeypatch.setattr(vote.util, "as_url", lambda _endpoint, **_kwargs: "/vote/project/1.0.0")
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
            policy_vote_comment_template="Default comment",
        ),
        version="1.0.0",
    )


def _session() -> SimpleNamespace:
    return SimpleNamespace(uid="voter")
