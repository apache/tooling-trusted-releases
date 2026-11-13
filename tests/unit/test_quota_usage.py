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

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.sql as sql


@pytest.fixture
async def data() -> abc.AsyncIterator[db.Session]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    async with sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session)() as session:
        yield session
    await engine.dispose()


async def test_project_bytes_count_distinct_content_of_releases_in_progress(data: db.Session):
    _release(data, "example", "1.0", sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
    _release(data, "example", "2.0", sql.ReleasePhase.RELEASE_PREVIEW)
    _release(data, "example", "0.9", sql.ReleasePhase.RELEASE)
    _release(data, "other", "1.0", sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
    # Replaced in the second revision, so both contents are held
    _file(data, "example-1.0", "a.tar.gz", 1, "hash1", 100)
    _file(data, "example-1.0", "a.tar.gz", 2, "hash2", 110)
    # The same content again, as when only its classification changes
    _file(data, "example-1.0", "a.tar.gz", 3, "hash2", 110)
    _file(data, "example-1.0", "b.txt", 1, "hash3", 5)
    # Each release holds its own copy
    _file(data, "example-2.0", "a.tar.gz", 1, "hash2", 110)
    _file(data, "example-0.9", "a.tar.gz", 1, "hash4", 1000)
    _file(data, "other-1.0", "a.tar.gz", 1, "hash5", 2000)
    await data.flush()

    assert await interaction.project_bytes_in_progress(data, "example") == 325
    assert await interaction.project_bytes_in_progress(data, "none") == 0
    assert await interaction.release_content_hashes(data, "example-1.0") == {"hash1", "hash2", "hash3"}
    assert await interaction.project_bytes_in_progress_by_project(data) == {"example": 325, "other": 2000}
    assert await interaction.releases_in_progress_count_by_project(data) == {"example": 2, "other": 1}
    # The latest revision of each release, which no longer has the first content of a.tar.gz
    assert sorted(await interaction.release_sizes_in_progress(data)) == [
        ("example", 110, 1),
        ("example", 115, 2),
        ("other", 2000, 1),
    ]


def _file(data: db.Session, release_key: str, path: str, seq: int, content_hash: str, size: int) -> None:
    data.add(
        sql.ReleaseFileState(
            release_key=release_key,
            path=path,
            since_revision_seq=seq,
            present=True,
            content_hash=content_hash,
            classification="source",
            size=size,
        )
    )


def _release(data: db.Session, project_key: str, version: str, phase: sql.ReleasePhase) -> None:
    data.add(
        sql.Release(
            key=f"{project_key}-{version}",
            project_key=project_key,
            cycle_key=1,
            version=version,
            phase=phase,
            created=datetime.datetime(2026, 11, 13, tzinfo=datetime.UTC),
        )
    )
