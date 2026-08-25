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


async def test_create_dedupes_and_audits_once(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        write_as = mock.MagicMock()
        writer = object.__new__(notifications.FoundationCommitter)
        writer._FoundationCommitter__write = mock.MagicMock()
        writer._FoundationCommitter__write_as = write_as
        writer._FoundationCommitter__data = data
        writer._FoundationCommitter__asf_uid = "alice"

        first = await writer.create("The  SVN dist repository was not reachable")
        second = await writer.create("The SVN dist repository was not reachable")

        assert first is not None
        assert second is None
        notification = (await data.execute(sqlmodel.select(sql.Notification))).scalar_one()
        assert notification.id == first.id
        assert notification.message == "The SVN dist repository was not reachable"
        write_as.append_to_audit_log.assert_called_once_with(asf_uid="alice", notification_id=first.id, level="error")
