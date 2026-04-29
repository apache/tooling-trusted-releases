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
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
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
                    selected_revision_number=release_model.safe_latest_revision_number,
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
                _release, vote_seq, _vote_mode = await writer._start_vote_no_commit(
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


def _member_writer_with_data(data: db.Session, write_as: SimpleNamespace) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__write_as = write_as
    writer._CommitteeParticipant__asf_uid = "chair"
    writer._CommitteeParticipant__committee_key = "project"
    writer._CommitteeMember__data = data
    writer._CommitteeMember__write_as = write_as
    writer._CommitteeMember__asf_uid = "chair"
    writer._CommitteeMember__committee_key = "project"
    return writer


def _release_writer_with_data(data: db.Session) -> release.CommitteeParticipant:
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "chair"
    return writer


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
