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
import atr.storage as storage
import atr.storage.writers.announce as announce
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
        assert error == "This release is not accepting votes on ATR."

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
@pytest.mark.parametrize("update", [False, True])
@pytest.mark.parametrize(
    ("expedited", "vote_mode", "valid"),
    [
        (False, None, True),
        (False, sql.VoteMode.EMAIL, True),
        (False, sql.VoteMode.TRUSTED, True),
        (True, None, True),
        (True, sql.VoteMode.EMAIL, False),
        (True, sql.VoteMode.TRUSTED, True),
    ],
)
async def test_expedited_vote_mode_constraint(sqlite_sessionmaker, update, expedited, vote_mode, valid) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = sql.Release(
            project_key="project",
            version="1.0.0",
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        )
        data.add(release_model)
        if update:
            await data.commit()
        release_model.expedited = expedited
        release_model.vote_mode = vote_mode
        if valid:
            await data.commit()
            return
        with pytest.raises(sqlalchemy.exc.IntegrityError, match="ck_release_expedited_vote_mode_trusted"):
            await data.commit()


@pytest.mark.asyncio
async def test_interpret_records_replaces_and_resets(sqlite_sessionmaker) -> None:
    project_key = safe.ProjectKey("project")
    version_key = safe.VersionKey("1.0.0")

    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, vote_mode=sql.VoteMode.EMAIL)
        writer = _member_writer_with_data(data, SimpleNamespace(append_to_audit_log=mock.MagicMock()))

        await writer.interpret(project_key, version_key, "eid", sql.VoteChoice.YES)
        await writer.interpret(project_key, version_key, "eid", sql.VoteChoice.NO)
        interpretation = (await data.execute(sqlmodel.select(sql.VoteInterpretation))).scalar_one()
        assert interpretation.choice == sql.VoteChoice.NO
        assert interpretation.asf_uid == "chair"

        await writer.interpret(project_key, version_key, "eid", None)
        assert (await data.execute(sqlmodel.select(sql.VoteInterpretation))).all() == []


@pytest.mark.asyncio
async def test_interpret_rejects_trusted_votes(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, vote_mode=sql.VoteMode.TRUSTED)
        writer = _member_writer_with_data(data, SimpleNamespace(append_to_audit_log=mock.MagicMock()))

        with pytest.raises(storage.AccessError):
            await writer.interpret(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "eid", sql.VoteChoice.YES)


@pytest.mark.asyncio
async def test_post_vote_passes_an_early_second_round_serial_to_the_writer(monkeypatch) -> None:
    email_send = mock.AsyncMock(return_value=(["general@incubator.apache.org"], ""))

    @contextlib.asynccontextmanager
    async def _write_context(_session):
        yield SimpleNamespace(
            as_foundation_committer=lambda: SimpleNamespace(vote=SimpleNamespace(send_user_vote=email_send))
        )

    monkeypatch.setattr(post_vote.quart, "flash", mock.AsyncMock())
    monkeypatch.setattr(post_vote.storage, "write", _write_context)
    release_model = _route_release(sql.VoteMode.EMAIL)
    release_model.committee.is_podling = True
    release_model.podling_thread_id = None
    session = _route_session(release_model)
    form = SimpleNamespace(decision="+1", comment="", vote_seq=2, vote_mode=sql.VoteMode.EMAIL)

    await _post_handler()(session, "vote", safe.ProjectKey("project"), safe.VersionKey("1.0.0"), form)

    assert email_send.call_args.kwargs["expected_vote_seq"] == 2


