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
import pathlib
import unittest.mock as mock
from collections.abc import AsyncIterator

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.validate as validate


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


def release(phase: sql.ReleasePhase) -> mock.MagicMock:
    result = mock.MagicMock()
    result.phase = phase
    result.key = "proj-1.0"
    result.project_key = "proj"
    result.version = "1.0"
    return result


def test_release_on_disk_flags_lingering_unfinished_directory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "unfinished" / "proj" / "1.0").mkdir(parents=True)
    monkeypatch.setattr(validate.paths, "get_unfinished_dir", lambda: safe.StatePath(tmp_path / "unfinished"))

    divergences = list(validate.release_on_disk(release(sql.ReleasePhase.RELEASE)))

    assert len(divergences) == 1


def test_release_on_disk_flags_missing_draft_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        validate.paths, "release_directory", lambda _release: safe.StatePath(tmp_path / "unfinished" / "proj" / "1.0")
    )

    divergences = list(validate.release_on_disk(release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)))

    assert len(divergences) == 1


def test_release_on_disk_passes_released_without_directories(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate.paths, "get_unfinished_dir", lambda: safe.StatePath(tmp_path / "unfinished"))

    divergences = list(validate.release_on_disk(release(sql.ReleasePhase.RELEASE)))

    assert divergences == []


@pytest.mark.parametrize(
    ("status", "task_type", "task_version", "expected_divergences"),
    [
        (sql.TaskStatus.QUEUED, sql.TaskType.RELEASE_FINALISE, "1.0", 0),
        (sql.TaskStatus.ACTIVE, sql.TaskType.RELEASE_FINALISE, "1.0", 0),
        (sql.TaskStatus.ACTIVE, sql.TaskType.RELEASE_FINALISE, "2.0", 1),
        (sql.TaskStatus.ACTIVE, sql.TaskType.SVN_PUBLISH, "1.0", 1),
        (sql.TaskStatus.COMPLETED, sql.TaskType.RELEASE_FINALISE, "1.0", 1),
        (sql.TaskStatus.FAILED, sql.TaskType.RELEASE_FINALISE, "1.0", 1),
        (sql.TaskStatus.BROKEN, sql.TaskType.RELEASE_FINALISE, "1.0", 1),
    ],
)
async def test_release_on_disk_accounts_for_finalisation_task_status(
    sqlite_sessionmaker,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    status: sql.TaskStatus,
    task_type: sql.TaskType,
    task_version: str,
    expected_divergences: int,
) -> None:
    unfinished = tmp_path / "unfinished"
    (unfinished / "proj" / "1.0").mkdir(parents=True)
    monkeypatch.setattr(validate.paths, "get_unfinished_dir", lambda: safe.StatePath(unfinished))
    created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    async with sqlite_sessionmaker() as data:
        data.add(sql.Committee(key="proj", name="Proj"))
        data.add(sql.Project(key="proj", name="Apache Proj", committee_key="proj", created=created))
        await data.commit()
        data.add(
            sql.Release(
                phase=sql.ReleasePhase.RELEASE,
                created=created,
                project_key="proj",
                version="1.0",
            )
        )
        data.add(
            sql.Task(
                status=status,
                task_type=task_type,
                task_args={},
                asf_uid="user",
                started=created if status == sql.TaskStatus.ACTIVE else None,
                pid=1 if status == sql.TaskStatus.ACTIVE else None,
                completed=created
                if status in (sql.TaskStatus.COMPLETED, sql.TaskStatus.FAILED, sql.TaskStatus.BROKEN)
                else None,
                result=results.ReleaseFinalise(kind="release_finalise", audit_events=1, message="Finalised")
                if status == sql.TaskStatus.COMPLETED
                else None,
                error="Failed" if status in (sql.TaskStatus.FAILED, sql.TaskStatus.BROKEN) else None,
                project_key="proj",
                version_key=task_version,
                revision_number="00001",
            )
        )
        await data.commit()

        divergences = [
            divergence async for divergence in validate.everything(data) if divergence.validator == "release_on_disk"
        ]

    assert len(divergences) == expected_divergences
