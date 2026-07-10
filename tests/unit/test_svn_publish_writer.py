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
from types import SimpleNamespace
from typing import Final

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.config as config
import atr.db as db
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.storage.writers.release as release_writer

INTERNAL_PUBLISH_URL: Final[str] = "https://internal.example.invalid/repos/dist/atr"


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


async def test_publish_to_svn_allows_retry_after_failed_task(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        data.add(_publish_task(status=sql.TaskStatus.FAILED))
        await data.commit()
        writer = _release_writer(data)

        task = await writer.publish_to_svn(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            None,
        )

        assert task.status == sql.TaskStatus.QUEUED


async def test_publish_to_svn_allows_retry_after_malformed_completed_task(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        task = _publish_task(status=sql.TaskStatus.COMPLETED)
        task.result = {
            "kind": "svn_publish",
            "message": "missing revision",
        }
        data.add(task)
        await data.commit()
        writer = _release_writer(data)

        retry = await writer.publish_to_svn(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            None,
        )

        assert retry.status == sql.TaskStatus.QUEUED


async def test_publish_to_svn_enqueues_task(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        writer = _release_writer(data)

        task = await writer.publish_to_svn(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            safe.RelPath("project-1.0.0"),
        )

        assert task.task_type == sql.TaskType.SVN_PUBLISH
        assert task.asf_uid == "alice"
        assert task.revision_number == "00001"
        assert "target_url" not in task.task_args


async def test_publish_to_svn_execute_maps_existing_svn_path(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    publish_release = mock.AsyncMock(
        side_effect=release_writer.svn.CommandExecutionError(1, "svn: E160020: path '/project/file' already exists")
    )
    monkeypatch.setattr(release_writer.svn, "publish_release", publish_release)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        writer = _release_writer(data)

        with pytest.raises(datatypes.FailedError, match="Release file already exists in SVN"):
            await writer.publish_to_svn_execute(
                args.SvnPublish(
                    asf_uid="alice",
                    project_key="project",
                    version_key="1.0.0",
                    revision_number="00001",
                )
            )


async def test_publish_to_svn_execute_returns_result(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    publish_release = mock.AsyncMock(return_value=12345)
    monkeypatch.setattr(release_writer.svn, "publish_release", publish_release)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        writer = _release_writer(data)

        result = await writer.publish_to_svn_execute(
            args.SvnPublish(
                asf_uid="alice",
                project_key="project",
                version_key="1.0.0",
                revision_number="00001",
                download_path_suffix="project-1.0.0",
            )
        )

        assert isinstance(result, results.SvnPublish)
        assert result.svn_revision == 12345
        assert "internal.example.invalid" not in result.message
        publish_release.assert_awaited_once()
        assert publish_release.await_args.args[1] == f"{INTERNAL_PUBLISH_URL}/project/project-1.0.0"


async def test_publish_to_svn_rejects_duplicate_completed_task(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        data.add(_publish_task(status=sql.TaskStatus.COMPLETED))
        await data.commit()
        writer = _release_writer(data)

        with pytest.raises(storage.AccessError, match="already completed"):
            await writer.publish_to_svn(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                safe.RevisionNumber("00001"),
                None,
            )


async def test_publish_to_svn_rejects_duplicate_completed_task_after_target_drift(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config.get(),
        "SVN_PUBLISH_URL",
        "https://internal.example.invalid/repos/dist/release",
        raising=False,
    )
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        data.add(_publish_task(status=sql.TaskStatus.COMPLETED))
        await data.commit()
        writer = _release_writer(data)

        with pytest.raises(storage.AccessError, match="already completed"):
            await writer.publish_to_svn(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                safe.RevisionNumber("00001"),
                None,
            )


async def test_publish_to_svn_rejects_duplicate_queued_task(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        writer = _release_writer(data)
        await writer.publish_to_svn(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            None,
        )

        with pytest.raises(storage.AccessError, match="already queued or running"):
            await writer.publish_to_svn(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                safe.RevisionNumber("00001"),
                None,
            )


async def test_publish_to_svn_rejects_stale_revision(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    async with sqlite_sessionmaker() as data:
        await _seed_preview_release(data)
        writer = _release_writer(data)

        with pytest.raises(storage.AccessError, match="newer revision"):
            await writer.publish_to_svn(
                safe.ProjectKey("project"),
                safe.VersionKey("1.0.0"),
                safe.RevisionNumber("00009"),
                None,
            )


def _publish_task(*, status: sql.TaskStatus) -> sql.Task:
    completed: datetime.datetime | None = None
    error: str | None = None
    result: dict[str, object] | None = None
    if status == sql.TaskStatus.COMPLETED:
        completed = datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC)
        result = {
            "kind": "svn_publish",
            "svn_revision": 42,
            "message": "ok",
        }
    elif status == sql.TaskStatus.FAILED:
        completed = datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC)
        error = "publish failed"
    return sql.Task(
        status=status,
        task_type=sql.TaskType.SVN_PUBLISH,
        task_args={
            "asf_uid": "alice",
            "project_key": "project",
            "version_key": "1.0.0",
            "revision_number": "00001",
            "download_path_suffix": None,
        },
        asf_uid="alice",
        project_key="project",
        version_key="1.0.0",
        revision_number="00001",
        added=datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
        completed=completed,
        result=result,
        error=error,
    )


def _release_writer(data: db.Session) -> release_writer.ReleaseManager:
    writer = object.__new__(release_writer.ReleaseManager)
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__data = data
    writer._ReleaseManager__write_as = SimpleNamespace(append_to_audit_log=mock.Mock())
    writer._ReleaseManager__write = mock.MagicMock()
    return writer


async def _seed_preview_release(data: db.Session) -> None:
    committee = sql.Committee(
        key="project",
        name="Project",
        is_podling=False,
        committee_members=["alice"],
        committers=["alice"],
    )
    project = sql.Project(key="project", name="Project", committee=committee)
    release = sql.Release(
        key="project-1.0.0",
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        project=project,
        project_key=project.key,
        version="1.0.0",
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    revision = sql.Revision(
        key="project-1.0.0 00001",
        release=release,
        release_key=release.key,
        seq=1,
        number="00001",
        asfuid="alice",
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
    )
    data.add_all([committee, project, release, revision])
    await data.commit()