@pytest.mark.asyncio
async def test_podling_early_second_round_becomes_current_when_first_round_passes(
    sqlite_sessionmaker, monkeypatch
) -> None:
    monkeypatch.setattr(
        vote.construct, "resolve_template", mock.AsyncMock(return_value=vote.construct.ResolvedTemplate("body"))
    )
    monkeypatch.setattr(vote.construct, "start_vote_subject_default", mock.AsyncMock(return_value="subject"))
    monkeypatch.setattr(vote.construct, "start_vote_subject_and_body", mock.AsyncMock(return_value=("subject", "body")))
    async with sqlite_sessionmaker() as data:
        seeded_release = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
            vote_mode=sql.VoteMode.EMAIL,
        )
        first_round_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add_all([sql.VoteCounter(release_key=seeded_release.key, last_allocated_number=1), first_round_task])
        await data.commit()
        cache = SimpleNamespace(
            get_message_archive_url=mock.AsyncMock(return_value="https://lists.apache.org/thread/t")
        )
        write_as = SimpleNamespace(
            release=_release_writer_with_data(data), cache=cache, append_to_audit_log=mock.MagicMock()
        )
        writer = _member_writer_with_data(data, write_as)

        early_task = await writer.start_second_round(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "Chair", 1)
        release_model = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        assert release_model.current_vote_seq == 1

        with pytest.raises(storage.AccessError, match="still being sent"):
            await writer.resolve(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "passed", "Chair", "Passed")
        await data.refresh(release_model)
        await data.refresh(early_task)
        await data.refresh(first_round_task)
        assert release_model.current_vote_seq == 1

        early_task.status = sql.TaskStatus.COMPLETED
        early_task.completed = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)
        early_task.result = first_round_task.result
        await data.commit()
        _release, _round, message, _error = await writer.resolve(
            safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "passed", "Chair", "Passed"
        )
        assert release_model.current_vote_seq == early_task.task_args["vote_seq"]
        assert release_model.podling_thread_id == "t"
        assert "already running" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("early_email_sent", [True, False])
async def test_podling_first_round_cancellation_cancels_the_early_second_round(
    sqlite_sessionmaker, early_email_sent: bool
) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
            vote_mode=sql.VoteMode.EMAIL,
        )
        first_round_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        early_task = _vote_task(2, datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC))
        early_task.task_args = {**early_task.task_args, "email_to": "general@incubator.apache.org"}
        if early_email_sent:
            early_task.status = sql.TaskStatus.COMPLETED
            early_task.completed = datetime.datetime(2026, 1, 2, 1, tzinfo=datetime.UTC)
            early_task.result = results.VoteInitiate(
                kind="vote_initiate",
                message="Vote announcement email sent successfully",
                email_to="general@incubator.apache.org",
                vote_end="2026-01-05 00:00:00 UTC",
                subject="[VOTE] Release",
                mid="early-mid@apache.org",
                mail_send_warnings=[],
            )
        data.add_all([first_round_task, early_task])
        await data.commit()
        cache = SimpleNamespace(
            get_message_archive_url=mock.AsyncMock(return_value="https://lists.apache.org/thread/t")
        )
        writer = _member_writer_with_data(data, SimpleNamespace(cache=cache, append_to_audit_log=mock.MagicMock()))

        message = None
        if early_email_sent:
            _release, _round, message, _error = await writer.resolve(
                safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "cancelled", "Chair", "Cancelled"
            )
        else:
            with pytest.raises(storage.AccessError, match="still being sent"):
                await writer.resolve(
                    safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "cancelled", "Chair", "Cancelled"
                )
        release_model = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        await data.refresh(release_model)
        sent = (
            (await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND)))
            .scalars()
            .all()
        )

    if not early_email_sent:
        assert release_model.phase == sql.ReleasePhase.RELEASE_CANDIDATE
        assert sent == []
        return
    assert release_model.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    assert message == (
        "First round vote was cancelled. The second round vote already running on general@incubator.apache.org"
        " was cancelled too"
    )
    replies = {task.task_args["in_reply_to"]: task.task_args for task in sent}
    assert set(replies) == {"start-mid@apache.org", "early-mid@apache.org"}
    assert replies["early-mid@apache.org"]["email_to"] == "general@incubator.apache.org"
    assert replies["early-mid@apache.org"]["subject"].endswith(" CANCELLED")
    assert "so this Incubator PMC vote is cancelled" in replies["early-mid@apache.org"]["body"]
    assert "https://lists.apache.org/thread/t" in replies["early-mid@apache.org"]["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("writer_cls", "publish"), [(vote.CommitteeMember, True), (vote.SecurityTeam, False)])
async def test_podling_early_second_round_keeps_publish_unless_security_override(
    sqlite_sessionmaker, writer_cls: type[vote.ReleaseManager], publish: bool
) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=1, is_podling=True
        )
        first_round_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        early_task = _completed_vote_task(2, datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC))
        early_task.task_args = {**early_task.task_args, "automatic_publish_when_resolved": True}
        data.add_all([first_round_task, early_task])
        await data.commit()
        writer = _member_writer_with_data(data, SimpleNamespace(append_to_audit_log=mock.MagicMock()), writer_cls)

        task, adopted = await writer._ReleaseManager__pass_first_round(
            release_model,
            first_round_task,
            "Chair",
            "t",
            None,
            automatic_resolve_when_finished=False,
            notify_when_finished=False,
        )
        await data.commit()

    assert adopted
    assert task.id == early_task.id
    assert task.task_args["automatic_publish_when_resolved"] is publish


