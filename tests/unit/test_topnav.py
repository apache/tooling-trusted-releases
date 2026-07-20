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
import sqlalchemy.event
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.sql as sql
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
async def sqlite_global_db() -> AsyncIterator[sqlalchemy.ext.asyncio.AsyncEngine]:
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
    yield engine
    await engine.dispose()
    db._global_atr_sessionmaker = None


@pytest.mark.asyncio
async def test_user_topnav_embargo_and_grouping(
    sqlite_global_db: sqlalchemy.ext.asyncio.AsyncEngine, mock_app: MockApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset()
    async with db.session() as data:
        data.add(sql.Committee(key="example", name="Example", committee_members=["alice"], committers=["bob"]))
        data.add(sql.Project(key="example", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(sql.Project(key="example-two", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(
            sql.Release(
                key="example-1.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                project_key="example",
                version="1.0.0",
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        data.add(
            sql.Release(
                key="example-2.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                project_key="example",
                version="2.0.0",
                created=datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC),
                expedited=True,
            )
        )
        data.add(
            sql.Release(
                key="example-0.9.0",
                phase=sql.ReleasePhase.RELEASE,
                project_key="example",
                version="0.9.0",
                created=datetime.datetime(2025, 12, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()

    unfinished, projects = await interaction.user_topnav("alice", False)
    assert projects == [("example", "example"), ("example-two", "example-two")]
    assert [(name, key) for name, key, _releases in unfinished] == [("example", "example")]
    releases = unfinished[0][2]
    assert [r.version for r in releases] == ["2.0.0", "1.0.0"]
    assert releases[0].project.key == "example"

    unfinished, _projects = await interaction.user_topnav("bob", False)
    assert [r.version for r in unfinished[0][2]] == ["1.0.0"]

    unfinished, _projects = await interaction.user_topnav("bob", True)
    assert [r.version for r in unfinished[0][2]] == ["2.0.0", "1.0.0"]


@pytest.mark.asyncio
async def test_user_topnav_statement_count_constant(
    sqlite_global_db: sqlalchemy.ext.asyncio.AsyncEngine, mock_app: MockApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset()
    statements: list[str] = []
    sqlalchemy.event.listen(
        sqlite_global_db.sync_engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, parameters, context, executemany: statements.append(statement),
    )

    async with db.session() as data:
        data.add(sql.Committee(key="example", name="Example", committee_members=["alice"]))
        data.add(sql.Project(key="example", committee_key="example", status=sql.ProjectStatus.ACTIVE))
        data.add(
            sql.Release(
                key="example-1.0.0",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                project_key="example",
                version="1.0.0",
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()

    statements.clear()
    await interaction.user_topnav("alice", False)
    baseline = len(statements)

    async with db.session() as data:
        for number in (2, 3):
            data.add(sql.Project(key=f"example-{number}", committee_key="example", status=sql.ProjectStatus.ACTIVE))
            data.add(
                sql.Release(
                    key=f"example-{number}-1.0.0",
                    phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                    project_key=f"example-{number}",
                    version="1.0.0",
                    created=datetime.datetime(2026, 1, number, tzinfo=datetime.UTC),
                )
            )
        await data.commit()

    statements.clear()
    unfinished, projects = await interaction.user_topnav("alice", False)
    assert len(projects) == 3
    assert len(unfinished) == 3
    assert len(statements) == baseline
