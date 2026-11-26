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
import atr.tabulate as tabulate
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
async def test_cast_preserves_private_recipient_and_requires_comment_for_no(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: "receipt-priv@apache.org")

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            expedited=True,
        )
        data.add(
            _completed_vote_task(
                1,
                datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                email_to="private@project.apache.org",
            )
        )
        await data.commit()

        writer, _write_as = _foundation_writer_with_data(data)
        with pytest.raises(storage.AccessError, match="comment is required") as exc_info:
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.NO,
                "   ",
                "Voter",
                expected_vote_seq=1,
            )
        assert exc_info.value.status == 400

        email_to, receipt_mid = await writer.cast(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
        )
        assert email_to == ["private@project.apache.org"]
        assert receipt_mid == "receipt-priv@apache.org"
        receipt_task = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalar_one()
        assert receipt_task.task_args["email_to"] == "private@project.apache.org"


@pytest.mark.asyncio
async def test_cast_recomputes_podling_bindingness(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: "receipt-1@apache.org")

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
        )
        data.add(sql.Committee(key="incubator", name="Incubator", committee_members=["chair"], committers=[]))
        data.add(_completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)))
        await data.commit()

        writer, _write_as = _foundation_writer_with_data(data)
        email_to, receipt_mid = await writer.cast(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
        )

        assert email_to == ["dev@project.apache.org"]
        assert receipt_mid == "receipt-1@apache.org"
        ballot = (await data.execute(sqlmodel.select(sql.BallotPaper))).scalar_one()
        assert ballot.vote_round == 1
        assert ballot.is_binding_at_cast is True


@pytest.mark.asyncio
async def test_cast_records_additive_ballots_and_receipt_tasks(sqlite_sessionmaker, monkeypatch) -> None:
    message_ids = iter(["receipt-1@apache.org", "receipt-2@apache.org"])
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: next(message_ids))

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
        )
        start_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add(start_task)
        await data.commit()

        writer, write_as = _foundation_writer_with_data(data)
        email_to, receipt_mid = await writer.cast(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "Looks good",
            "Voter",
            expected_vote_seq=1,
        )

        assert email_to == ["dev@project.apache.org"]
        assert receipt_mid == "receipt-1@apache.org"
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

        email_to, receipt_mid = await writer.cast(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.NO,
            "Changed my mind",
            "Voter",
            expected_vote_seq=1,
        )

        assert email_to == ["dev@project.apache.org"]
        assert receipt_mid == "receipt-2@apache.org"
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
async def test_cast_rejects_stale_closed_or_missing_seq_release_state(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
        )
        writer, _write_as = _foundation_writer_with_data(data)

        release_model.vote_started = None
        await data.commit()
        with pytest.raises(storage.AccessError, match="not currently open") as exc_info:
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.YES,
                "",
                "Voter",
                expected_vote_seq=1,
            )
        assert exc_info.value.status == 409

        release_model.vote_started = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        release_model.current_vote_seq = None
        await data.commit()
        with pytest.raises(storage.AccessError, match="Vote serial is missing") as exc_info:
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.YES,
                "",
                "Voter",
                expected_vote_seq=1,
            )
        assert exc_info.value.status == 409

        release_model.current_vote_seq = 1
        await data.commit()
        with pytest.raises(storage.AccessError, match="vote form is stale") as exc_info:
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.YES,
                "",
                "Voter",
                expected_vote_seq=2,
            )
        assert exc_info.value.status == 409


@pytest.mark.asyncio
async def test_cast_rejects_unready_start_tasks(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
        )
        writer, write_as = _foundation_writer_with_data(data)
        with pytest.raises(storage.AccessError, match="not been sent yet") as exc_info:
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.YES,
                "",
                "Voter",
                expected_vote_seq=1,
            )
        assert exc_info.value.status == 409

        start_task = _vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add(start_task)
        await data.commit()
        with pytest.raises(storage.AccessError, match="not been sent yet") as exc_info:
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.YES,
                "",
                "Voter",
                expected_vote_seq=1,
            )
        assert exc_info.value.status == 409

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
        with pytest.raises(storage.AccessError, match="not been sent yet") as exc_info:
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.YES,
                "",
                "Voter",
                expected_vote_seq=1,
            )
        assert exc_info.value.status == 409

        assert write_as.append_to_audit_log.call_count == 0
        assert (await data.execute(sqlmodel.select(sql.BallotPaper))).scalars().all() == []


