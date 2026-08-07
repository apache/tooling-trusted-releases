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
import atr.storage as storage
import atr.storage.writers.catalogue as catalogue


@pytest.fixture
async def sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )

    # The app enables foreign keys per connection; the test engine must opt in too,
    # or the re-point ordering these tests exist to prove is never enforced
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


def _writer(data: db.Session) -> catalogue.FoundationAdmin:
    writer = object.__new__(catalogue.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__asf_uid = "tester"
    writer._FoundationAdmin__write_as = mock.MagicMock()
    return writer


async def _seed_project(data: db.Session, committee: str, key: str, is_podling: bool = False) -> None:
    data.add(sql.Committee(key=committee, name=committee.title(), is_podling=is_podling))
    data.add(sql.Committee(key="dest", name="Dest", is_podling=False))
    data.add(sql.Project(key=key, name=f"Apache {key}", committee_key=committee))
    await data.commit()


@pytest.mark.asyncio
async def test_move_project_reassigns_committee(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _writer(data).move_project(safe.ProjectKey("alpha-one"), safe.CommitteeKey("dest"))

        moved = await data.get(sql.Project, "alpha-one")
        assert moved is not None
        assert moved.committee_key == "dest"


@pytest.mark.asyncio
async def test_move_project_rejects_missing_destination(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        with pytest.raises(storage.AccessError, match="destination committee"):
            await _writer(data).move_project(safe.ProjectKey("alpha-one"), safe.CommitteeKey("ghost"))


async def _seed_release_with_children(data: db.Session, project_key: str, version: str) -> None:
    release_key = f"{project_key}-{version}"
    cycle_key = f"{project_key}-default"
    # The project's default cycle already exists: creating a Project fires an after_insert
    # hook that populates "{key}-default". LifecycleEvent has no ORM relationship to its
    # release or cycle, so its insert can't be ordered within one flush; commit the release
    # first, the way the seed does
    data.add(
        sql.Release(
            key=release_key,
            project_key=project_key,
            cycle_key=cycle_key,
            version=version,
            phase=sql.ReleasePhase.RELEASE,
            created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        )
    )
    await data.commit()
    data.add(
        sql.Artifact(
            project_key=project_key,
            version=version,
            artifact_path=f"apache-{project_key}-{version}.tar.gz",
            release_key=release_key,
        )
    )
    data.add(
        sql.LifecycleEvent(
            project_key=project_key,
            cycle_key=cycle_key,
            version_key=release_key,
            event=sql.LifecycleEventType.RELEASE,
            effective=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        )
    )
    await data.commit()


@pytest.mark.asyncio
async def test_rename_repoints_every_dependent_row(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _seed_release_with_children(data, "alpha-one", "1.2.0")

        await _writer(data).rename_project(safe.ProjectKey("alpha-one"), safe.ProjectKey("alpha-core"), "Apache Core")

        assert await data.get(sql.Project, "alpha-one") is None
        renamed = await data.get(sql.Project, "alpha-core")
        assert renamed is not None
        assert renamed.name == "Apache Core"
        assert await data.get(sql.Release, "alpha-core-1.2.0") is not None
        assert await data.get(sql.Release, "alpha-one-1.2.0") is None
        artifact = await data.get(sql.Artifact, ("alpha-core", "1.2.0", "apache-alpha-one-1.2.0.tar.gz"))
        assert artifact is not None
        assert artifact.release_key == "alpha-core-1.2.0"
        cycle = await data.get(sql.ProjectCycle, "alpha-core-default")
        assert cycle is not None


@pytest.mark.asyncio
async def test_rename_rejects_existing_target_key(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        data.add(sql.Project(key="alpha-two", name="Apache Two", committee_key="alpha"))
        await data.commit()
        with pytest.raises(storage.AccessError, match="already exists"):
            await _writer(data).rename_project(safe.ProjectKey("alpha-one"), safe.ProjectKey("alpha-two"), None)


async def _seed_managed_project(data: db.Session, committee: str, key: str) -> None:
    # A project carrying a revision looks like one that went through the ATR release
    # workflow, which the catalogue-correction page refuses to touch
    await _seed_project(data, committee, key)
    await _seed_release_with_children(data, key, "1.0.0")
    data.add(
        sql.Revision(
            key=f"{key}-1.0.0 00001",
            release_key=f"{key}-1.0.0",
            asfuid="someone",
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        )
    )
    await data.commit()


@pytest.mark.asyncio
async def test_rename_refuses_a_project_with_live_workflow_data(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_managed_project(data, "alpha", "alpha-one")
        with pytest.raises(storage.AccessError, match="catalogued projects only"):
            await _writer(data).rename_project(safe.ProjectKey("alpha-one"), safe.ProjectKey("alpha-core"), None)


@pytest.mark.asyncio
async def test_move_refuses_a_project_with_live_workflow_data(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_managed_project(data, "alpha", "alpha-one")
        with pytest.raises(storage.AccessError, match="catalogued projects only"):
            await _writer(data).move_project(safe.ProjectKey("alpha-one"), safe.CommitteeKey("dest"))


@pytest.mark.asyncio
async def test_delete_refuses_a_project_with_live_workflow_data(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_managed_project(data, "alpha", "alpha-one")
        with pytest.raises(storage.AccessError, match="catalogued projects only"):
            await _writer(data).delete_project(safe.ProjectKey("alpha-one"))


@pytest.mark.asyncio
async def test_rehome_refuses_when_source_has_live_workflow_data(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_managed_project(data, "alpha", "alpha-src")
        data.add(sql.Project(key="alpha-dst", name="Apache Dst", committee_key="alpha"))
        await data.commit()
        with pytest.raises(storage.AccessError, match="catalogued projects only"):
            await _writer(data).rehome_project(safe.ProjectKey("alpha-src"), safe.ProjectKey("alpha-dst"))


@pytest.mark.asyncio
async def test_rehome_moves_contents_and_deletes_source(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-src")
        # Creating the project auto-populates its default cycle; no manual cycle needed
        data.add(sql.Project(key="alpha-dst", name="Apache Dst", committee_key="alpha"))
        await data.commit()
        await _seed_release_with_children(data, "alpha-src", "1.0.0")

        await _writer(data).rehome_project(safe.ProjectKey("alpha-src"), safe.ProjectKey("alpha-dst"))

        assert await data.get(sql.Project, "alpha-src") is None
        moved = await data.get(sql.Release, "alpha-dst-1.0.0")
        assert moved is not None
        assert moved.project_key == "alpha-dst"
        assert moved.cycle_key == "alpha-dst-default"
        assert await data.get(sql.Release, "alpha-src-1.0.0") is None

        # The artifact re-points to the target project and release, keeping its filename
        artifact = await data.get(sql.Artifact, ("alpha-dst", "1.0.0", "apache-alpha-src-1.0.0.tar.gz"))
        assert artifact is not None
        assert artifact.release_key == "alpha-dst-1.0.0"
        assert await data.get(sql.Artifact, ("alpha-src", "1.0.0", "apache-alpha-src-1.0.0.tar.gz")) is None

        events = (
            (
                await data.execute(
                    sqlmodel.select(sql.LifecycleEvent).where(
                        sql.validate_instrumented_attribute(sql.LifecycleEvent.version_key) == "alpha-dst-1.0.0"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].cycle_key == moved.cycle_key


@pytest.mark.asyncio
async def test_rehome_halts_on_version_collision(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-src")
        data.add(sql.Project(key="alpha-dst", name="Apache Dst", committee_key="alpha"))
        await data.commit()
        await _seed_release_with_children(data, "alpha-src", "1.0.0")
        await _seed_release_with_children(data, "alpha-dst", "1.0.0")

        with pytest.raises(storage.AccessError, match="collision"):
            await _writer(data).rehome_project(safe.ProjectKey("alpha-src"), safe.ProjectKey("alpha-dst"))

        # Nothing moved: the source release still exists
        assert await data.get(sql.Release, "alpha-src-1.0.0") is not None


@pytest.mark.asyncio
async def test_delete_project_removes_project_and_contents(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        data.add(sql.Project(key="alpha-two", name="Apache Two", committee_key="alpha"))
        await data.commit()
        await _seed_release_with_children(data, "alpha-one", "2.0.0")

        await _writer(data).delete_project(safe.ProjectKey("alpha-one"))

        assert await data.get(sql.Project, "alpha-one") is None
        assert await data.get(sql.Release, "alpha-one-2.0.0") is None
        assert await data.get(sql.ProjectCycle, "alpha-one-default") is None


@pytest.mark.asyncio
async def test_archive_project_marks_it_retired(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")

        await _writer(data).archive_project(safe.ProjectKey("alpha-one"))

        project = await data.get(sql.Project, "alpha-one")
        assert project is not None
        assert project.status == sql.ProjectStatus.RETIRED


@pytest.mark.asyncio
async def test_restore_project_returns_it_to_active(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _writer(data).archive_project(safe.ProjectKey("alpha-one"))

        await _writer(data).restore_project(safe.ProjectKey("alpha-one"))

        project = await data.get(sql.Project, "alpha-one")
        assert project is not None
        assert project.status == sql.ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_archive_rejects_a_project_already_in_that_status(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _writer(data).archive_project(safe.ProjectKey("alpha-one"))

        with pytest.raises(storage.AccessError, match="already"):
            await _writer(data).archive_project(safe.ProjectKey("alpha-one"))


@pytest.mark.asyncio
async def test_restore_rejects_a_project_already_in_that_status(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")

        with pytest.raises(storage.AccessError, match="already"):
            await _writer(data).restore_project(safe.ProjectKey("alpha-one"))


@pytest.mark.asyncio
async def test_archive_release_marks_it_archived(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _seed_release_with_children(data, "alpha-one", "1.0.0")

        await _writer(data).archive_release(safe.ReleaseKey("alpha-one-1.0.0"))

        release = await data.get(sql.Release, "alpha-one-1.0.0")
        assert release is not None
        assert release.is_archived is True


@pytest.mark.asyncio
async def test_restore_release_returns_it_to_current(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _seed_release_with_children(data, "alpha-one", "1.0.0")
        await _writer(data).archive_release(safe.ReleaseKey("alpha-one-1.0.0"))

        await _writer(data).restore_release(safe.ReleaseKey("alpha-one-1.0.0"))

        release = await data.get(sql.Release, "alpha-one-1.0.0")
        assert release is not None
        assert release.is_archived is False


@pytest.mark.asyncio
async def test_archive_release_rejects_one_already_in_that_status(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _seed_release_with_children(data, "alpha-one", "1.0.0")
        await _writer(data).archive_release(safe.ReleaseKey("alpha-one-1.0.0"))

        with pytest.raises(storage.AccessError, match="already"):
            await _writer(data).archive_release(safe.ReleaseKey("alpha-one-1.0.0"))


@pytest.mark.asyncio
async def test_restore_release_rejects_one_already_in_that_status(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        await _seed_release_with_children(data, "alpha-one", "1.0.0")

        with pytest.raises(storage.AccessError, match="already"):
            await _writer(data).restore_release(safe.ReleaseKey("alpha-one-1.0.0"))


@pytest.mark.asyncio
async def test_delete_project_orphans_a_child_super_pointer_rather_than_deleting_it(sessionmaker) -> None:
    # A sub-project points at the deleted project via super_project_key; the child must
    # survive with its pointer cleared, not be swept away by the delete
    async with sessionmaker() as data:
        await _seed_project(data, "alpha", "alpha-one")
        data.add(
            sql.Project(key="alpha-one-sub", name="Apache Sub", committee_key="alpha", super_project_key="alpha-one")
        )
        await data.commit()

        await _writer(data).delete_project(safe.ProjectKey("alpha-one"))

        child = await data.get(sql.Project, "alpha-one-sub")
        assert child is not None
        assert child.super_project_key is None
