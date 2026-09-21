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

import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.models.results as results
import atr.models.sql as sql
import atr.tasks.maintenance as maintenance
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


@pytest.mark.parametrize("task_type", [sql.TaskType.MAINTENANCE, sql.TaskType.INTEGRITY_CHECK])
async def test_completion_deletes_a_recurring_task_and_logs_it(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch, task_type: sql.TaskType
) -> None:
    completed_log = mock.Mock()
    monkeypatch.setattr(worker, "_task_completed_log", completed_log)
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=os.getpid(), task_type=task_type)
        data.add(task_row)
        await data.commit()

        await worker._task_result_process(task_row.id, None, task.COMPLETED)

        remaining = (await data.execute(sqlmodel.select(sql.Task))).first()
        assert remaining is None
        completed_log.assert_called_once()
        record = completed_log.call_args.args[0]
        assert record["task_type"] == task_type.value
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


@pytest.mark.parametrize("kind", list(config.SvnPublishKind))
async def test_completion_queues_one_download_monitor(sqlite_sessionmaker, monkeypatch, kind) -> None:
    monkeypatch.setattr(config, "svn_publish_kind", lambda: kind)
    async with sqlite_sessionmaker() as data:
        parent = _active_task(pid=os.getpid(), task_type=sql.TaskType.SVN_PUBLISH)
        parent.task_args = {
            "asf_uid": "alice",
            "project_key": "example",
            "version_key": "1.0",
            "revision_number": "00001",
            "download_path_suffix": "saved-path",
        }
        data.add(parent)
        await data.commit()
        result = results.SvnPublish(kind="svn_publish", svn_revision=42, message="Published")
        await worker._task_result_process(parent.id, result, task.COMPLETED)
        await worker._task_result_process(parent.id, result, task.COMPLETED)
        monitors = await data.task(task_type=sql.TaskType.DOWNLOADS_CHECK).all()
        if kind is config.SvnPublishKind.LOCAL_REPOSITORY:
            assert not monitors
            return
        assert len(monitors) == 1
        monitor = monitors[0]
        assert (
            await data.task(task_type=sql.TaskType.DOWNLOADS_CHECK, task_args={"publish_task_id": parent.id}).get()
            is monitor
        )
        assert monitor.task_args == {"publish_task_id": parent.id}
        assert monitor.asf_uid == constants.SYSTEM_SERVICE_UID
        assert (monitor.project_key, monitor.version_key, monitor.revision_number) == ("example", "1.0", "00001")


async def test_deferral_saves_progress_and_preserves_it_with_default_delay(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        row = _active_task(pid=os.getpid(), task_type=sql.TaskType.DOWNLOADS_CHECK)
        data.add(row)
        await data.commit()
        checkpoint = results.DownloadsCheck(waiting_for="b.tar.gz", total=3)
        before = datetime.datetime.now(datetime.UTC)
        await worker._task_defer(row.id, seconds=30, checkpoint=checkpoint)
        await data.refresh(row)
        assert row.status is sql.TaskStatus.QUEUED
        assert row.result == checkpoint
        assert row.started is row.pid is row.pid_created is None
        assert 30 <= (row.scheduled - before).total_seconds() < 35
        scheduled = row.scheduled
        await worker._task_defer(row.id, seconds=0, checkpoint=results.DownloadsCheck())
        await data.refresh(row)
        assert (row.result, row.scheduled) == (checkpoint, scheduled)
        row.status, row.started, row.pid = sql.TaskStatus.ACTIVE, datetime.datetime.now(datetime.UTC), os.getpid()
        await data.commit()
        before = datetime.datetime.now(datetime.UTC)
        await worker._task_defer(row.id)
        await data.refresh(row)
        assert row.result == checkpoint
    assert 120 <= (row.scheduled - before).total_seconds() < 125


async def test_maintenance_backfills_only_current_publications_once(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(config, "svn_publish_kind", lambda: config.SvnPublishKind.ASF_DISTRIBUTION)
    async with sqlite_sessionmaker() as data:
        data.add(sql.Project(key="example"))
        for version, revisions in [("0.5", 0), ("1.0", 1), ("2.0", 2)]:
            data.add(
                sql.Release(
                    key=f"example-{version}",
                    project_key="example",
                    version=version,
                    created=datetime.datetime.now(datetime.UTC),
                    phase=sql.ReleasePhase.RELEASE_PREVIEW,
                )
            )
            for _ in range(revisions):
                data.add(
                    sql.Revision(
                        release_key=f"example-{version}",
                        asfuid="alice",
                        phase=sql.ReleasePhase.RELEASE_PREVIEW,
                    )
                )
            data.add(
                sql.Task(
                    task_type=sql.TaskType.SVN_PUBLISH,
                    status=sql.TaskStatus.COMPLETED,
                    started=datetime.datetime.now(datetime.UTC),
                    completed=datetime.datetime.now(datetime.UTC),
                    pid=os.getpid(),
                    project_key="example",
                    version_key=version,
                    revision_number="00001",
                    asf_uid="alice",
                    task_args=dict(
                        asf_uid="alice", project_key="example", version_key=version, revision_number="00001"
                    ),
                    result=results.SvnPublish(kind="svn_publish", svn_revision=42, message="Published"),
                )
            )
        await data.commit()
    await maintenance._downloads_monitor_maintenance()
    await maintenance._downloads_monitor_maintenance()
    async with sqlite_sessionmaker() as data:
        monitors = await data.task(task_type=sql.TaskType.DOWNLOADS_CHECK).all()
        assert len(monitors) == 1
        assert (monitors[0].version_key, monitors[0].status) == ("1.0", sql.TaskStatus.QUEUED)


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