@pytest.mark.asyncio
async def test_cast_rolls_back_duplicate_receipt_id(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: "receipt-1@apache.org")

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
        )
        data.add(_completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)))
        await data.commit()

        writer, write_as = _foundation_writer_with_data(data)
        email_to, receipt_mid = await writer.cast(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            sql.VoteChoice.YES,
            "",
            "Voter",
            expected_vote_seq=1,
        )
        assert email_to == ["dev@project.apache.org"]
        assert receipt_mid == "receipt-1@apache.org"
        await data.rollback()

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await writer.cast(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                sql.VoteChoice.NO,
                "Objection",
                "Voter",
                expected_vote_seq=1,
            )

        receipt_tasks = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalars()
        ballots = (await data.execute(sqlmodel.select(sql.BallotPaper))).scalars()
        assert len(receipt_tasks.all()) == 1
        assert len(ballots.all()) == 1
        assert write_as.append_to_audit_log.call_count == 1


@pytest.mark.asyncio
async def test_confirm_email_vote_records_updates_unconfirms_and_guards(sqlite_sessionmaker, monkeypatch) -> None:
    project_key = safe.ProjectKey("project")
    version_key = safe.VersionKey("1.0.0")
    email_ts = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)

    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
        )
        vote_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        receipt_ballot = sql.BallotPaper(
            release_key=release_model.key,
            vote_seq=1,
            vote_round=None,
            voter_asf_uid="alice",
            voter_fullname="Alice",
            choice=sql.VoteChoice.YES,
            comment="",
            is_binding_at_cast=False,
            revision_number_at_cast="00001",
            receipt_message_id="receipt-1@apache.org",
        )
        data.add_all([vote_task, receipt_ballot])
        await data.commit()

        cache = SimpleNamespace(
            get_message_archive_url=mock.AsyncMock(return_value="https://lists.apache.org/thread/threadabc")
        )
        write_as = SimpleNamespace(cache=cache, append_to_audit_log=mock.MagicMock())
        writer = _member_writer_with_data(data, write_as)

        async def _confirmable(_thread_id: str, asf_eid: str, *, excluded_message_ids: set[str]):
            assert not data.in_transaction()
            assert excluded_message_ids == {"receipt-1@apache.org"}
            return tabulate.ConfirmableEmail(
                asf_eid=asf_eid,
                email_timestamp=email_ts,
                voter_asf_uid="chair",
                voter_email="chair@apache.org",
                voter_fullname="Chair",
            )

        monkeypatch.setattr(tabulate, "email_for_confirmation", _confirmable)

        with pytest.raises(storage.AccessError, match="receipt") as exc_info:
            await writer.confirm_email_vote(project_key, version_key, " <receipt-1@apache.org> ", sql.VoteChoice.YES)
        assert exc_info.value.status == 409

        await writer.confirm_email_vote(project_key, version_key, "eid-1", sql.VoteChoice.YES, expected_vote_seq=1)
        await writer.confirm_email_vote(project_key, version_key, "eid-1", sql.VoteChoice.NO, expected_vote_seq=1)
        interpretation = (await data.execute(sqlmodel.select(sql.VoteInterpretation))).scalar_one()
        assert interpretation.choice == sql.VoteChoice.NO
        assert interpretation.confirmed_by == "chair"
        assert interpretation.is_binding_at_confirm is True
        assert interpretation.vote_seq == 1

        await writer.confirm_email_vote(project_key, version_key, "eid-1", None, expected_vote_seq=1)
        assert (await data.execute(sqlmodel.select(sql.VoteInterpretation))).all() == []

        async def _transition_during_fetch(_thread_id: str, asf_eid: str, *, excluded_message_ids: set[str]):
            res = await _confirmable(_thread_id, asf_eid, excluded_message_ids=excluded_message_ids)
            await data.execute(sqlmodel.update(sql.Release).values(current_vote_seq=2))
            await data.commit()
            return res

        monkeypatch.setattr(tabulate, "email_for_confirmation", _transition_during_fetch)
        with pytest.raises(storage.AccessError, match=r"vote form is stale|vote has changed") as exc_info:
            await writer.confirm_email_vote(project_key, version_key, "eid-late", sql.VoteChoice.YES)
        assert exc_info.value.status == 409

        release_model.expedited = True
        await data.commit()
        with pytest.raises(storage.AccessError, match="Expedited releases") as exc_info:
            await writer.confirm_email_vote(project_key, version_key, "eid-1", sql.VoteChoice.YES)
        assert exc_info.value.status == 409


