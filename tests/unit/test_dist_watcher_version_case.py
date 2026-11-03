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
import sqlalchemy.pool
import sqlmodel

import atr.classify as classify
import atr.db as db
import atr.models.sql as sql
import atr.svn.catalog as catalog
import atr.svn.dist_rules as dist_rules

# The dist watcher keys a commit's versions lowercased but the catalogue holds them as dist writes
# them (#1812), so every lookup has to match whatever the spelling


@pytest.fixture
async def sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )

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


@pytest.mark.asyncio
async def test_a_release_is_found_under_another_spelling(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed(data, "3.0.0-M3")
        found = await catalog._catalogued_release(data, "tomcat", "3.0.0-m3")
    assert (found is not None) and (found.version == "3.0.0-M3")


@pytest.mark.asyncio
async def test_a_twin_resolves_to_the_spelling_the_commit_writes(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed(data, "2.0.0-M1", "2.0.0-m1")
        upper = await catalog._catalogued_release(data, "tomcat", "2.0.0-M1")
        lower = await catalog._catalogued_release(data, "tomcat", "2.0.0-m1")
    assert (upper is not None) and (upper.version == "2.0.0-M1")
    assert (lower is not None) and (lower.version == "2.0.0-m1")


@pytest.mark.asyncio
async def test_deleting_a_version_dir_archives_the_release_under_its_catalogued_spelling(sessionmaker) -> None:
    # The removal is keyed 9.0.0.m1, but the catalogue holds 9.0.0.M1
    async with sessionmaker() as data:
        await _seed(data, "9.0.0.M1")
        resolved = await catalog._resolve_archive(dist_rules.empty(), data, "tomcat", None, "9.0.0.m1")
    assert resolved is not None
    assert str(resolved[1]) == "9.0.0.M1"


@pytest.mark.asyncio
async def test_a_new_release_takes_the_spelling_dist_writes(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed(data)
        resolution = await catalog._resolve_release(dist_rules.empty(), data, _bundle("10.0.0-M1"))
    assert resolution.catalogue is not None
    assert str(resolution.catalogue[1]) == "10.0.0-M1"


@pytest.mark.asyncio
async def test_a_republished_current_release_under_another_spelling_is_not_catalogued_again(sessionmaker) -> None:
    # A lowercase release from before #1812 seen again as 2.2-RC1 is the same release, not a twin
    async with sessionmaker() as data:
        await _seed(data, "2.2-rc1")
        resolution = await catalog._resolve_release(dist_rules.empty(), data, _bundle("2.2-RC1"))
    assert resolution.catalogue is None


def test_a_commit_bundle_keys_lowercased_and_keeps_the_written_version() -> None:
    changes = catalog._structural_changes(
        dist_rules.empty(),
        {"release/tomcat/apache-tomcat-9.0.0.M1-src.tar.gz": {"flags": "A "}},
    )
    assert list(changes.added) == [("tomcat", None, "9.0.0.m1")]
    assert changes.added[("tomcat", None, "9.0.0.m1")].version == "9.0.0.M1"


def _bundle(version: str) -> catalog._ReleaseFiles:
    bundle = catalog._ReleaseFiles("tomcat", None, version)
    bundle.files.append(("tomcat", f"apache-tomcat-{version}-src.tar.gz", classify.FileType.SOURCE))
    bundle.has_source = True
    return bundle


async def _seed(data: db.Session, *versions: str) -> None:
    # A project's default cycle comes from the after_insert hook on Project
    data.add(sql.Committee(key="tomcat", name="Tomcat", is_podling=False))
    data.add(sql.Project(key="tomcat", name="Apache Tomcat", committee_key="tomcat"))
    await data.commit()
    for version in versions:
        data.add(
            sql.Release(
                key=f"tomcat-{version}",
                project_key="tomcat",
                cycle_key="tomcat-default",
                version=version,
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
            )
        )
    await data.commit()
