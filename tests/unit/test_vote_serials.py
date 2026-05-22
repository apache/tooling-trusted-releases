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
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post.vote as post_vote
import atr.storage.writers.release as release
import atr.storage.writers.vote as vote
import atr.util as util


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
async def test_cast_trusted_recomputes_podling_bindingness(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: "receipt-1@apache.org")

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
            vote_mode=sql.VoteMode.TRUSTED,
        )
        data.add(sql.Committee(key="incubator", name="Incubator", committee_members=["chair"], committers=[]))
        data.add(_completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)))
        await data.commit()

        writer, _write_as = _foundation_writer_with_data(data)
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )

        assert email_to == ["dev@project.apache.org"]
        assert error == ""
        ballot = (await data.execute(sqlmodel.select(sql.BallotPaper))).scalar_one()
        assert ballot.vote_round == 1
        assert ballot.is_binding_at_cast is True


@pytest.mark.asyncio
async def test_cast_trusted_records_additive_ballots_and_receipt_tasks(sqlite_sessionmaker, monkeypatch) -> None:
    message_ids = iter(["receipt-1@apache.org", "receipt-2@apache.org"])
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: next(message_ids))

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_mode=sql.VoteMode.TRUSTED,
        )
        start_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add(start_task)
        await data.commit()

        writer, write_as = _foundation_writer_with_data(data)
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "Looks good",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )

        assert email_to == ["dev@project.apache.org"]
        assert error == ""
        receipt_tasks = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalars()
        ballots = (await data.execute(sqlmodel.select(sql.BallotPaper))).scalars()
        receipt_task = receipt_tasks.one()
        ballot = ballots.one()
        assert receipt_task.task_args["message_id"] == "receipt-1@apache.org"
        assert receipt_task.task_args["in_reply_to"] == "start-mid@apache.org"
        assert ballot.receipt_message_id == "receipt-1@apache.org"
        assert ballot.choice == sql.VoteChoice.YES
        assert ballot.comment == "Looks good"
        write_as.append_to_audit_log.assert_called_once()
        assert write_as.append_to_audit_log.call_args.kwargs["ballot_id"] == ballot.id
        assert write_as.append_to_audit_log.call_args.kwargs["replaced_ballot_id"] is None

        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.NO,
            "Changed my mind",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )

        assert email_to == ["dev@project.apache.org"]
        assert error == ""
        receipt_tasks = (
            await data.execute(
                sqlmodel.select(sql.Task)
                .where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND)
                .order_by(sql.validate_instrumented_attribute(sql.Task.id))
            )
        ).scalars()
        ballots = (
            await data.execute(
                sqlmodel.select(sql.BallotPaper).order_by(sql.validate_instrumented_attribute(sql.BallotPaper.id))
            )
        ).scalars()
        receipt_task_rows = receipt_tasks.all()
        ballot_rows = ballots.all()
        assert len(receipt_task_rows) == 2
        assert len(ballot_rows) == 2
        assert receipt_task_rows[1].task_args["message_id"] == "receipt-2@apache.org"
        assert ballot_rows[1].receipt_message_id == "receipt-2@apache.org"
        assert ballot_rows[1].choice == sql.VoteChoice.NO
        assert write_as.append_to_audit_log.call_count == 2
        assert write_as.append_to_audit_log.call_args.kwargs["replaced_ballot_id"] == ballot_rows[0].id


@pytest.mark.asyncio
async def test_cast_trusted_rejects_stale_or_non_trusted_release_state(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_mode=sql.VoteMode.EMAIL,
        )
        writer, _write_as = _foundation_writer_with_data(data)
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.EMAIL,
        )
        assert email_to == []
        assert error == "The vote form is stale, please refresh and try again."

        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )
        assert email_to == []
        assert error == "This release is not accepting trusted votes."

        release_model.vote_mode = sql.VoteMode.TRUSTED
        release_model.current_vote_seq = None
        await data.commit()
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )
        assert email_to == []
        assert error == "Vote serial is missing, please refresh and try again."

        release_model.current_vote_seq = 1
        await data.commit()
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=2,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )
        assert email_to == []
        assert error == "The vote form is stale, please refresh and try again."


