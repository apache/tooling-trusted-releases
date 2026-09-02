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

import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.get.release as release
import atr.models.sql as sql

_MAY = datetime.datetime(2025, 5, 10, tzinfo=datetime.UTC)


def test_committees_sorted_by_display_name() -> None:
    solr = sql.Committee(key="solr", name="Solr")
    commons = sql.Committee(key="commons", name="Commons")
    projects = [
        sql.Project(key="solr", name="Apache Solr", committee=solr),
        sql.Project(key="commons-io", name="Apache Commons IO", committee=commons),
    ]
    latest = {"solr": (1, "9.6.1", _MAY), "commons-io": (1, "2.16.1", _MAY)}

    catalog = release._committee_release_catalog(projects, latest)

    assert [entry.committee.display_name for entry in catalog] == ["Commons", "Solr"]


def test_empty_version_yields_no_latest() -> None:
    tika = sql.Committee(key="tika", name="Tika")
    projects = [sql.Project(key="tika", name="Apache Tika", committee=tika)]

    catalog = release._committee_release_catalog(projects, {"tika": (1, "", _MAY)})

    assert catalog[0].projects[0].latest_version is None


def test_groups_projects_under_their_committee() -> None:
    commons = sql.Committee(key="commons", name="Commons")
    projects = [
        sql.Project(key="commons-lang", name="Apache Commons Lang", committee=commons),
        sql.Project(key="commons-io", name="Apache Commons IO", committee=commons),
        sql.Project(key="commons-new", name="Apache Commons New", committee=commons),
    ]
    latest = {"commons-io": (2, "2.16.1", _MAY), "commons-lang": (3, "3.14.0", None)}

    catalog = release._committee_release_catalog(projects, latest)

    assert len(catalog) == 1
    assert catalog[0].committee.key == "commons"
    assert [(entry.project.key, entry.finished_count) for entry in catalog[0].projects] == [
        ("commons-io", 2),
        ("commons-lang", 3),
    ]


async def test_project_latest_finished_counts_and_picks_the_newest() -> None:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessions = sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    async with sessions() as data:
        data.add_all(
            [
                _release("tika", "2.9.1", released=datetime.datetime(2025, 1, 10, tzinfo=datetime.UTC)),
                _release("tika", "2.9.2", released=_MAY),
                _release("tika", "2.9.0", released=datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)),
                _release("solr", "9.7.0", created=datetime.datetime(2025, 6, 1, tzinfo=datetime.UTC)),
                _release("solr", "9.6.1", released=datetime.datetime(2025, 3, 1, tzinfo=datetime.UTC)),
                _release("solr", "10.0.0", released=_MAY, phase=sql.ReleasePhase.RELEASE_CANDIDATE),
            ]
        )
        await data.commit()
        latest = await interaction.project_latest_finished(data)
    await engine.dispose()

    assert latest == {"tika": (3, "2.9.2", _MAY), "solr": (2, "9.7.0", None)}


def test_release_with_no_committee_is_skipped() -> None:
    projects = [sql.Project(key="orphan", name="Apache Orphan", committee=None)]

    catalog = release._committee_release_catalog(projects, {"orphan": (1, "1.0.0", _MAY)})

    assert catalog == []


def _release(
    project_key: str,
    version: str,
    *,
    released: datetime.datetime | None = None,
    created: datetime.datetime = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC),
    phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE,
) -> sql.Release:
    return sql.Release(phase=phase, version=version, project_key=project_key, created=created, released=released)
