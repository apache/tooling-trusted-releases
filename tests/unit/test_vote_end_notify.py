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
import atr.form as form_module
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.voting as shared_voting
import atr.storage as storage
import atr.storage.writers.vote as vote_writer
import atr.tasks.vote as task_vote


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
async def test_cancel_pending_vote_followups_marks_queued_tasks_failed(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        seeded = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_resolved=None,
        )
        notify_task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.VOTE_END_NOTIFY,
            task_args={"release_key": seeded.key, "vote_seq": 1, "recipient_id": "chair"},
            asf_uid="chair",
            project_key="project",
            version_key="1.0.0",
        )
        unrelated_task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.VOTE_END_NOTIFY,
            task_args={"release_key": "other-1.0.0", "vote_seq": 1, "recipient_id": "alice"},
            asf_uid="alice",
            project_key="other",
            version_key="1.0.0",
        )
        data.add_all([notify_task, unrelated_task])
        await data.commit()

        writer = object.__new__(vote_writer.CommitteeMember)
        writer._ReleaseManager__data = data
        await writer._cancel_pending_vote_followups(seeded)
        await data.commit()

    async with sqlite_sessionmaker() as data:
        rows = (await data.execute(sqlmodel.select(sql.Task).order_by(sql.Task.project_key))).scalars().all()
        by_project = {row.project_key: row for row in rows}
        assert by_project["project"].status == sql.TaskStatus.FAILED
        assert by_project["project"].error == "Vote resolved before reminder fired"
        assert by_project["project"].completed is not None
        assert by_project["other"].status == sql.TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_end_notify_sends_mail_when_vote_active(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_resolved=None,
        )
        await data.commit()

    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))
    monkeypatch.setattr(task_vote.config, "get", lambda: SimpleNamespace(APP_HOST="atr.example.org"))
    mail_send = mock.AsyncMock(return_value=("end-notify-mid@apache.org", []))
    monkeypatch.setattr(task_vote.storage, "write", _write_factory(mail_send))

    result = await task_vote.end_notify(
        args.VoteEndNotify(
            release_key="project-1.0.0",
            vote_seq=1,
            recipient_id="chair",
            vote_end="2026-01-04 00:00:00 UTC",
        ).model_dump()
    )

    assert isinstance(result, results.VoteEndNotify)
    assert result.sent is True
    assert result.skip_reason is None
    assert result.mid == "end-notify-mid@apache.org"
    mail_send.assert_awaited_once()
    await_args = mail_send.await_args
    assert await_args is not None
    sent_message = await_args.args[0]
    assert sent_message.email_sender == "chair@apache.org"
    assert sent_message.email_to == "chair@apache.org"
    assert "project-1.0.0" in sent_message.subject
    assert "https://atr.example.org/vote/project/1.0.0" in sent_message.body


