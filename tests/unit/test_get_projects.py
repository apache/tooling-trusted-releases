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
import atr.get.projects as projects
import atr.log as log
import atr.models.sql as sql
import atr.sessions as sessions
import atr.user as user


class MockApp:
    def __init__(self):
        self.extensions: dict[str, object] = {}


class MockConfig:
    ADMIN_ONLY = False
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
async def test_projects_directory_action_forms(
    sqlite_global_db: None, mock_app: MockApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset()

    async with db.session() as data:
        data.add(sql.Committee(key="example", name="Example", committee_members=["alice"]))
        data.add(sql.Project(key="example-none", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(sql.Project(key="example-drafts", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(sql.Project(key="example-mixed", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(
            sql.Release(
                key="example-drafts-1.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                project_key="example-drafts",
                version="1.0.0",
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        data.add(
            sql.Release(
                key="example-mixed-1.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                project_key="example-mixed",
                version="1.0.0",
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        data.add(
            sql.Release(
                key="example-mixed-0.9.0",
                phase=sql.ReleasePhase.RELEASE,
                project_key="example-mixed",
                version="0.9.0",
                created=datetime.datetime(2025, 12, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()

    render = mock.AsyncMock(return_value="")
    form_render = mock.AsyncMock(return_value="")
    monkeypatch.setattr(sessions, "read", mock.AsyncMock(return_value=sql.UserSession(uid="alice")))
    monkeypatch.setattr(
        sessions.asfquart.session, "read", mock.AsyncMock(return_value=sessions.asfquart.session.ClientSession({}))
    )
    monkeypatch.setattr(log, "performance", mock.Mock())
    monkeypatch.setattr(projects.template, "render", render)
    monkeypatch.setattr(projects.form, "render", form_render)
    monkeypatch.setattr(projects.util, "as_url", lambda _endpoint, **_kwargs: "/")

    await projects.projects()

    assert render.await_args is not None
    assert render.await_args.args == ("projects.html",)
    action_forms = render.await_args.kwargs["action_forms"]
    assert set(action_forms) == {"example-none", "example-drafts"}
    labels = {c.kwargs["defaults"]["project_key"]: c.kwargs["submit_label"] for c in form_render.await_args_list}
    assert labels == {"example-none": "Request deletion", "example-drafts": "Request archival"}
