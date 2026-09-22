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
import sqlalchemy.event
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.writers.release as release


@pytest.fixture
async def sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )

    # The app turns foreign keys on per connection; the test engine has to opt in too,
    # or the lifecycle-event foreign key this test leans on is never enforced
    @sqlalchemy.event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _writer(data: db.Session) -> release.FoundationAdmin:
    writer = object.__new__(release.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__asf_uid = "tester"
    writer._FoundationAdmin__write_as = mock.MagicMock()
    return writer


async def _seed_project(data: db.Session, committee: str, key: str) -> None:
    # Creating a Project fires an after_insert hook that populates its "{key}-default" cycle,
    # so catalogue_release finds a cycle to hang the release on
    data.add(sql.Committee(key=committee, name=committee.title(), is_podling=False))
    data.add(sql.Project(key=key, name=f"Apache {key}", committee_key=committee))
    await data.commit()


def _artifact(name: str) -> release.ArtifactInput:
    return release.ArtifactInput(artifact_path=name, classification="source", download_path_suffix="alpha/one/1.0.0")


async def _artifact_names(data: db.Session, release_key: str) -> list[str]:
    via = sql.validate_instrumented_attribute
    result = await data.execute(sqlmodel.select(sql.Artifact).where(via(sql.Artifact.release_key) == release_key))
    return sorted(row.artifact_path for row in result.scalars().all())


async def _events(data: db.Session, release_key: str) -> list[sql.LifecycleEvent]:
    via = sql.validate_instrumented_attribute
    result = await data.execute(
        sqlmodel.select(sql.LifecycleEvent).where(via(sql.LifecycleEvent.version_key) == release_key)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_recatalogue_restores_an_archived_version_and_swaps_its_files(sessionmaker) -> None:
    project_key = safe.ProjectKey("alpha-one")
    version = safe.VersionKey("1.0.0")
    release_key = "alpha-one-1.0.0"
    first_seen = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    seen_again = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)

    original = [_artifact("apache-one-1.0.0.tar.gz")]
    recut = [_artifact("apache-one-1.0.0-bin.tar.gz")]

    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        writer = _writer(data)

        assert await writer.catalogue_release(project_key, version, first_seen, original) is None
        assert await writer.archive(project_key, version) is None

        archived = await data.get(sql.Release, release_key)
        assert (archived is not None) and (archived.is_archived is True)

        # The same version reappears in dist, carrying a fresh file set
        assert await writer.catalogue_release(project_key, version, seen_again, recut) is None

        data.expire_all()
        restored = await data.get(sql.Release, release_key)
        assert restored is not None
        assert restored.is_archived is False
        assert restored.archived is None
        assert restored.archive_source is None

        # Its files are the ones just seen, not the archived set
        assert await _artifact_names(data, release_key) == ["apache-one-1.0.0-bin.tar.gz"]

        # The archive event is retracted, not deleted: it stays and a WITHDRAW points back at it
        events = await _events(data, release_key)
        archives = [event for event in events if event.event is sql.LifecycleEventType.ARCHIVE]
        withdraws = [event for event in events if event.event is sql.LifecycleEventType.WITHDRAW]
        assert len(archives) == 1
        assert len(withdraws) == 1
        assert withdraws[0].target_event_id == archives[0].id


@pytest.mark.asyncio
async def test_recatalogue_leaves_a_current_release_untouched(sessionmaker) -> None:
    project_key = safe.ProjectKey("alpha-one")
    version = safe.VersionKey("1.0.0")
    release_key = "alpha-one-1.0.0"
    when = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)

    original = [_artifact("apache-one-1.0.0.tar.gz")]
    recut = [_artifact("apache-one-1.0.0-bin.tar.gz")]

    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        writer = _writer(data)

        assert await writer.catalogue_release(project_key, version, when, original) is None
        # Re-seeing a version we still hold as current is a no-op, files and all
        assert await writer.catalogue_release(project_key, version, when, recut) is None

        data.expire_all()
        assert await _artifact_names(data, release_key) == ["apache-one-1.0.0.tar.gz"]
        events = await _events(data, release_key)
        assert [event.event for event in events] == [sql.LifecycleEventType.RELEASE]