@pytest.mark.asyncio
async def test_cast_trusted_rejects_unready_start_tasks(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_mode=sql.VoteMode.TRUSTED,
        )
        writer, write_as = _foundation_writer_with_data(data)
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )
        assert email_to == []
        assert error == "No vote task found."

        start_task = _vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add(start_task)
        await data.commit()
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )
        assert email_to == []
        assert error == "Vote task has not completed yet."

        start_task.status = sql.TaskStatus.COMPLETED
        start_task.completed = datetime.datetime(2026, 1, 1, 1, tzinfo=datetime.UTC)
        start_task.result = results.VoteInitiate(
            kind="vote_initiate",
            message="Vote announcement email sent successfully",
            email_to="dev@project.apache.org",
            vote_end="2026-01-04 00:00:00 UTC",
            subject="[VOTE] Release",
            mid=None,
            mail_send_warnings=[],
        )
        await data.commit()
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )
        assert email_to == []
        assert error == "No vote thread found."

        assert write_as.append_to_audit_log.call_count == 0
        assert (await data.execute(sqlmodel.select(sql.BallotPaper))).scalars().all() == []


@pytest.mark.asyncio
async def test_cast_trusted_rolls_back_duplicate_receipt_id(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: "receipt-1@apache.org")

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_mode=sql.VoteMode.TRUSTED,
        )
        data.add(_completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)))
        await data.commit()

        writer, write_as = _foundation_writer_with_data(data)
        email_to, error = await writer.cast_trusted(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.TRUSTED,
        )
        assert email_to == ["dev@project.apache.org"]
        assert error == ""
        await data.rollback()

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await writer.cast_trusted(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.NO,
                "",
                "Voter",
                expected_vote_seq=1,
                expected_vote_mode=sql.VoteMode.TRUSTED,
            )

        receipt_tasks = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalars()
        ballots = (await data.execute(sqlmodel.select(sql.BallotPaper))).scalars()
        assert len(receipt_tasks.all()) == 1
        assert len(ballots.all()) == 1
        assert write_as.append_to_audit_log.call_count == 1


@pytest.mark.asyncio
async def test_podling_second_round_rolls_back_with_task_creation(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        seeded_release = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
            vote_mode=sql.VoteMode.EMAIL,
        )
        data.add(sql.VoteCounter(release_key=seeded_release.key, last_allocated_number=1))
        await data.commit()

        release_model = await data.release(
            key="project-1.0.0",
            _project=True,
            _committee=True,
            _project_release_policy=True,
        ).demand(RuntimeError("release missing"))
        release_participant = _release_writer_with_data(data)
        write_as = SimpleNamespace(release=release_participant, append_to_audit_log=mock.MagicMock())
        writer = _member_writer_with_data(data, write_as)

        with pytest.raises(RuntimeError, match="task creation failed"):
            try:
                await data.begin_immediate()
                await writer._resolve_transition(
                    release_model,
                    expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    expected_podling_thread_id=None,
                    new_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    new_vote_mode=sql.VoteMode.EMAIL,
                    new_vote_resolved=None,
                    new_podling_thread_id="thread",
                )
                await data.refresh(release_model)
                task = await writer.start(
                    email_to=util.INCUBATOR_GENERAL_ADDRESS,
                    permitted_recipients=[util.INCUBATOR_GENERAL_ADDRESS],
                    project_key=release_model.safe_project_key,
                    version_key=release_model.safe_version_key,
                    asf_fullname="Chair",
                    vote_duration_choice=72,
                    subject="[VOTE] Release",
                    body_data="Please vote",
                    release=release_model,
                    promote=False,
                )
                assert task.task_args["vote_seq"] == 2
                raise RuntimeError("task creation failed")
            except Exception:
                await data.rollback()
                raise

    async with sqlite_sessionmaker() as data:
        release_model = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        counter = await data.get(sql.VoteCounter, "project-1.0.0")
        assert counter is not None
        assert release_model.podling_thread_id is None
        assert release_model.current_vote_seq == 1
        assert counter.last_allocated_number == 1