@pytest.mark.asyncio
async def test_podling_early_second_round_refused_for_trusted_votes(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
            vote_mode=sql.VoteMode.TRUSTED,
        )
        writer = _member_writer_with_data(data, SimpleNamespace(append_to_audit_log=mock.MagicMock()))

        with pytest.raises(storage.AccessError, match="only be started early for an email vote"):
            await writer.start_second_round(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "Chair", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "found"),
    [(sql.TaskStatus.QUEUED, True), (sql.TaskStatus.FAILED, False), (sql.TaskStatus.BROKEN, False)],
)
async def test_podling_early_second_round_task_ignores_failed_and_broken(
    sqlite_sessionmaker, status: sql.TaskStatus, found: bool
) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=1, is_podling=True
        )
        early_task = _vote_task(2, datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC))
        early_task.status = status
        if status != sql.TaskStatus.QUEUED:
            early_task.completed = datetime.datetime(2026, 1, 2, 1, tzinfo=datetime.UTC)
            early_task.error = "Vote email could not be sent"
        data.add(early_task)
        await data.commit()

        task = await interaction.release_early_second_round_task(release_model, data)

    assert (task is not None) is found


@pytest.mark.asyncio
async def test_podling_early_second_round_task_requires_the_observed_current_vote(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=2, is_podling=True)
        data.add(_completed_vote_task(2, datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)))
        await data.commit()
        observed = SimpleNamespace(key="project-1.0.0", project_key="project", version="1.0.0", current_vote_seq=1)

        task = await interaction.release_early_second_round_task(observed, data)

    assert task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("vote_mode", [sql.VoteMode.EMAIL, sql.VoteMode.TRUSTED])
@pytest.mark.parametrize("voted_revision_number", [None, "00001", "00002"])
async def test_podling_second_round_preserves_voted_revision(
    sqlite_sessionmaker, vote_mode, voted_revision_number
) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            is_podling=True,
            vote_mode=vote_mode,
            voted_revision_number=voted_revision_number,
        )
        writer = _release_writer_with_data(data)
        await data.begin_immediate()
        if voted_revision_number == "00002":
            with pytest.raises(storage.AccessError) as error:
                await writer.start_vote_no_commit(
                    safe.ReleaseKey("project-1.0.0"),
                    safe.RevisionNumber("00001"),
                    expected_vote_mode=vote_mode,
                    promote=False,
                )
            assert error.value.status == 409
            await data.rollback()
            assert await data.get(sql.VoteCounter, "project-1.0.0") is None
        else:
            release_model, _seq, _mode, _revision = await writer.start_vote_no_commit(
                safe.ReleaseKey("project-1.0.0"),
                safe.RevisionNumber("00001"),
                expected_vote_mode=vote_mode,
                promote=False,
            )
            assert release_model.voted_revision_number == voted_revision_number
            await data.commit()

    async with sqlite_sessionmaker() as data:
        release_model = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        assert release_model.voted_revision_number == voted_revision_number


