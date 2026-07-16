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
import atr.get.committees as committees
import atr.log as log
import atr.models.sql as sql
import atr.sessions as sessions


@pytest.fixture
async def sqlite_global_db() -> AsyncIterator[None]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)

    db._global_atr_sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(
        bind=engine, class_=db.Session, expire_on_commit=False
    )
    yield
    await engine.dispose()
    db._global_atr_sessionmaker = None


async def _seed_committee_with_projects() -> None:
    async with db.session() as data:
        data.add(sql.Committee(key="example", name="Example", is_podling=False))
        data.add(sql.Project(key="example", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(sql.Project(key="example-lib", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(sql.Project(key="example-widget", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(
            sql.Release(
                key="example-lib-1.0.0",
                phase=sql.ReleasePhase.RELEASE,
                project_key="example-lib",
                version="1.0.0",
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
                released=datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
            )
        )
        await data.commit()


@pytest.mark.asyncio
async def test_view_sorts_projects_after_session_close(sqlite_global_db, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed_committee_with_projects()

    render = mock.AsyncMock(return_value="")
    monkeypatch.setattr(sessions, "read", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(log, "performance", mock.Mock())
    monkeypatch.setattr(committees.template, "render", render)
    monkeypatch.setattr(committees.form, "render", mock.AsyncMock(return_value=""))
    monkeypatch.setattr(committees.util, "as_url", lambda _endpoint, **_kwargs: "/")

    await committees.view(name="example")

    assert render.await_args is not None
    assert render.await_args.args == ("committee-view.html",)
    projects = render.await_args.kwargs["projects"]
    assert [str(p.key) for p in projects] == ["example", "example-lib", "example-widget"]
    assert [p.display_name for p in projects] == ["example", "example-lib", "example-widget"]
