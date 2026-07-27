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
import os
import unittest.mock as mock
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.manager as manager
import atr.models.sql as sql
import atr.tasks.task as task
import atr.worker as worker


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


async def test_check_handler_is_not_built_for_stale_generation(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
) -> None:
    task_id = await _add_task(
        sqlite_sessionmaker,
        status=sql.TaskStatus.ACTIVE,
        pid=os.getpid(),
        execution_generation=2,
    )
    monkeypatch.setattr(worker.db, "session", lambda: sqlite_sessionmaker())
    handler = mock.AsyncMock()
    handler.__name__ = "handler"

    with pytest.raises(ValueError, match="no longer owned"):
        await worker._execute_check_task(handler, {}, task_id, "check", 1)

    handler.assert_not_awaited()


async def test_claim_increments_execution_generation(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
) -> None:
    task_id = await _add_task(sqlite_sessionmaker)
    monkeypatch.setattr(worker.db, "session", lambda: sqlite_sessionmaker())

    first_claim = await worker._task_next_claim()

    assert first_claim is not None
    assert first_claim[0] == task_id
    assert first_claim[4] == 1

    async with sqlite_sessionmaker() as data:
        task_obj = await data.task(id=task_id).demand(AssertionError("task missing"))
        task_obj.status = sql.TaskStatus.QUEUED
        task_obj.started = None
        task_obj.pid = None
        await data.commit()

    second_claim = await worker._task_next_claim()

    assert second_claim is not None
    assert second_claim[0] == task_id
    assert second_claim[4] == 2


async def test_completed_recurring_task_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
) -> None:
    task_id = await _add_task(
        sqlite_sessionmaker,
        status=sql.TaskStatus.ACTIVE,
        pid=os.getpid(),
        execution_generation=1,
    )
    monkeypatch.setattr(worker.db, "session", lambda: sqlite_sessionmaker())
    completed_log = mock.Mock()
    monkeypatch.setattr(worker, "_task_completed_log", completed_log)

    await worker._task_result_process(task_id, 1, None, task.COMPLETED)

    async with sqlite_sessionmaker() as data:
        assert await data.task(id=task_id).get() is None
    completed_log.assert_called_once()


async def test_completed_task_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
) -> None:
    task_id = await _add_task(
        sqlite_sessionmaker,
        status=sql.TaskStatus.ACTIVE,
        pid=os.getpid(),
        execution_generation=1,
        task_type=sql.TaskType.RAT_CHECK,
    )
    monkeypatch.setattr(worker.db, "session", lambda: sqlite_sessionmaker())

    await worker._task_result_process(task_id, 1, None, task.COMPLETED)

    async with sqlite_sessionmaker() as data:
        task_obj = await data.task(id=task_id).demand(AssertionError("task missing"))
        assert task_obj.status == sql.TaskStatus.COMPLETED
        assert task_obj.completed is not None


async def test_stale_result_cannot_finish_new_generation(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
) -> None:
    task_id = await _add_task(
        sqlite_sessionmaker,
        status=sql.TaskStatus.ACTIVE,
        pid=os.getpid(),
        execution_generation=2,
    )
    monkeypatch.setattr(worker.db, "session", lambda: sqlite_sessionmaker())
    notify_failure = mock.AsyncMock()
    monkeypatch.setattr(worker.task, "notify_failure", notify_failure)

    await worker._task_result_process(task_id, 1, None, task.FAILED, "stale failure")

    async with sqlite_sessionmaker() as data:
        task_obj = await data.task(id=task_id).demand(AssertionError("task missing"))
        assert task_obj.status == sql.TaskStatus.ACTIVE
        assert task_obj.error is None
    notify_failure.assert_not_awaited()

    await worker._task_result_process(task_id, 2, None, task.FAILED, "current failure")

    async with sqlite_sessionmaker() as data:
        task_obj = await data.task(id=task_id).demand(AssertionError("task missing"))
        assert task_obj.status == sql.TaskStatus.FAILED
        assert task_obj.error == "current failure"
    notify_failure.assert_awaited_once()


async def test_timeout_transition_rejects_stale_generation(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
) -> None:
    pid = 4242
    task_id = await _add_task(
        sqlite_sessionmaker,
        status=sql.TaskStatus.ACTIVE,
        pid=pid,
        execution_generation=2,
    )
    worker_manager = manager.WorkerManager()
    worker_process = mock.Mock(pid=pid)
    killpg = mock.Mock()
    monkeypatch.setattr(manager.os, "killpg", killpg)

    async with sqlite_sessionmaker() as data:
        async with data.begin():
            failed_task = await worker_manager.terminate_long_running_task(data, worker_process, task_id, pid, 1, 300)
        assert failed_task is None

    async with sqlite_sessionmaker() as data:
        task_obj = await data.task(id=task_id).demand(AssertionError("task missing"))
        assert task_obj.status == sql.TaskStatus.ACTIVE
    killpg.assert_not_called()

    async with sqlite_sessionmaker() as data:
        async with data.begin():
            failed_task = await worker_manager.terminate_long_running_task(data, worker_process, task_id, pid, 2, 300)
        assert failed_task is not None

    async with sqlite_sessionmaker() as data:
        task_obj = await data.task(id=task_id).demand(AssertionError("task missing"))
        assert task_obj.status == sql.TaskStatus.FAILED
    killpg.assert_called_once_with(pid, manager.signal.SIGTERM)


async def _add_task(
    sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
    *,
    status: sql.TaskStatus = sql.TaskStatus.QUEUED,
    pid: int | None = None,
    execution_generation: int = 0,
    task_type: sql.TaskType = sql.TaskType.MAINTENANCE,
) -> int:
    async with sessionmaker() as data:
        task_obj = sql.Task(
            status=status,
            task_type=task_type,
            task_args={},
            asf_uid="test",
            started=datetime.datetime.now(datetime.UTC) if status == sql.TaskStatus.ACTIVE else None,
            pid=pid,
            execution_generation=execution_generation,
        )
        data.add(task_obj)
        await data.commit()
        assert task_obj.id is not None
        return task_obj.id
