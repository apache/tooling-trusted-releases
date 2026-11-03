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
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.writers.release as release

_RELEASED = datetime.datetime(2026, 11, 3, 12, 0, 0, tzinfo=datetime.UTC)


@pytest.fixture
async def sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _writer(data: db.Session) -> tuple[release.FoundationAdmin, mock.MagicMock]:
    write_as = mock.MagicMock()
    writer = object.__new__(release.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__asf_uid = "tester"
    writer._FoundationAdmin__write_as = write_as
    return writer, write_as


async def _seed(data: db.Session, *, managed: bool | None) -> None:
    data.add(sql.Committee(key="alpha", name="Alpha"))
    data.add(sql.Project(key="alpha-one", name="Apache Alpha One", committee_key="alpha"))
    await data.commit()
    if managed is not None:
        data.add(
            sql.Artifact(
                project_key="alpha-one",
                version="0.9.0",
                artifact_path="apache-alpha-one-0.9.0.tar.gz",
                managed=managed,
            )
        )
        await data.commit()


async def _queued_tasks(data: db.Session) -> list[sql.Task]:
    return list((await data.execute(sqlmodel.select(sql.Task))).scalars().all())


async def test_warns_when_the_project_has_a_managed_release(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed(data, managed=True)
        writer, write_as = _writer(data)

        error = await writer.warn_unmanaged(safe.ProjectKey("alpha-one"), safe.VersionKey("1.0.0"), _RELEASED)

        assert error is None
        tasks = await _queued_tasks(data)
        assert len(tasks) == 1
        assert tasks[0].task_type == sql.TaskType.MESSAGE_SEND
        assert tasks[0].project_key == "alpha-one"
        assert tasks[0].version_key == "1.0.0"
        assert tasks[0].task_args["subject"] == "Unmanaged release: Alpha One 1.0.0"
        write_as.append_to_audit_log.assert_called_once_with(
            asf_uid="tester", project_key="alpha-one", version="1.0.0", warned="unmanaged release"
        )


async def test_stays_quiet_when_the_project_has_only_catalogued_releases(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed(data, managed=False)
        writer, write_as = _writer(data)

        error = await writer.warn_unmanaged(safe.ProjectKey("alpha-one"), safe.VersionKey("1.0.0"), _RELEASED)

        assert error is None
        assert await _queued_tasks(data) == []
        write_as.append_to_audit_log.assert_not_called()


async def test_stays_quiet_when_the_project_has_no_releases_at_all(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed(data, managed=None)
        writer, _ = _writer(data)

        error = await writer.warn_unmanaged(safe.ProjectKey("alpha-one"), safe.VersionKey("1.0.0"), _RELEASED)

        assert error is None
        assert await _queued_tasks(data) == []


async def test_reports_an_unknown_project(sessionmaker) -> None:
    async with sessionmaker() as data:
        writer, _ = _writer(data)

        error = await writer.warn_unmanaged(safe.ProjectKey("ghost"), safe.VersionKey("1.0.0"), _RELEASED)

        assert error == "Project ghost not found"
        assert await _queued_tasks(data) == []
