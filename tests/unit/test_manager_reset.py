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
import signal
import unittest.mock as mock
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.manager as manager
import atr.models.sql as sql


@pytest.fixture
async def sqlite_sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    monkeypatch.setattr(db, "session", lambda log_queries=None: sessionmaker())
    yield sessionmaker
    await engine.dispose()


async def test_an_overrun_orphan_which_exits_before_the_kill_is_not_killed(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=100.0, age_seconds=301)
    birth_times = iter([100.0, None])
    monkeypatch.setattr(manager, "_process_created", lambda pid: next(birth_times))
    killpg = mock.Mock()
    monkeypatch.setattr(manager.os, "killpg", killpg)

    await manager.WorkerManager().reset_broken_tasks()

    task_row = await _get_task(sqlite_sessionmaker, task_id)
    assert task_row.status == sql.TaskStatus.BROKEN
    killpg.assert_not_called()


async def test_reset_fails_and_kills_an_overrun_orphan(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=100.0, age_seconds=301)
    monkeypatch.setattr(manager, "_process_created", lambda pid: 100.0)
    killpg = mock.Mock()
    monkeypatch.setattr(manager.os, "killpg", killpg)

    await manager.WorkerManager().reset_broken_tasks()

    task_row = await _get_task(sqlite_sessionmaker, task_id)
    assert task_row.status == sql.TaskStatus.BROKEN
    assert task_row.error is not None
    killpg.assert_called_once_with(1234, signal.SIGKILL)


async def test_reset_keeps_a_live_untracked_task(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=100.0)
    monkeypatch.setattr(manager, "_process_created", lambda pid: 100.0)
    killpg = mock.Mock()
    monkeypatch.setattr(manager.os, "killpg", killpg)

    await manager.WorkerManager().reset_broken_tasks()

    task_row = await _get_task(sqlite_sessionmaker, task_id)
    assert task_row.status == sql.TaskStatus.ACTIVE
    killpg.assert_not_called()


async def test_reset_kills_a_surviving_group_before_requeueing(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=100.0)
    group_members = iter([[4321], []])
    monkeypatch.setattr(manager, "_process_created", lambda pid: None)
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: next(group_members))
    killpg = mock.Mock()
    monkeypatch.setattr(manager.os, "killpg", killpg)
    worker_manager = manager.WorkerManager()

    await worker_manager.reset_broken_tasks()
    task_row = await _get_task(sqlite_sessionmaker, task_id)

    assert task_row.status == sql.TaskStatus.ACTIVE
    killpg.assert_called_once_with(1234, signal.SIGKILL)

    await worker_manager.reset_broken_tasks()
    task_row = await _get_task(sqlite_sessionmaker, task_id)

    assert task_row.status == sql.TaskStatus.QUEUED


async def test_reset_requeues_a_dead_claimant(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=100.0)
    monkeypatch.setattr(manager, "_process_created", lambda pid: None)
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [])

    await manager.WorkerManager().reset_broken_tasks()

    task_row = await _get_task(sqlite_sessionmaker, task_id)
    assert task_row.status == sql.TaskStatus.QUEUED
    assert task_row.started is None
    assert task_row.pid is None
    assert task_row.pid_created is None


async def test_reset_requeues_a_legacy_claim_without_killing(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=None)
    monkeypatch.setattr(manager, "_process_created", lambda pid: 100.0)
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [4321])
    killpg = mock.Mock()
    monkeypatch.setattr(manager.os, "killpg", killpg)

    await manager.WorkerManager().reset_broken_tasks()

    task_row = await _get_task(sqlite_sessionmaker, task_id)
    assert task_row.status == sql.TaskStatus.QUEUED
    killpg.assert_not_called()


async def test_reset_requeues_a_reused_pid(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=100.0)
    monkeypatch.setattr(manager, "_process_created", lambda pid: 200.0)
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [4321])
    killpg = mock.Mock()
    monkeypatch.setattr(manager.os, "killpg", killpg)

    await manager.WorkerManager().reset_broken_tasks()

    task_row = await _get_task(sqlite_sessionmaker, task_id)
    assert task_row.status == sql.TaskStatus.QUEUED
    killpg.assert_not_called()


async def test_reset_tolerates_a_small_creation_time_shift(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = await _add_task(sqlite_sessionmaker, pid_created=100.0)
    monkeypatch.setattr(manager, "_process_created", lambda pid: 101.5)

    await manager.WorkerManager().reset_broken_tasks()

    task_row = await _get_task(sqlite_sessionmaker, task_id)
    assert task_row.status == sql.TaskStatus.ACTIVE


async def _add_task(sqlite_sessionmaker, pid_created: float | None, age_seconds: float = 0) -> int:
    async with sqlite_sessionmaker() as data:
        task_row = sql.Task(
            status=sql.TaskStatus.ACTIVE,
            task_type=sql.TaskType.COMPARE_SOURCE_TREES,
            task_args={},
            asf_uid="alice",
            started=datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=age_seconds),
            pid=1234,
            pid_created=pid_created,
        )
        data.add(task_row)
        await data.commit()
        return task_row.id


async def _get_task(sqlite_sessionmaker, task_id: int) -> sql.Task:
    async with sqlite_sessionmaker() as data:
        return await data.task(id=task_id).demand(RuntimeError("Task disappeared"))