@pytest.mark.asyncio
async def test_post_vote_dispatches_trusted_email_manual_and_stale(monkeypatch) -> None:
    trusted_cast = mock.AsyncMock(return_value=(["dev@project.apache.org"], ""))
    email_send = mock.AsyncMock(return_value=(["dev@project.apache.org"], ""))
    flash = mock.AsyncMock()

    @contextlib.asynccontextmanager
    async def _participant_context(_committee_key):
        yield SimpleNamespace(vote=SimpleNamespace(send_user_vote=email_send))

    @contextlib.asynccontextmanager
    async def _write_context(_session):
        yield SimpleNamespace(
            as_foundation_committer=lambda: SimpleNamespace(vote=SimpleNamespace(cast_trusted=trusted_cast))
        )

    monkeypatch.setattr(post_vote.quart, "flash", flash)
    monkeypatch.setattr(post_vote.storage, "write", _write_context)
    monkeypatch.setattr(post_vote.storage, "write_as_committee_participant", _participant_context)
    monkeypatch.setattr(post_vote.user, "is_binding_for_release", mock.AsyncMock(return_value=(True, "Project")))

    handler = _post_handler()
    form = SimpleNamespace(decision="+1", comment="", vote_seq=1, vote_mode=sql.VoteMode.TRUSTED)
    result = await handler(
        _route_session(_route_release(sql.VoteMode.TRUSTED)),
        "vote",
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        form,
    )
    assert result == "redirect"
    trusted_cast.assert_awaited_once()
    email_send.assert_not_awaited()

    trusted_cast.reset_mock()
    email_send.reset_mock()
    form.vote_mode = sql.VoteMode.EMAIL
    result = await handler(
        _route_session(_route_release(sql.VoteMode.EMAIL)),
        "vote",
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        form,
    )
    assert result == "redirect"
    trusted_cast.assert_not_awaited()
    email_send.assert_awaited_once()

    trusted_cast.reset_mock()
    email_send.reset_mock()
    form.vote_mode = sql.VoteMode.MANUAL
    result = await handler(
        _route_session(_route_release(sql.VoteMode.MANUAL)),
        "vote",
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        form,
    )
    assert result == "redirect"
    trusted_cast.assert_not_awaited()
    email_send.assert_not_awaited()
    assert flash.call_args.args == ("Voting through this form is not available for manual votes.", "error")

    flash.reset_mock()
    form.vote_mode = sql.VoteMode.EMAIL
    result = await handler(
        _route_session(_route_release(sql.VoteMode.TRUSTED)),
        "vote",
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        form,
    )
    assert result == "redirect"
    trusted_cast.assert_not_awaited()
    email_send.assert_not_awaited()
    assert flash.call_args.args == ("The vote form is stale, please refresh and try again.", "error")


@pytest.mark.asyncio
async def test_release_current_vote_task_matches_serial_and_legacy_fallback(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=2)
        older = _vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        current = _vote_task(2, datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC))
        data.add_all([older, current])
        await data.commit()

        matched = await interaction.release_current_vote_task(release_model, data)
        assert matched is not None
        assert matched.task_args["vote_seq"] == 2

        release_model.current_vote_seq = 3
        await data.commit()
        assert await interaction.release_current_vote_task(release_model, data) is None

        release_model.current_vote_seq = None
        await data.commit()
        legacy = await interaction.release_current_vote_task(release_model, data)
        assert legacy is not None
        assert legacy.task_args["vote_seq"] == 2


@pytest.mark.asyncio
async def test_vote_start_allocation_rolls_back_with_task_creation(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(release.util, "number_of_release_files", mock.AsyncMock(return_value=1))
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
        writer = _release_writer_with_data(data)

        with pytest.raises(RuntimeError, match="task creation failed"):
            try:
                await data.begin_immediate()
                _release, vote_seq, _vote_mode, _revision_number = await writer._start_vote_no_commit(
                    safe.ReleaseKey("project-1.0.0"),
                    safe.RevisionNumber("00001"),
                    allowed_vote_modes=frozenset({sql.VoteMode.EMAIL}),
                    promote=True,
                )
                data.add(_vote_task(vote_seq, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)))
                raise RuntimeError("task creation failed")
            except Exception:
                await data.rollback()
                raise

    async with sqlite_sessionmaker() as data:
        release_model = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        counter = await data.get(sql.VoteCounter, "project-1.0.0")
        latest_task = await interaction.release_latest_vote_task(release_model, data)
        assert release_model.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
        assert release_model.current_vote_seq is None
        assert counter is None
        assert latest_task is None


