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

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.storage.writers.tokens as tokens


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


async def test_revoke_all_users_tokens_keeps_system_tokens(sqlite_sessionmaker) -> None:
    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
    async with sqlite_sessionmaker() as data:
        data.add(sql.PersonalAccessToken(asfuid="alice", created_by="alice", token_hash="a", expires=expires))
        data.add(sql.PersonalAccessToken(asfuid="bob", created_by="bob", token_hash="b", expires=expires))
        data.add(sql.PersonalAccessToken(created_by="admin", token_hash="s", expires=expires, is_system=True))
        await data.commit()

        write_as = mock.MagicMock()
        writer = object.__new__(tokens.FoundationAdmin)
        writer._FoundationAdmin__write_as = write_as
        writer._FoundationAdmin__data = data
        writer._FoundationAdmin__asf_uid = "admin"

        count = await writer.revoke_all_users_tokens()

        assert count == 2
        remaining = (await data.execute(sqlmodel.select(sql.PersonalAccessToken))).scalars().all()
        assert [token.token_hash for token in remaining] == ["s"]
        write_as.append_to_audit_log.assert_called_once_with(tokens_revoked=2)
