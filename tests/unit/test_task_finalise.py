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
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sbom.streaming as streaming
import atr.tasks.checks as checks
import atr.tasks.checks.sbom as sbom
import atr.tasks.task as task


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


async def test_finalise_failure_notifies_for_non_check_tasks(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=1234, task_type=sql.TaskType.SBOM_GENERATE)
        data.add(task_row)
        await data.commit()

        finalised = await task.finalise_failure(task_row.id, 1234, "boom", task.FAILED, caller_data=data)

        assert finalised is True
        await data.refresh(task_row)
        assert task_row.status == sql.TaskStatus.FAILED
        assert (await data.execute(sqlmodel.select(sql.CheckResult))).first() is None
        notification = (await data.execute(sqlmodel.select(sql.Notification))).scalar_one()
        assert notification.asf_uid == "alice"
        assert "failed" in notification.message


async def test_finalise_failure_dedupes_repeated_notifications(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        first = _active_task(pid=1234, task_type=sql.TaskType.SBOM_GENERATE)
        second = _active_task(pid=1234, task_type=sql.TaskType.SBOM_GENERATE)
        second.inputs_hash = "blake3:4567"
        data.add(first)
        data.add(second)
        await data.commit()

        assert await task.finalise_failure(first.id, 1234, "boom", task.FAILED, caller_data=data) is True
        assert await task.finalise_failure(second.id, 1234, "boom", task.FAILED, caller_data=data) is True

        notification = (await data.execute(sqlmodel.select(sql.Notification))).scalar_one()
        assert notification.asf_uid == "alice"


async def test_finalise_failure_preserves_sbom_license_concern(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(db, "session", sqlite_sessionmaker)
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=1234, task_type=sql.TaskType.SBOM_REVIEW)
        data.add(task_row)
        await data.commit()
        recorder = await checks.Recorder.create(
            sbom.review,
            sbom.REVIEW_VERSION,
            task_row.inputs_hash,
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            safe.RelPath(task_row.primary_rel_path),
        )
        inventory = streaming.Inventory(
            licensed_components=[streaming.LicensedComponent("a", None, None, [("GPL-3.0-only", False)])]
        )
        await sbom._licenses(recorder, inventory)
        assert await task.finalise_failure(task_row.id, 1234, "lookup failed", task.BROKEN, caller_data=data)
        rows = (await data.execute(sqlmodel.select(sql.CheckResult))).scalars().all()
        assert {row.status for row in rows} == {sql.CheckResultStatus.CONCERN, sql.CheckResultStatus.EXCEPTION}
        concern = next(row for row in rows if (row.status == sql.CheckResultStatus.CONCERN))
        assert concern.data["license_count"] == 1
        assert all(row.inputs_hash == task_row.inputs_hash for row in rows)
        assert all(row.checker == checks.function_key(sbom.review) for row in rows)


async def test_finalise_failure_skips_notification_for_the_system_uid(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=1234, asf_uid="system", task_type=sql.TaskType.SBOM_GENERATE)
        data.add(task_row)
        await data.commit()

        finalised = await task.finalise_failure(task_row.id, 1234, "boom", task.FAILED, caller_data=data)

        assert finalised is True
        await data.refresh(task_row)
        assert task_row.status == sql.TaskStatus.FAILED
        assert (await data.execute(sqlmodel.select(sql.Notification))).first() is None


async def test_finalise_failure_writes_all_records_together(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=1234)
        data.add(task_row)
        await data.commit()

        finalised = await task.finalise_failure(
            task_row.id, 1234, "clone failed", task.BROKEN, error_data={"repo_url": "u"}, caller_data=data
        )

        assert finalised is True
        await data.refresh(task_row)
        assert task_row.status == sql.TaskStatus.BROKEN
        assert task_row.error == "clone failed"
        check_result = (await data.execute(sqlmodel.select(sql.CheckResult))).scalar_one()
        assert check_result.status == sql.CheckResultStatus.EXCEPTION
        assert check_result.checker == "atr.tasks.checks.compare.source_trees"
        assert check_result.message == "clone failed"
        assert check_result.data == {"repo_url": "u"}
        assert check_result.inputs_hash == "blake3:0123"
        assert (await data.execute(sqlmodel.select(sql.Notification))).first() is None


async def test_finalise_failure_writes_nothing_for_a_stale_pid(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        task_row = _active_task(pid=1234)
        data.add(task_row)
        await data.commit()

        finalised = await task.finalise_failure(task_row.id, 4321, "late result", task.FAILED, caller_data=data)

        assert finalised is False
        await data.refresh(task_row)
        assert task_row.status == sql.TaskStatus.ACTIVE
        assert (await data.execute(sqlmodel.select(sql.CheckResult))).first() is None
        assert (await data.execute(sqlmodel.select(sql.Notification))).first() is None


def _active_task(
    pid: int, asf_uid: str = "alice", task_type: sql.TaskType = sql.TaskType.COMPARE_SOURCE_TREES
) -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.ACTIVE,
        task_type=task_type,
        task_args={},
        asf_uid=asf_uid,
        started=datetime.datetime.now(datetime.UTC),
        pid=pid,
        project_key="project",
        version_key="1.0.0",
        revision_number="00001",
        primary_rel_path="apache-project-1.0.0.tar.gz",
        inputs_hash="blake3:0123",
    )