@pytest.mark.asyncio
async def test_end_notify_skips_when_phase_not_candidate(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
            current_vote_seq=1,
            vote_resolved=None,
        )
        await data.commit()

    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))
    mail_send = mock.AsyncMock()
    monkeypatch.setattr(task_vote.storage, "write", _write_factory(mail_send))

    result = await task_vote.end_notify(
        args.VoteEndNotify(
            release_key="project-1.0.0",
            vote_seq=1,
            recipient_id="chair",
            vote_end="2026-01-04 00:00:00 UTC",
        ).model_dump()
    )

    assert result.sent is False
    assert result.skip_reason == "not_in_candidate_phase"
    mail_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_notify_skips_when_release_not_trusted(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        release = await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_resolved=None,
        )
        release.vote_mode = sql.VoteMode.EMAIL
        await data.commit()

    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))
    mail_send = mock.AsyncMock()
    monkeypatch.setattr(task_vote.storage, "write", _write_factory(mail_send))

    result = await task_vote.end_notify(
        args.VoteEndNotify(
            release_key="project-1.0.0",
            vote_seq=1,
            recipient_id="alice",
            vote_end="2026-01-04 00:00:00 UTC",
        ).model_dump()
    )

    assert result.sent is False
    assert result.skip_reason == "not_trusted_mode"
    mail_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_notify_skips_when_resolved(sqlite_sessionmaker, monkeypatch) -> None:
    resolved_at = datetime.datetime(2026, 1, 3, 12, tzinfo=datetime.UTC)
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_resolved=resolved_at,
        )
        await data.commit()

    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))
    mail_send = mock.AsyncMock()
    monkeypatch.setattr(task_vote.storage, "write", _write_factory(mail_send))

    result = await task_vote.end_notify(
        args.VoteEndNotify(
            release_key="project-1.0.0",
            vote_seq=1,
            recipient_id="chair",
            vote_end="2026-01-04 00:00:00 UTC",
        ).model_dump()
    )

    assert isinstance(result, results.VoteEndNotify)
    assert result.sent is False
    assert result.skip_reason == "already_resolved"
    mail_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_notify_skips_when_vote_seq_changed(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=2,
            vote_resolved=None,
        )
        await data.commit()

    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))
    mail_send = mock.AsyncMock()
    monkeypatch.setattr(task_vote.storage, "write", _write_factory(mail_send))

    result = await task_vote.end_notify(
        args.VoteEndNotify(
            release_key="project-1.0.0",
            vote_seq=1,
            recipient_id="chair",
            vote_end="2026-01-04 00:00:00 UTC",
        ).model_dump()
    )

    assert result.sent is False
    assert result.skip_reason == "vote_seq_changed"
    mail_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_notify_uses_recipient_id_for_sender_and_recipient(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(
            data,
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            current_vote_seq=1,
            vote_resolved=None,
        )
        await data.commit()

    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))
    monkeypatch.setattr(task_vote.config, "get", lambda: SimpleNamespace(APP_HOST="atr.example.org"))
    mail_send = mock.AsyncMock(return_value=("end-notify-mid@apache.org", []))
    captured_storage_uid: list[str | None] = []

    @contextlib.asynccontextmanager
    async def _capturing_write(asf_uid):
        captured_storage_uid.append(asf_uid)
        yield SimpleNamespace(as_foundation_committer=lambda: SimpleNamespace(mail=SimpleNamespace(send=mail_send)))

    monkeypatch.setattr(task_vote.storage, "write", _capturing_write)

    result = await task_vote.end_notify(
        args.VoteEndNotify(
            release_key="project-1.0.0",
            vote_seq=1,
            recipient_id="alice",
            vote_end="2026-01-04 00:00:00 UTC",
        ).model_dump()
    )

    assert result.sent is True
    assert captured_storage_uid == ["alice"]
    await_args = mail_send.await_args
    assert await_args is not None
    sent_message = await_args.args[0]
    assert sent_message.email_sender == "alice@apache.org"
    assert sent_message.email_to == "alice@apache.org"


def test_form_checkbox_value_matches_to_bool_contract() -> None:
    assert form_module.to_bool("on") is True
    with pytest.raises(ValueError):
        form_module.to_bool("y")


def test_initiate_args_round_trip_preserves_notify_flag() -> None:
    initiate = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="chair",
        initiator_fullname="Chair",
        subject="[VOTE] Release",
        body="Please vote",
        vote_seq=1,
        notify_when_finished=True,
    )
    serialised = initiate.model_dump()
    assert serialised["notify_when_finished"] is True
    assert bool(serialised.get("notify_when_finished", False)) is True

    legacy_args: dict[str, object] = {}
    assert bool(legacy_args.get("notify_when_finished", False)) is False


@pytest.mark.asyncio
async def test_initiate_skips_schedule_when_duration_zero(monkeypatch) -> None:
    release = _initiate_release_namespace()
    _patch_initiate_dependencies(monkeypatch, release)
    schedule_mock = mock.AsyncMock()
    monkeypatch.setattr(task_vote, "_schedule_end_notify", schedule_mock)

    task_args = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=0,
        initiator_id="chair",
        initiator_fullname="Chair",
        subject="[VOTE] Release",
        body="Please vote",
        vote_seq=1,
        notify_when_finished=True,
    )
    result = await task_vote._initiate_core_logic(task_args)

    assert isinstance(result, results.VoteInitiate)
    schedule_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_initiate_skips_schedule_when_release_not_trusted(monkeypatch) -> None:
    release = _initiate_release_namespace(vote_mode=sql.VoteMode.EMAIL)
    _patch_initiate_dependencies(monkeypatch, release)
    schedule_mock = mock.AsyncMock()
    monkeypatch.setattr(task_vote, "_schedule_end_notify", schedule_mock)

    task_args = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="chair",
        initiator_fullname="Chair",
        subject="[VOTE] Release",
        body="Please vote",
        vote_seq=1,
        notify_when_finished=True,
    )
    result = await task_vote._initiate_core_logic(task_args)

    assert isinstance(result, results.VoteInitiate)
    schedule_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_initiate_swallows_schedule_failures(monkeypatch) -> None:
    release = _initiate_release_namespace()
    _patch_initiate_dependencies(monkeypatch, release)

    async def _raise(**_kwargs):
        raise RuntimeError("failed to schedule vote end reminder")

    monkeypatch.setattr(task_vote, "_schedule_end_notify", _raise)

    task_args = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="chair",
        initiator_fullname="Chair",
        subject="[VOTE] Release",
        body="Please vote",
        vote_seq=1,
        notify_when_finished=True,
    )
    result = await task_vote._initiate_core_logic(task_args)

    assert isinstance(result, results.VoteInitiate)
    assert result.email_to == "dev@project.apache.org"