@pytest.mark.asyncio
@pytest.mark.parametrize("voted_revision_number", [None, "00001"])
async def test_podling_second_round_rolls_back_with_task_creation(sqlite_sessionmaker, voted_revision_number) -> None:
    async with sqlite_sessionmaker() as data:
        seeded_release = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
            vote_mode=sql.VoteMode.EMAIL,
            voted_revision_number=voted_revision_number,
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
                release_model = await data.release(
                    key="project-1.0.0",
                    _project=True,
                    _committee=True,
                    _project_release_policy=True,
                ).demand(RuntimeError("release missing"))
                task = await writer.start(
                    email_to=util.INCUBATOR_GENERAL_ADDRESS,
                    permitted_recipients=[util.INCUBATOR_GENERAL_ADDRESS],
                    project_key=release_model.safe_project_key,
                    version_key=release_model.safe_version_key,
                    asf_fullname="Chair",
                    vote_duration_choice=72,
                    subject="[VOTE] Release",
                    body_data="Please vote",
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
        assert release_model.voted_revision_number == voted_revision_number
        assert counter.last_allocated_number == 1


@pytest.mark.asyncio
async def test_post_vote_dispatches_trusted_email_and_stale(monkeypatch) -> None:
    trusted_cast = mock.AsyncMock(return_value=(["dev@project.apache.org"], ""))
    email_send = mock.AsyncMock(return_value=(["dev@project.apache.org", "private@project.apache.org"], ""))
    flash = mock.AsyncMock()

    @contextlib.asynccontextmanager
    async def _write_context(_session):
        yield SimpleNamespace(
            as_foundation_committer=lambda: SimpleNamespace(
                vote=SimpleNamespace(cast_trusted=trusted_cast, send_user_vote=email_send)
            )
        )

    monkeypatch.setattr(post_vote.quart, "flash", flash)
    monkeypatch.setattr(post_vote.storage, "write", _write_context)

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
    assert flash.call_args.args == (
        "Sending your vote to dev@project.apache.org, private@project.apache.org.",
        "success",
    )

    trusted_cast.reset_mock()
    email_send.reset_mock()
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
async def test_resolve_transition_rejects_restarted_vote(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=2)
        writer = _member_writer_with_data(data, SimpleNamespace())

        with pytest.raises(storage.AccessError, match="release state has changed"):
            await writer._resolve_transition(
                SimpleNamespace(key="project-1.0.0", current_vote_seq=1),
                expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                expected_podling_thread_id=None,
                new_phase=sql.ReleasePhase.RELEASE_PREVIEW,
                new_vote_mode=sql.VoteMode.EMAIL,
                new_vote_resolved=datetime.datetime.now(datetime.UTC),
                new_podling_thread_id=None,
            )


@pytest.mark.asyncio
async def test_send_user_vote_records_an_email_ballot_apart_from_streamlined_ballots(
    sqlite_sessionmaker, monkeypatch
) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=1)
        start_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add(start_task)
        await data.commit()
        monkeypatch.setattr(vote.interaction, "release_current_vote_task", mock.AsyncMock(return_value=start_task))
        writer, _write_as = _foundation_writer_with_data(data)

        await writer.send_user_vote(
            safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "-1", "Needs a fix", "Voter", expected_vote_seq=1
        )
        message_task = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalar_one()
        email_ballot = await interaction.latest_ballot_for_voter(
            "project-1.0.0", 1, "chair", data, vote_mode=sql.VoteMode.EMAIL
        )
        streamlined_ballot = await interaction.latest_ballot_for_voter(
            "project-1.0.0", 1, "chair", data, vote_mode=sql.VoteMode.TRUSTED
        )
        resolution_ballots = await interaction.ballots_for_resolution("project-1.0.0", 1, data)
        receipt_message_ids = await interaction.ballot_receipt_message_ids("project-1.0.0", 1, data)
        reply_task = await interaction.message_send_task("project", "1.0.0", message_task.task_args["message_id"], data)

    assert email_ballot is not None
    assert reply_task is not None
    assert reply_task.id == message_task.id
    assert email_ballot.choice == sql.VoteChoice.NO
    assert email_ballot.comment == "Needs a fix"
    assert email_ballot.receipt_message_id == message_task.task_args["message_id"]
    assert streamlined_ballot is None
    assert resolution_ballots == []
    assert receipt_message_ids == set()


