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

import collections.abc as abc
import datetime
import unittest.mock as mock

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.cache as cache
import atr.constants as constants
import atr.db as db
import atr.log as log
import atr.models.sql as sql
import atr.tasks as tasks
import atr.validate as validate


@pytest.fixture
async def sqlite_sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> abc.AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    monkeypatch.setattr(db, "session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


@pytest.mark.parametrize("outcome", ["healthy", "findings", "exception"])
async def test_integrity_check(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    divergence = validate.AnnotatedDivergence(
        ["Project.name"], "project_name", "example", validate.Divergence("Apache Example", "Example")
    )
    divergences = mock.MagicMock()
    divergences.__aiter__.return_value = [divergence] if outcome == "findings" else []
    monkeypatch.setattr(validate, "everything", mock.Mock(return_value=divergences))
    report = (
        validate.Consistency(["missing"], ["orphan"], []) if outcome == "findings" else validate.Consistency([], [], [])
    )
    consistency = mock.AsyncMock(return_value=report)
    if outcome == "exception":
        consistency.side_effect = RuntimeError("scan failed")
    monkeypatch.setattr(validate, "consistency", consistency)
    monkeypatch.setattr(cache, "admins_get_async", mock.AsyncMock(return_value=frozenset({"alice", "bob"})))
    error_log = mock.Mock()
    monkeypatch.setattr(log, "error", error_log)

    started = datetime.datetime.now(datetime.UTC)
    handler = tasks.resolve(sql.TaskType.INTEGRITY_CHECK)
    task_args = {"asf_uid": constants.SYSTEM_SERVICE_UID, "next_schedule_seconds": 1200}
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="scan failed"):
            await handler(task_args)
    else:
        assert await handler(task_args) is None

    async with sqlite_sessionmaker() as data:
        queued = (await data.execute(sqlmodel.select(sql.Task))).scalar_one()
        assert queued.task_type == sql.TaskType.INTEGRITY_CHECK
        assert queued.status == sql.TaskStatus.QUEUED
        assert queued.task_args == task_args
        assert queued.scheduled is not None
        assert started + datetime.timedelta(seconds=1200) <= queued.scheduled
        assert queued.scheduled <= datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1200)
        notifications = (await data.execute(sqlmodel.select(sql.Notification))).scalars().all()

    if outcome == "healthy":
        assert not notifications
        error_log.assert_not_called()
        return
    assert {notification.asf_uid for notification in notifications} == {"alice", "bob"}
    assert len(notifications) == 2
    assert all(notification.level == sql.NotificationLevel.ERROR for notification in notifications)
    assert all(notification.link == "/admin/data?tab=validation" for notification in notifications)
    if outcome == "exception":
        assert all("could not complete" in notification.message for notification in notifications)
        return
    assert all("1 validation errors and 2 consistency errors" in notification.message for notification in notifications)
    error_log.assert_has_calls(
        [
            mock.call(f"Integrity validation error: {divergence!r}"),
            mock.call("Integrity consistency error: directory missing from filesystem: missing"),
            mock.call("Integrity consistency error: directory missing from database: orphan"),
        ]
    )