@pytest.mark.asyncio
async def test_schedule_end_notify_creates_scheduled_task(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))

    initiate = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="chair",
        initiator_fullname="Chair",
        subject="[VOTE] Release",
        body="please vote",
        vote_seq=1,
        notify_when_finished=True,
    )
    vote_end = datetime.datetime(2026, 1, 4, tzinfo=datetime.UTC)
    await task_vote._schedule_end_notify(
        task_args=initiate,
        project_key="project",
        version_key="1.0.0",
        vote_end=vote_end,
        vote_end_str="2026-01-04 00:00:00 UTC",
    )

    async with sqlite_sessionmaker() as data:
        notify_task = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.VOTE_END_NOTIFY))
        ).scalar_one()
        assert notify_task.scheduled == vote_end
        assert notify_task.project_key == "project"
        assert notify_task.version_key == "1.0.0"
        assert notify_task.task_args["release_key"] == "project-1.0.0"
        assert notify_task.task_args["vote_seq"] == 1
        assert notify_task.task_args["recipient_id"] == "chair"
        assert notify_task.task_args["vote_end"] == "2026-01-04 00:00:00 UTC"


@pytest.mark.asyncio
async def test_schedule_end_notify_no_op_without_vote_seq(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))

    initiate = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="chair",
        initiator_fullname="Chair",
        subject="[VOTE] Release",
        body="please vote",
        vote_seq=None,
        notify_when_finished=True,
    )
    await task_vote._schedule_end_notify(
        task_args=initiate,
        project_key="project",
        version_key="1.0.0",
        vote_end=datetime.datetime(2026, 1, 4, tzinfo=datetime.UTC),
        vote_end_str="2026-01-04 00:00:00 UTC",
    )

    async with sqlite_sessionmaker() as data:
        rows = (await data.execute(sqlmodel.select(sql.Task))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_schedule_end_notify_uses_initiator_as_recipient(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(task_vote.db, "session", _session_factory(sqlite_sessionmaker))
    monkeypatch.setattr(task_vote.config, "get", lambda: SimpleNamespace(APP_HOST="atr.example.org"))

    initiate = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="alice",
        initiator_fullname="Alice",
        subject="[VOTE] Release",
        body="Please vote",
        vote_seq=1,
        notify_when_finished=True,
    )

    await task_vote._schedule_end_notify(
        task_args=initiate,
        project_key="project",
        version_key="1.0.0",
        vote_end=datetime.datetime(2026, 1, 4, tzinfo=datetime.UTC),
        vote_end_str="2026-01-04 00:00:00 UTC",
    )

    async with sqlite_sessionmaker() as data:
        notify_task = (
            await data.execute(sqlmodel.select(sql.Task).where(sql.Task.task_type == sql.TaskType.VOTE_END_NOTIFY))
        ).scalar_one()
        assert notify_task.task_args["recipient_id"] == "alice"


def test_start_voting_form_accepts_notify_opt_in() -> None:
    parsed = shared_voting.StartVotingForm.model_validate(
        {
            "csrf_token": "token",
            "email_to": "dev@project.apache.org",
            "email_cc": [],
            "email_bcc": [],
            "second_round_email_to": None,
            "vote_duration": 72,
            "subject": "[VOTE] Release",
            "subject_template_hash": "hash",
            "body": "body",
            "notify_when_finished": "on",
            "vote_mode": sql.VoteMode.TRUSTED,
            "rendered_revision": "00001",
        }
    )
    assert parsed.notify_when_finished is True


@pytest.mark.asyncio
async def test_writer_start_rejects_notify_outside_trusted_mode() -> None:
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    committee = SimpleNamespace(key="project", is_podling=False, committee_members=["chair"])
    data.project = mock.MagicMock(
        return_value=SimpleNamespace(get=mock.AsyncMock(return_value=SimpleNamespace(committee=committee)))
    )

    release_for_start = SimpleNamespace(
        project_key="project",
        version="1.0.0",
        key="project-1.0.0",
        project=SimpleNamespace(status=sql.ProjectStatus.ACTIVE, is_active=True),
    )
    data.release = mock.MagicMock(return_value=SimpleNamespace(demand=mock.AsyncMock(return_value=release_for_start)))
    release_writer = SimpleNamespace(
        _start_vote_no_commit=mock.AsyncMock(
            return_value=(release_for_start, 1, sql.VoteMode.EMAIL, safe.RevisionNumber("00001"))
        ),
    )
    write_as = SimpleNamespace(release=release_writer, append_to_audit_log=mock.MagicMock())
    writer = object.__new__(vote_writer.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__write_as = write_as
    writer._ReleaseManager__asf_uid = "chair"
    writer._ReleaseManager__committee_key = "project"

    with pytest.raises(storage.AccessError, match="Trusted Vote mode"):
        await writer.start(
            "dev@project.apache.org",
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            72,
            "[VOTE] Release",
            "Please vote",
            "Project Chair",
            permitted_recipients=["dev@project.apache.org"],
            notify_when_finished=True,
        )

    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()


def _initiate_release_namespace(
    vote_mode: sql.VoteMode = sql.VoteMode.TRUSTED,
    *,
    committee_key: str = "project",
) -> SimpleNamespace:
    return SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        current_vote_seq=1,
        latest_revision_number="00001",
        version="1.0.0",
        project=SimpleNamespace(key="project"),
        safe_project_key=SimpleNamespace(__str__=lambda self: "project"),
        safe_version_key=SimpleNamespace(__str__=lambda self: "1.0.0"),
        safe_latest_revision_number=SimpleNamespace(__str__=lambda self: "00001"),
        committee=SimpleNamespace(is_podling=False, key=committee_key),
        podling_thread_id=None,
        effective_vote_mode=vote_mode,
    )


def _patch_initiate_dependencies(monkeypatch, release: SimpleNamespace) -> None:
    query = mock.AsyncMock()
    query.demand = mock.AsyncMock(return_value=release)
    mock_data = mock.MagicMock()
    mock_data.release = mock.MagicMock(return_value=query)

    @contextlib.asynccontextmanager
    async def _session(**_kwargs):
        yield mock_data

    monkeypatch.setattr(task_vote.db, "session", _session)
    monkeypatch.setattr(task_vote.interaction, "tasks_ongoing", mock.AsyncMock(return_value=0))
    monkeypatch.setattr(
        task_vote.util,
        "permitted_podling_first_round_recipients",
        lambda uid, committee_key, *, is_podling, project=None: ["dev@project.apache.org"],
    )

    mock_send = mock.AsyncMock(return_value=("vote-mid@apache.org", []))
    mock_wafc = SimpleNamespace(mail=SimpleNamespace(send=mock_send))
    mock_write = SimpleNamespace(as_foundation_committer=lambda: mock_wafc)

    @contextlib.asynccontextmanager
    async def _storage_write(_asf_uid):
        yield mock_write

    monkeypatch.setattr(task_vote.storage, "write", _storage_write)


async def _seed_release(
    data: db.Session,
    *,
    phase: sql.ReleasePhase,
    current_vote_seq: int | None,
    vote_resolved: datetime.datetime | None,
) -> sql.Release:
    committee = sql.Committee(
        key="project",
        name="Project",
        is_podling=False,
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
        vote_mode=sql.VoteMode.TRUSTED,
        vote_resolved=vote_resolved,
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
    return release


def _session_factory(sessionmaker):
    @contextlib.asynccontextmanager
    async def _session():
        async with sessionmaker() as data:
            yield data

    return _session


def _write_factory(mail_send: mock.AsyncMock):
    @contextlib.asynccontextmanager
    async def _write(_asf_uid):
        yield SimpleNamespace(as_foundation_committer=lambda: SimpleNamespace(mail=SimpleNamespace(send=mail_send)))

    return _write