@pytest.mark.asyncio
async def test_send_user_vote_without_a_vote_serial_sends_without_a_ballot(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE)
        start_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        start_task.task_args = {key: value for key, value in start_task.task_args.items() if (key != "vote_seq")}
        data.add(start_task)
        await data.commit()
        monkeypatch.setattr(vote.interaction, "release_current_vote_task", mock.AsyncMock(return_value=start_task))
        writer, _write_as = _foundation_writer_with_data(data)

        result = await writer.send_user_vote(
            safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "+1", "", "Voter", expected_vote_seq=None
        )
        sent = (
            (await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND)))
            .scalars()
            .all()
        )
        ballots = (await data.execute(sqlmodel.select(sql.BallotPaper))).scalars().all()

    assert result == (["dev@project.apache.org"], "")
    assert len(sent) == 1
    assert ballots == []


@pytest.mark.asyncio
async def test_send_user_vote_refuses_a_vote_that_changed(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=2, is_podling=True)
        second_round_task = _completed_vote_task(2, datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC))
        data.add(second_round_task)
        await data.commit()
        monkeypatch.setattr(
            vote.interaction, "release_current_vote_task", mock.AsyncMock(return_value=second_round_task)
        )
        writer, _write_as = _foundation_writer_with_data(data)

        result = await writer.send_user_vote(
            safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "+1", "", "Voter", expected_vote_seq=1
        )
        sent = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).all()

    assert result == ([], "The vote form is stale, please refresh and try again.")
    assert sent == []


@pytest.mark.asyncio
async def test_send_user_vote_replies_to_an_early_second_round(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE, current_vote_seq=1, is_podling=True)
        first_round_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        early_task = _completed_vote_task(2, datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC))
        early_task.task_args = {**early_task.task_args, "email_to": "general@incubator.apache.org"}
        early_task.result = results.VoteInitiate(
            kind="vote_initiate",
            message="Vote announcement email sent successfully",
            email_to="general@incubator.apache.org",
            vote_end="2026-01-05 00:00:00 UTC",
            subject="[VOTE] Release",
            mid="early-mid@apache.org",
            mail_send_warnings=[],
        )
        incubator = sql.Committee(key="incubator", name="Incubator", committee_members=["chair"], committers=[])
        data.add_all([first_round_task, early_task, incubator])
        await data.commit()
        monkeypatch.setattr(
            vote.interaction, "release_current_vote_task", mock.AsyncMock(return_value=first_round_task)
        )
        writer, _write_as = _foundation_writer_with_data(data)

        result = await writer.send_user_vote(
            safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "+1", "", "Voter", expected_vote_seq=2
        )
        sent = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalar_one()

    assert result == (["general@incubator.apache.org"], "")
    assert sent.task_args["in_reply_to"] == "early-mid@apache.org"
    assert "+1 (binding) (chair) Voter" in sent.task_args["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("vote_mode", [sql.VoteMode.EMAIL, sql.VoteMode.TRUSTED])
@pytest.mark.parametrize(
    ("vote_round", "is_member", "is_ipmc_member", "body"),
    [
        (1, True, True, "+1 (PPMC-binding) (chair) Voter"),
        (1, False, True, "+1 (non-binding) (chair) Voter"),
        (1, True, False, "+1 (PPMC-binding) (chair) Voter"),
        (1, False, False, "+1 (non-binding) (chair) Voter"),
        (2, False, True, "+1 (binding) (chair) Voter"),
        (2, True, False, "+1 (chair) Voter"),
        (None, True, True, "+1 (binding) (chair) Voter"),
        (None, False, True, "+1 (chair) Voter"),
    ],
)
async def test_vote_email_labels_reflect_membership(
    sqlite_sessionmaker, monkeypatch, vote_mode, vote_round, is_member, is_ipmc_member, body
) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=vote_round is not None,
            vote_mode=vote_mode,
        )
        release_model.podling_thread_id = "thread-abc" if (vote_round == 2) else None
        release_model.project.committee.committee_members = ["chair"] if is_member else []
        ipmc_members = ["chair"] if is_ipmc_member else []
        data.add(sql.Committee(key="incubator", name="Incubator", committee_members=ipmc_members, committers=[]))
        start_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add(start_task)
        await data.commit()
        monkeypatch.setattr(vote.interaction, "release_current_vote_task", mock.AsyncMock(return_value=start_task))

        writer, _write_as = _foundation_writer_with_data(data)
        if vote_mode == sql.VoteMode.TRUSTED:
            result = await writer.cast_trusted(
                safe.ProjectKey("project"), safe.VersionKey("1.0.0"), sql.VoteChoice.YES, "", "Voter"
            )
        else:
            result = await writer.send_user_vote(
                safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "+1", "", "Voter", expected_vote_seq=1
            )

        assert result == (["dev@project.apache.org"], "")
        message_task = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalar_one()
        assert message_task.task_args["body"] == body