@pytest.mark.asyncio
async def test_podling_early_second_round_becomes_current_when_first_round_passes(
    sqlite_sessionmaker, monkeypatch
) -> None:
    monkeypatch.setattr(
        vote.construct, "resolve_template", mock.AsyncMock(return_value=vote.construct.ResolvedTemplate("body"))
    )
    monkeypatch.setattr(vote.construct, "start_vote_subject_default", mock.AsyncMock(return_value="subject"))
    monkeypatch.setattr(vote.construct, "start_vote_subject_and_body", mock.AsyncMock(return_value=("subject", "body")))
    monkeypatch.setattr(
        vote.interaction,
        "counted_vote_details_and_summary",
        mock.AsyncMock(return_value=([], interaction.CountedVoteSummary(binding_votes_yes=3))),
    )
    async with sqlite_sessionmaker() as data:
        seeded_release = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=True,
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

        await writer.resolve(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), "passed", "Chair", "Passed")
        assert release_model.current_vote_seq == early_task.task_args["vote_seq"]
        assert release_model.podling_thread_id == "t"


@pytest.mark.asyncio
@pytest.mark.parametrize("voted_revision_number", [None, "00001", "00002"])
async def test_podling_second_round_preserves_voted_revision(sqlite_sessionmaker, voted_revision_number) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            is_podling=True,
            voted_revision_number=voted_revision_number,
        )
        writer = _release_writer_with_data(data)
        await data.begin_immediate()
        if voted_revision_number == "00002":
            with pytest.raises(storage.AccessError) as error:
                await writer.start_vote_no_commit(
                    safe.ReleaseKey("project-1.0.0"),
                    safe.RevisionNumber("00001"),
                    promote=False,
                )
            assert error.value.status == 409
            await data.rollback()
            assert await data.get(sql.VoteCounter, "project-1.0.0") is None
        else:
            release_model, _seq, _revision = await writer.start_vote_no_commit(
                safe.ReleaseKey("project-1.0.0"),
                safe.RevisionNumber("00001"),
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
async def test_post_vote_dispatches_unified_cast_and_missing_seq(monkeypatch) -> None:
    cast_mock = mock.AsyncMock(return_value=(["dev@project.apache.org"], "receipt-1@apache.org"))
    flash = mock.AsyncMock()

    @contextlib.asynccontextmanager
    async def _write_context(_session):
        yield SimpleNamespace(as_foundation_committer=lambda: SimpleNamespace(vote=SimpleNamespace(cast=cast_mock)))

    monkeypatch.setattr(post_vote.quart, "flash", flash)
    monkeypatch.setattr(post_vote.storage, "write", _write_context)

    handler = _post_handler()
    form = SimpleNamespace(decision="+1", comment="", vote_seq=1)
    result = await handler(
        _route_session(_route_release()),
        "vote",
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        form,
    )
    assert result == "redirect"
    cast_mock.assert_awaited_once()
    assert flash.call_args.args == (
        "Your vote has been recorded and a receipt is being sent to dev@project.apache.org.",
        "success",
    )

    cast_mock.reset_mock()
    flash.reset_mock()
    form.vote_seq = None
    result = await handler(
        _route_session(_route_release()),
        "vote",
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        form,
    )
    assert result == "redirect"
    cast_mock.assert_not_awaited()
    assert flash.call_args.args == ("Vote serial is missing, please refresh and try again.", "error")


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
async def test_resolve_unattended_guard_atomic_tally_and_auto_publish(sqlite_sessionmaker, monkeypatch) -> None:
    async def _template(_project, kind: str):
        assert kind == "finish_vote"
        return vote.construct.ResolvedTemplate("{{PROJECT_NAME}} {{OUTCOME}}\n{{ATR_TALLY}}")

    monkeypatch.setattr(vote.construct, "resolve_template", _template)
    monkeypatch.setattr(vote.interaction, "vote_resolution_bypass", lambda _r, _u: False)

    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
        )
        vote_task = _completed_vote_task(
            1,
            datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            automatic_publish_when_resolved=True,
        )
        data.add(vote_task)
        await data.commit()

        publish_mock = mock.AsyncMock()
        release_writer = SimpleNamespace(publish_to_svn=publish_mock)
        cache = SimpleNamespace(
            get_message_archive_url=mock.AsyncMock(return_value="https://lists.apache.org/thread/thread123")
        )
        write_as = SimpleNamespace(
            release=release_writer,
            cache=cache,
            distributions=SimpleNamespace(expect_for_release_no_commit=mock.AsyncMock()),
            append_to_audit_log=mock.MagicMock(),
        )
        writer = _member_writer_with_data(data, write_as)

        monkeypatch.setattr(
            vote.interaction,
            "counted_vote_details_and_summary",
            mock.AsyncMock(
                return_value=(
                    [],
                    interaction.CountedVoteSummary(binding_votes_yes=3, non_binding_votes_abstain=1),
                )
            ),
        )
        with pytest.raises(storage.AccessError, match="Somebody voted 0 or -1") as exc_info:
            await writer.resolve(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                "passed",
                "Chair",
                unattended=True,
            )
        assert exc_info.value.status == 409

        result = await writer.resolve(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            "passed",
            "Chair",
            resolution_body=None,
            unattended=False,
        )
        assert result[0].phase == sql.ReleasePhase.RELEASE_PREVIEW
        assert result[1:] == (None, "Vote marked as passed", None)
        publish_mock.assert_awaited_once()
        resolution_task = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.MESSAGE_SEND))
        ).scalar_one()
        assert "Project passed" in resolution_task.task_args["body"]
        assert "https://lists.apache.org/thread/thread123" in resolution_task.task_args["body"]


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
                new_vote_resolved=datetime.datetime.now(datetime.UTC),
                new_podling_thread_id=None,
            )


