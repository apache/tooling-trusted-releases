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

import pathlib
import unittest.mock as mock
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.construct as construct
import atr.db as db
import atr.models.sql as sql
import atr.selftest as selftest


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


async def test_report_emails_when_the_failures_change_and_when_they_clear(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(db, "session", sqlite_sessionmaker)
    monkeypatch.setattr(selftest.cache, "admins_get_async", mock.AsyncMock(return_value=frozenset()))
    failures = {"RAT": "Apache RAT process failed with code 127"}

    await selftest.report({})
    await selftest.report(failures)
    await selftest.report(failures)
    await selftest.report({})

    async with sqlite_sessionmaker() as data:
        emails = (await data.execute(sqlmodel.select(sql.Task).order_by(sql.Task.id))).scalars().all()
        assert [email.task_args["email_to"] for email in emails] == [construct._SECURITY_LIST_ADDRESS] * 2
        assert "failed" in emails[0].task_args["subject"]
        assert "passing again" in emails[1].task_args["subject"]
        assert (await data.ns_text_get(selftest._NAMESPACE, selftest._KEY)) is None


async def test_run_collects_failing_and_crashing_probes(monkeypatch) -> None:
    monkeypatch.setattr(selftest, "_sandbox", lambda _scratch: None)
    monkeypatch.setattr(selftest, "_rsync", _crash)
    monkeypatch.setattr(selftest, "_rat", lambda _scratch: "Apache RAT process failed with code 127")

    assert await selftest.run() == {"rsync": "rsync", "RAT": "Apache RAT process failed with code 127"}


def test_sandbox_probe_needs_landlock_only_in_production(monkeypatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(selftest.sandbox, "landlock_setpriv", lambda: None)
    production = mock.Mock(return_value=False)
    monkeypatch.setattr(selftest.config, "is_production_mode", production)

    assert selftest._sandbox(tmp_path) is None
    production.return_value = True
    assert selftest._sandbox(tmp_path) is not None


def _crash(_scratch: pathlib.Path) -> str | None:
    raise FileNotFoundError("rsync")