@pytest.mark.asyncio
async def test_vote_restart_records_new_revision(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(release.util, "number_of_release_files", mock.AsyncMock(return_value=1))
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            vote_mode=sql.VoteMode.EMAIL,
            voted_revision_number="00001",
        )
        release_model.vote_started = datetime.datetime.now(datetime.UTC)
        await data.commit()
        writer = _member_writer_with_data(data, SimpleNamespace(append_to_audit_log=mock.MagicMock()))
        await writer._resolve_transition(
            release_model,
            expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            expected_podling_thread_id=None,
            new_phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            new_vote_mode=None,
            new_vote_resolved=None,
            new_podling_thread_id=None,
        )
        await data.commit()
        await data.refresh(release_model)
        assert release_model.voted_revision_number is None
        assert await data.release(key=release_model.key, voted_revision_number=None).get() is not None
        data.add(
            sql.Revision(
                key="project-1.0.0 00002",
                release_key=release_model.key,
                seq=2,
                number="00002",
                asfuid="chair",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            )
        )
        await data.commit()
        await data.refresh(release_model)
        await data.begin_immediate()
        release_model, _seq, _mode, _revision = await _release_writer_with_data(data).start_vote_no_commit(
            safe.ReleaseKey("project-1.0.0"),
            safe.RevisionNumber("00002"),
            expected_vote_mode=sql.VoteMode.EMAIL,
            promote=True,
        )
        assert release_model.voted_revision_number == "00002"
        await data.commit()


