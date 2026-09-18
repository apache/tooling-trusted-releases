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
import pathlib
import unittest.mock as mock

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.config as config
import atr.db as db
import atr.models.sql as sql
import atr.validate as validate


def release(phase: sql.ReleasePhase, project_key: str, version: str, expedited: bool = False) -> mock.MagicMock:
    result = mock.MagicMock()
    result.phase = phase
    result.project_key = project_key
    result.version = version
    # Mirror the real is_embargoed property, so path resolution picks the right storage root.
    result.is_embargoed = expedited and (phase != sql.ReleasePhase.RELEASE)
    return result


@pytest.fixture
async def sqlite_sessionmaker() -> abc.AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    yield sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize(
    "status", [sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE, sql.TaskStatus.COMPLETED, sql.TaskStatus.FAILED]
)
async def test_consistency(
    sqlite_sessionmaker,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    status: sql.TaskStatus,
) -> None:
    for root in ("finished", "embargoed", "unfinished"):
        monkeypatch.setattr(config.get(), f"{root.upper()}_STORAGE_DIR", str(tmp_path / root))
        (tmp_path / root).mkdir()
    paired = ["embargoed/proj/private", "finished/proj/old", "unfinished/proj/draft"]
    finalising = ["unfinished/proj/finalising", "unfinished/proj/finalising.deleting-"]
    for directory in paired + finalising + ["unfinished/proj/orphan"]:
        (tmp_path / directory).mkdir(parents=True)
    created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    async with sqlite_sessionmaker() as data:
        data.add(sql.Committee(key="proj", name="Proj"))
        data.add(sql.Project(key="proj", name="Apache Proj", committee_key="proj", created=created))
        await data.commit()
        for version in ("old", "newer", "finalising"):
            data.add(sql.Release(project_key="proj", version=version, phase=sql.ReleasePhase.RELEASE, created=created))
        for version in ("draft", "missing", "private"):
            data.add(
                sql.Release(
                    project_key="proj",
                    version=version,
                    phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                    expedited=version == "private",
                    created=created,
                )
            )
        data.add(
            sql.Task(
                status=status,
                task_type=sql.TaskType.RELEASE_FINALISE,
                task_args={},
                asf_uid="user",
                project_key="proj",
                version_key="finalising",
                started=created if status == sql.TaskStatus.ACTIVE else None,
                pid=1 if status == sql.TaskStatus.ACTIVE else None,
                completed=created if status in (sql.TaskStatus.COMPLETED, sql.TaskStatus.FAILED) else None,
                error="Failed" if status == sql.TaskStatus.FAILED else None,
            )
        )
        await data.commit()

        report = await validate.consistency(data)

    filesystem_only = ["unfinished/proj/orphan"]
    if status in (sql.TaskStatus.COMPLETED, sql.TaskStatus.FAILED):
        filesystem_only += finalising
    assert report == validate.Consistency(
        [str(tmp_path / "unfinished/proj/missing")],
        sorted(str(tmp_path / directory) for directory in filesystem_only),
        [str(tmp_path / directory) for directory in paired],
    )


async def test_consistency_database_dirs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "FINISHED_STORAGE_DIR", str(tmp_path / "finished"), raising=False)
    monkeypatch.setattr(config.get(), "UNFINISHED_STORAGE_DIR", str(tmp_path / "unfinished"), raising=False)
    (tmp_path / "finished" / "old" / "1.0").mkdir(parents=True)
    old_world = release(sql.ReleasePhase.RELEASE, "old", "1.0")
    new_world = release(sql.ReleasePhase.RELEASE, "newer", "2.0")
    draft = release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, "draft", "3.0")

    dirs = await validate._consistency_database_dirs([old_world, new_world, draft])

    assert dirs == [
        str(tmp_path / "finished" / "old" / "1.0"),
        str(tmp_path / "unfinished" / "draft" / "3.0"),
    ]
