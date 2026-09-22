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

import unittest.mock as mock
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.storage.writers.notifications as notifications


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


@pytest.mark.parametrize("is_admin", [False, True])
async def test_create_dedupes_and_audits_once(sqlite_sessionmaker, is_admin: bool) -> None:
    async with sqlite_sessionmaker() as data:
        write_as = mock.MagicMock()
        writer = object.__new__(notifications.FoundationCommitter)
        writer._FoundationCommitter__write = mock.MagicMock()
        writer._FoundationCommitter__write_as = write_as
        writer._FoundationCommitter__data = data
        writer._FoundationCommitter__asf_uid = "alice"

        first = await writer.create("The  SVN dist repository was not reachable", is_admin=is_admin)
        second = await writer.create("The SVN dist repository was not reachable", is_admin=is_admin)

        assert first is not None
        assert second is None
        notification = (await data.execute(sqlmodel.select(sql.Notification))).scalar_one()
        assert notification.id == first.id
        assert notification.message == "The SVN dist repository was not reachable"
        assert notification.is_admin is is_admin
        write_as.append_to_audit_log.assert_called_once_with(asf_uid="alice", notification_id=first.id, level="error")


@pytest.mark.parametrize("is_admin", [False, True])
async def test_create_updates_admin_classification(sqlite_sessionmaker, is_admin: bool) -> None:
    async with sqlite_sessionmaker() as data:
        writer = notifications.FoundationCommitter(mock.MagicMock(), mock.MagicMock(asf_uid="alice"), data)
        first = await writer.create("Service unavailable", is_admin=not is_admin)
        assert first is not None
        original = (first.id, first.created, first.dedup_hash)

        updated = await writer.create("Service unavailable", is_admin=is_admin)

        assert updated is not None
        assert (updated.id, updated.created, updated.dedup_hash) == original
        assert updated.is_admin is is_admin
        assert await writer.create("Service unavailable", is_admin=is_admin) is None
        stored = (await data.execute(sqlmodel.select(sql.Notification))).scalar_one()
        assert stored.message == "Service unavailable"
        assert stored.is_admin is is_admin


async def test_replace_dedupes_normalised_message(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        write_as = mock.MagicMock(asf_uid="alice")
        writer = notifications.FoundationCommitter(mock.MagicMock(), write_as, data)
        link = "/admin/data?tab=validation"

        await writer.replace("Integrity  check failed", link)
        first = (await data.execute(sqlmodel.select(sql.Notification))).scalar_one()
        original = (first.id, first.created)
        write_as.append_to_audit_log.reset_mock()
        await writer.replace("Integrity check failed", link, is_admin=True)
        await writer.replace("Integrity check failed", link, is_admin=True)

        notification = (await data.execute(sqlmodel.select(sql.Notification))).scalar_one()
        assert (notification.id, notification.created) == original
        assert notification.message == "Integrity check failed"
        assert notification.is_admin
        write_as.append_to_audit_log.assert_called_once_with(
            asf_uid="alice", link=link, removed=0, notification_id=notification.id
        )
