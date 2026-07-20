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
import atr.get.root as root
import atr.log as log
import atr.models.sql as sql
import atr.sessions as sessions
import atr.user as user


class MockApp:
    def __init__(self):
        self.extensions: dict[str, object] = {}


class MockConfig:
    ADMIN_USERS_ADDITIONAL = ""


@pytest.fixture
def mock_app(monkeypatch: pytest.MonkeyPatch) -> MockApp:
    app = MockApp()
    monkeypatch.setattr("asfquart.APP", app)
    return app


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


@pytest.mark.asyncio
async def test_index_groups_releases_and_completed_flags(
    sqlite_global_db: None, mock_app: MockApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset()

    async with db.session() as data:
        data.add(sql.Committee(key="example", name="Example", committee_members=["alice"]))
        data.add(sql.Committee(key="other", name="Other", committers=["alice"]))
        data.add(sql.Project(key="alpha", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(sql.Project(key="beta", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(sql.Project(key="delta", committee_key="other", status=sql.ProjectStatus.ACTIVE))
        data.add(
            sql.Release(
                key="alpha-1.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                project_key="alpha",
                version="1.0.0",
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        data.add(
            sql.Release(
                key="alpha-0.9.0",
                phase=sql.ReleasePhase.RELEASE,
                project_key="alpha",
                version="0.9.0",
                created=datetime.datetime(2025, 12, 1, tzinfo=datetime.UTC),
            )
        )
        data.add(
            sql.Release(
                key="delta-1.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                project_key="delta",
                version="1.0.0",
                created=datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
                expedited=True,
            )
        )
        await data.commit()

    render = mock.AsyncMock(return_value="")
    monkeypatch.setattr(sessions, "read", mock.AsyncMock(return_value=sql.UserSession(uid="alice")))
    monkeypatch.setattr(log, "performance", mock.Mock())
    monkeypatch.setattr(root.template, "render", render)

    await root.index()

    assert render.await_args is not None
    assert render.await_args.args == ("index-committer.html",)
    all_projects = render.await_args.kwargs["all_projects"]
    assert [item["project"].key for item in all_projects] == ["alpha", "beta", "delta"]
    by_key = {item["project"].key: item for item in all_projects}
    assert [r.version for r in by_key["alpha"]["active_releases"]] == ["1.0.0"]
    assert by_key["alpha"]["active_releases"][0].project.key == "alpha"
    assert by_key["alpha"]["completed_releases"] is True
    assert by_key["beta"]["active_releases"] == []
    assert by_key["beta"]["completed_releases"] is False
    assert by_key["delta"]["active_releases"] == []
    assert by_key["delta"]["completed_releases"] is False