@pytest.mark.asyncio
async def test_vote_start_allocation_rolls_back_with_task_creation(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(release.util, "number_of_release_files", mock.AsyncMock(return_value=1))
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
        writer = _release_writer_with_data(data)

        with pytest.raises(RuntimeError, match="task creation failed"):
            try:
                await data.begin_immediate()
                _release, vote_seq, _vote_mode, _revision_number = await writer.start_vote_no_commit(
                    safe.ReleaseKey("project-1.0.0"),
                    safe.RevisionNumber("00001"),
                    expected_vote_mode=sql.VoteMode.EMAIL,
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
        assert release_model.voted_revision_number is None
        assert counter is None
        assert latest_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vote_mode", "expedited"),
    [
        (sql.VoteMode.EMAIL, False),
        (sql.VoteMode.TRUSTED, False),
        (sql.VoteMode.TRUSTED, True),
    ],
)
async def test_vote_start_records_revision(sqlite_sessionmaker, monkeypatch, vote_mode, expedited) -> None:
    monkeypatch.setattr(release.util, "number_of_release_files", mock.AsyncMock(return_value=1))
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
        release_model.project.committee.vote_mode = vote_mode
        release_model.expedited = expedited
        await data.commit()
        await data.begin_immediate()
        release_model, _seq, actual_mode, revision = await _release_writer_with_data(data).start_vote_no_commit(
            safe.ReleaseKey("project-1.0.0"),
            None,
            expected_vote_mode=vote_mode,
            promote=True,
        )
        assert actual_mode == vote_mode
        assert release_model.voted_revision_number == str(revision) == "00001"
        await data.commit()

    async with sqlite_sessionmaker() as data:
        assert await data.release(key="project-1.0.0", voted_revision_number="00001").get() is not None
        assert await data.release(key="project-1.0.0", voted_revision_number=None).get() is None


@pytest.mark.asyncio
async def test_promote_refuses_outstanding_distribution(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(data, phase=sql.ReleasePhase.RELEASE_PREVIEW)
        data.add(
            sql.Distribution(
                release_key=release_model.key,
                platform=sql.DistributionPlatform.PYPI,
                package="project",
                version="1.0.0",
                status=sql.DistributionStatus.IGNORED,
            )
        )
        await data.commit()
        # The session holds the row as ignored, then it changes underneath
        await data.distribution(release_key=release_model.key).all()
        await data.execute(sqlmodel.update(sql.Distribution).values(status=sql.DistributionStatus.CHECKING))
        await data.commit()
        announce_writer = object.__new__(announce.ReleaseManager)
        announce_writer._ReleaseManager__data = data

        with pytest.raises(storage.AccessError, match="1 distribution is found or ignored"):
            await announce_writer._ReleaseManager__promote_in_database(
                release_model,
                safe.RevisionNumber("00001"),
                datetime.datetime.now(datetime.UTC),
            )
        await data.rollback()

    async with sqlite_sessionmaker() as data:
        unchanged = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        assert unchanged.phase == sql.ReleasePhase.RELEASE_PREVIEW


@pytest.mark.asyncio
@pytest.mark.parametrize("voted_revision_number", [None, "00001"])
async def test_voted_revision_survives_pass_announcement_and_archive(
    sqlite_sessionmaker, voted_revision_number
) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            vote_mode=sql.VoteMode.EMAIL,
            voted_revision_number=voted_revision_number,
        )
        release_model.vote_started = datetime.datetime.now(datetime.UTC)
        await data.commit()
        writer = _member_writer_with_data(
            data,
            SimpleNamespace(
                append_to_audit_log=mock.MagicMock(),
                distributions=SimpleNamespace(expect_for_release_no_commit=mock.AsyncMock()),
            ),
        )
        await writer._resolve_transition(
            release_model,
            expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            expected_podling_thread_id=None,
            new_phase=sql.ReleasePhase.RELEASE_PREVIEW,
            new_vote_mode=sql.VoteMode.EMAIL,
            new_vote_resolved=datetime.datetime.now(datetime.UTC),
            new_podling_thread_id=None,
            new_vote_thread_url="https://lists.apache.org/thread/vote",
        )
        await data.commit()
        await data.refresh(release_model)
        assert release_model.voted_revision_number == voted_revision_number
        assert release_model.latest_revision_number == "00001"
        announce_writer = object.__new__(announce.ReleaseManager)
        announce_writer._ReleaseManager__data = data
        await announce_writer._ReleaseManager__promote_in_database(
            release_model, safe.RevisionNumber("00001"), datetime.datetime.now(datetime.UTC)
        )
        await data.commit()

    async with sqlite_sessionmaker() as data:
        release_model = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        assert release_model.phase == sql.ReleasePhase.RELEASE
        assert release_model.vote_thread_url == "https://lists.apache.org/thread/vote"
        assert release_model.latest_revision_number is None
        assert release_model.voted_revision_number == voted_revision_number
        error = await release.archive_release_core(
            data,
            SimpleNamespace(append_to_audit_log=mock.MagicMock()),
            "chair",
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            release_model,
            sql.ArchiveSource.DIST_WATCHER,
        )
        assert error is None

    async with sqlite_sessionmaker() as data:
        release_model = await data.release(key="project-1.0.0").demand(RuntimeError("release missing"))
        assert release_model.is_archived
        assert release_model.voted_revision_number == voted_revision_number


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
    writer._FoundationCommitter__write = mock.MagicMock()
    return writer, write_as


def _member_writer_with_data(
    data: db.Session, write_as: SimpleNamespace, writer_cls: type[vote.ReleaseManager] = vote.CommitteeMember
) -> vote.ReleaseManager:
    writer = object.__new__(writer_cls)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__write_as = write_as
    writer._CommitteeParticipant__asf_uid = "chair"
    writer._CommitteeParticipant__committee_key = "project"
    writer._ReleaseManager__data = data
    writer._ReleaseManager__write_as = write_as
    writer._ReleaseManager__asf_uid = "chair"
    writer._ReleaseManager__committee_key = "project"
    writer._CommitteeParticipant__write = mock.MagicMock()
    writer._ReleaseManager__write = mock.MagicMock()
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
    writer._CommitteeParticipant__write = mock.MagicMock()
    return writer


def _route_release(vote_mode: sql.VoteMode) -> SimpleNamespace:
    return SimpleNamespace(
        committee=SimpleNamespace(key="project", is_podling=False),
        current_vote_seq=1,
        effective_vote_mode=vote_mode,
        expedited=False,
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
    voted_revision_number: str | None = None,
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
        voted_revision_number=voted_revision_number,
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