def _completed_vote_task(vote_seq: int, added: datetime.datetime) -> sql.Task:
    task = _vote_task(vote_seq, added)
    task.status = sql.TaskStatus.COMPLETED
    task.completed = datetime.datetime(2026, 1, 1, 1, tzinfo=datetime.UTC)
    task.result = results.VoteInitiate(
        kind="vote_initiate",
        message="Vote announcement email sent successfully",
        email_to="dev@project.apache.org",
        vote_end="2026-01-04 00:00:00 UTC",
        subject="[VOTE] Release",
        mid="start-mid@apache.org",
        mail_send_warnings=[],
    )
    return task


def _foundation_writer_with_data(data: db.Session) -> tuple[vote.FoundationCommitter, SimpleNamespace]:
    write_as = SimpleNamespace(append_to_audit_log=mock.MagicMock())
    writer = object.__new__(vote.FoundationCommitter)
    writer._FoundationCommitter__data = data
    writer._FoundationCommitter__write_as = write_as
    writer._FoundationCommitter__asf_uid = "chair"
    return writer, write_as


def _member_writer_with_data(data: db.Session, write_as: SimpleNamespace) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__write_as = write_as
    writer._CommitteeParticipant__asf_uid = "chair"
    writer._CommitteeParticipant__committee_key = "project"
    writer._ReleaseManager__data = data
    writer._ReleaseManager__write_as = write_as
    writer._ReleaseManager__asf_uid = "chair"
    writer._ReleaseManager__committee_key = "project"
    return writer


def _post_handler():
    wrapper = post_vote.selected_post.__wrapped__
    if wrapper.__closure__ is None:
        raise AssertionError("Expected post_vote.selected_post wrapper closure")
    for name, cell in zip(wrapper.__code__.co_freevars, wrapper.__closure__):
        if name == "func":
            return cell.cell_contents
    raise AssertionError("Could not find wrapped vote post handler")


def _release_writer_with_data(data: db.Session) -> release.CommitteeParticipant:
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "chair"
    return writer


def _route_release(vote_mode: sql.VoteMode) -> SimpleNamespace:
    return SimpleNamespace(
        committee=SimpleNamespace(key="project", is_podling=False),
        current_vote_seq=1,
        effective_vote_mode=vote_mode,
    )


def _route_session(release_model: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        fullname="Voter",
        redirect=mock.AsyncMock(return_value="redirect"),
        release=mock.AsyncMock(return_value=release_model),
        uid="chair",
    )


async def _seed_release(
    data: db.Session,
    *,
    phase: sql.ReleasePhase,
    current_vote_seq: int | None = None,
    is_podling: bool = False,
    vote_mode: sql.VoteMode | None = None,
) -> sql.Release:
    committee = sql.Committee(
        key="project",
        name="Project",
        is_podling=is_podling,
        committee_members=["chair"],
        committers=["chair"],
    )
    project = sql.Project(key="project", name="Project", committee=committee)
    release = sql.Release(
        key="project-1.0.0",
        phase=phase,
        project=project,
        project_key=project.key,
        version="1.0.0",
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        current_vote_seq=current_vote_seq,
        vote_mode=vote_mode,
    )
    revision = sql.Revision(
        key="project-1.0.0 00001",
        release=release,
        release_key=release.key,
        seq=1,
        number="00001",
        asfuid="chair",
        phase=phase,
    )
    data.add_all([committee, project, release, revision])
    await data.commit()
    return release


def _vote_task(vote_seq: int, added: datetime.datetime) -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.QUEUED,
        task_type=sql.TaskType.VOTE_INITIATE,
        task_args={
            "release_key": "project-1.0.0",
            "email_to": "dev@project.apache.org",
            "vote_duration": 72,
            "initiator_id": "chair",
            "initiator_fullname": "Chair",
            "subject": "[VOTE] Release",
            "body": "Please vote",
            "vote_seq": vote_seq,
        },
        asf_uid="chair",
        project_key="project",
        version_key="1.0.0",
        added=added,
    )
