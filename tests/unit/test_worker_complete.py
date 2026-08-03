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

import psutil
import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.tasks.task as task
import atr.worker as worker


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


async def test_completion_deletes_a_recurring_task_and_logs_it(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed_log = mock.Mock()
    monkeypatch.setattr(worker, "_task_completed_log", completed_log)
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=os.getpid(), task_type=sql.TaskType.MAINTENANCE)
        data.add(task_row)
        await data.commit()

        await worker._task_result_process(task_row.id, None, task.COMPLETED)

        remaining = (await data.execute(sqlmodel.select(sql.Task))).first()
        assert remaining is None
        completed_log.assert_called_once()
        record = completed_log.call_args.args[0]
        assert record["task_type"] == sql.TaskType.MAINTENANCE.value
        assert record["status"] == sql.TaskStatus.COMPLETED.value


async def test_completion_does_not_overwrite_a_finalised_task(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=os.getpid())
        data.add(task_row)
        await data.commit()

        finalised = await task.finalise_failure(task_row.id, os.getpid(), "took too long", task.BROKEN)
        await worker._task_result_process(task_row.id, None, task.COMPLETED)

        assert finalised is True
        await data.refresh(task_row)
        assert task_row.status == sql.TaskStatus.BROKEN
        assert task_row.error == "took too long"


async def test_completion_is_fenced_and_stores_a_null_result(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=os.getpid())
        data.add(task_row)
        await data.commit()

        await worker._task_result_process(task_row.id, None, task.COMPLETED)

        await data.refresh(task_row)
        assert task_row.status == sql.TaskStatus.COMPLETED
        assert task_row.completed is not None


async def test_task_claim_records_the_process_creation_time(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        task_row = sql.Task(task_type=sql.TaskType.COMPARE_SOURCE_TREES, task_args={}, asf_uid="alice")
        data.add(task_row)
        await data.commit()

        claimed = await worker._task_next_claim()

        assert claimed is not None
        await data.refresh(task_row)
        assert task_row.pid == os.getpid()
        assert task_row.pid_created == psutil.Process().create_time()


def _active_task(pid: int, task_type: sql.TaskType = sql.TaskType.COMPARE_SOURCE_TREES) -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.ACTIVE,
        task_type=task_type,
        task_args={},
        asf_uid="alice",
        started=datetime.datetime.now(datetime.UTC),
        pid=pid,
    )
