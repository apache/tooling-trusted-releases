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
from typing import Final

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql

TARGET_URL: Final[str] = "https://dist.apache.org/repos/dist/atr/project"
OTHER_URL: Final[str] = "https://dist.apache.org/repos/dist/atr/other"
PROJECT: Final[safe.ProjectKey] = safe.ProjectKey("project")
VERSION: Final[safe.VersionKey] = safe.VersionKey("1.0.0")
REVISION: Final[safe.RevisionNumber] = safe.RevisionNumber("00001")


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


async def test_completed_publish_for_revision_returns_latest_valid_result(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        data.add(
            _publish_task(
                target_url=TARGET_URL,
                status=sql.TaskStatus.COMPLETED,
                added=datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
            )
        )
        malformed = _publish_task(
            target_url=OTHER_URL,
            status=sql.TaskStatus.COMPLETED,
            added=datetime.datetime(2026, 5, 2, tzinfo=datetime.UTC),
        )
        malformed.result = {
            "kind": "svn_publish",
            "target_url": OTHER_URL,
            "message": "missing revision",
        }
        data.add(malformed)
        await data.commit()

        result = await interaction.release_completed_svn_publish_task_for_revision(
            PROJECT, VERSION, REVISION, caller_data=data
        )

        assert result is not None
        assert result.task_args["target_url"] == TARGET_URL


async def test_in_flight_publish_returns_newest_queued_or_active_task(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        data.add(
            _publish_task(
                target_url=OTHER_URL,
                status=sql.TaskStatus.ACTIVE,
                added=datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
            )
        )
        data.add(
            _publish_task(
                target_url=TARGET_URL,
                status=sql.TaskStatus.QUEUED,
                added=datetime.datetime(2026, 5, 2, tzinfo=datetime.UTC),
            )
        )
        data.add(
            _publish_task(
                target_url=TARGET_URL,
                status=sql.TaskStatus.FAILED,
                added=datetime.datetime(2026, 5, 3, tzinfo=datetime.UTC),
            )
        )
        await data.commit()

        latest = await interaction.release_in_flight_svn_publish_task(PROJECT, VERSION, REVISION, caller_data=data)
        other_target = await interaction.release_in_flight_svn_publish_task(
            PROJECT, VERSION, REVISION, OTHER_URL, caller_data=data
        )

        assert latest is not None
        assert latest.task_args["target_url"] == TARGET_URL
        assert other_target is not None
        assert other_target.status == sql.TaskStatus.ACTIVE


async def test_latest_failed_publish_returns_failed_task(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        data.add(
            _publish_task(
                target_url=TARGET_URL,
                status=sql.TaskStatus.QUEUED,
                added=datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
            )
        )
        data.add(
            _publish_task(
                target_url=TARGET_URL,
                status=sql.TaskStatus.FAILED,
                added=datetime.datetime(2026, 5, 2, tzinfo=datetime.UTC),
            )
        )
        await data.commit()

        result = await interaction.release_latest_failed_svn_publish_task(
            PROJECT, VERSION, REVISION, TARGET_URL, caller_data=data
        )

        assert result is not None
        assert result.status == sql.TaskStatus.FAILED


def _publish_task(
    *,
    target_url: str,
    status: sql.TaskStatus,
    added: datetime.datetime,
    revision_number: str = "00001",
    project_key: str = "project",
    version_key: str = "1.0.0",
) -> sql.Task:
    completed: datetime.datetime | None = None
    error: str | None = None
    pid: int | None = None
    result: dict[str, object] | None = None
    started: datetime.datetime | None = None
    if status == sql.TaskStatus.ACTIVE:
        started = added
        pid = 1234
    elif status == sql.TaskStatus.COMPLETED:
        completed = added
        result = {
            "kind": "svn_publish",
            "svn_revision": 42,
            "target_url": target_url,
            "message": "ok",
        }
    elif status == sql.TaskStatus.FAILED:
        completed = added
        error = "publish failed"
    return sql.Task(
        status=status,
        task_type=sql.TaskType.SVN_PUBLISH,
        task_args={
            "asf_uid": "alice",
            "project_key": project_key,
            "version_key": version_key,
            "revision_number": revision_number,
            "download_path_suffix": None,
            "target_url": target_url,
        },
        asf_uid="alice",
        project_key=project_key,
        version_key=version_key,
        revision_number=revision_number,
        added=added,
        started=started,
        pid=pid,
        completed=completed,
        result=result,
        error=error,
    )