@pytest.mark.asyncio
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
    sqlite_sessionmaker, monkeypatch, vote_round, is_member, is_ipmc_member, body
) -> None:
    monkeypatch.setattr(vote.mail, "message_id_create", lambda: "receipt-label@apache.org")
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            is_podling=vote_round is not None,
        )
        release_model.podling_thread_id = "thread-abc" if (vote_round == 2) else None
        release_model.project.committee.committee_members = ["chair"] if is_member else []
        ipmc_members = ["chair"] if is_ipmc_member else []
        data.add(sql.Committee(key="incubator", name="Incubator", committee_members=ipmc_members, committers=[]))
        start_task = _completed_vote_task(1, datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
        data.add(start_task)
        await data.commit()

        writer, _write_as = _foundation_writer_with_data(data)
        result = await writer.cast(
            safe.ProjectKey("project"), safe.VersionKey("1.0.0"), sql.VoteChoice.YES, "", "Voter"
        )

        assert result == (["dev@project.apache.org"], "receipt-label@apache.org")
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
        release_model, _seq, _revision = await _release_writer_with_data(data).start_vote_no_commit(
            safe.ReleaseKey("project-1.0.0"),
            safe.RevisionNumber("00002"),
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
                _release, vote_seq, _revision_number = await writer.start_vote_no_commit(
                    safe.ReleaseKey("project-1.0.0"),
                    safe.RevisionNumber("00001"),
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
@pytest.mark.parametrize("expedited", [False, True])
async def test_vote_start_records_revision(sqlite_sessionmaker, monkeypatch, expedited: bool) -> None:
    monkeypatch.setattr(release.util, "number_of_release_files", mock.AsyncMock(return_value=1))
    async with sqlite_sessionmaker() as data:
        release_model = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            expedited=expedited,
        )
        await data.commit()
        await data.begin_immediate()
        release_model, _seq, revision = await _release_writer_with_data(data).start_vote_no_commit(
            safe.ReleaseKey("project-1.0.0"),
            None,
            promote=True,
        )
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
            voted_revision_number=voted_revision_number,
        )
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


def _completed_vote_task(
    vote_seq: int,
    added: datetime.datetime,
    *,
    email_to: str = "dev@project.apache.org",
    automatic_publish_when_resolved: bool = False,
) -> sql.Task:
    task = _vote_task(vote_seq, added, email_to=email_to)
    task.task_args["automatic_publish_when_resolved"] = automatic_publish_when_resolved
    task.status = sql.TaskStatus.COMPLETED
    task.completed = datetime.datetime(2026, 1, 1, 1, tzinfo=datetime.UTC)
    task.result = results.VoteInitiate(
        kind="vote_initiate",
        message="Vote announcement email sent successfully",
        email_to=email_to,
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
    writer._CommitteeParticipant__write = mock.MagicMock()
    writer._ReleaseManager__write = mock.MagicMock()
    writer._CommitteeMember__data = data
    writer._CommitteeMember__write_as = write_as
    writer._CommitteeMember__asf_uid = "chair"
    writer._CommitteeMember__committee_key = "project"
    writer._CommitteeMember__write = mock.MagicMock()
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


def _route_release() -> SimpleNamespace:
    return SimpleNamespace(
        committee=SimpleNamespace(key="project", is_podling=False),
        current_vote_seq=1,
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
    expedited: bool = False,
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
        vote_started=(
            datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
            if (phase == sql.ReleasePhase.RELEASE_CANDIDATE)
            else None
        ),
        current_vote_seq=current_vote_seq,
        expedited=expedited,
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


def _vote_task(vote_seq: int, added: datetime.datetime, *, email_to: str = "dev@project.apache.org") -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.QUEUED,
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
        asf_uid="chair",
        project_key="project",
        version_key="1.0.0",
        added=added,
    )
