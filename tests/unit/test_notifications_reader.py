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

import atr.db as db
import atr.models.sql as sql
import atr.storage.readers.notifications as notifications


@pytest.fixture
async def sqlite_sessionmaker() -> abc.AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=sqlalchemy.pool.StaticPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    yield sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize(
    ("include_admin", "expected"),
    [(None, ["admin1", "admin2"]), (True, ["admin1", "admin2"]), (False, ["ordinary1", "ordinary2"])],
)
async def test_pending_filters_before_limit(
    sqlite_sessionmaker, include_admin: bool | None, expected: list[str]
) -> None:
    async with sqlite_sessionmaker() as data:
        for uid, message, is_admin in [
            ("bob", "other admin", True),
            ("bob", "other ordinary", False),
            ("alice", "admin1", True),
            ("alice", "admin2", True),
            ("alice", "ordinary1", False),
            ("alice", "ordinary2", False),
            ("alice", "ordinary3", False),
        ]:
            data.add(
                sql.Notification(
                    asf_uid=uid,
                    created=datetime.datetime(2026, 9, 22, tzinfo=datetime.UTC),
                    message=message,
                    dedup_hash=message,
                    is_admin=is_admin,
                )
            )
        await data.commit()
        read = mock.MagicMock()
        read.authorisation.asf_uid = "alice"
        reader = notifications.FoundationCommitter(read, mock.MagicMock(), data)

        if include_admin is None:
            pending = await reader.pending(limit=2)
        else:
            pending = await reader.pending(limit=2, include_admin=include_admin)

        assert [notification.message for notification in pending] == expected
        assert len(await reader.pending()) == 5
