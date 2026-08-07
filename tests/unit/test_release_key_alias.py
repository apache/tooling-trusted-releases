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

import asfquart.base as base
import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.writers.revision as revision
import atr.web as web


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


async def _seed_collision(
    sessionmaker: sqlalchemy.ext.asyncio.async_sessionmaker[db.Session],
) -> datetime.datetime:
    created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    async with sessionmaker() as data:
        data.add(sql.Committee(key="abc", name="ABC", is_podling=False))
        data.add(sql.Project(key="abc", name="ABC", committee_key="abc"))
        data.add(sql.Project(key="abc-pqr", name="ABC PQR", committee_key="abc"))
        await data.commit()
        data.add(
            sql.Release(
                key="abc-pqr-xyz-0.1",
                project_key="abc",
                cycle_key="abc-default",
                version="pqr-xyz-0.1",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                created=created,
                activity_at=created,
            )
        )
        await data.commit()
    return created


@pytest.mark.asyncio
async def test_release_alias_read_is_not_found(sessionmaker) -> None:
    await _seed_collision(sessionmaker)
    async with sessionmaker() as data:
        with pytest.raises(base.ASFQuartException, match="Release does not exist"):
            await web.Committer.release(
                mock.MagicMock(),
                safe.ProjectKey("abc-pqr"),
                safe.VersionKey("xyz-0.1"),
                phase=None,
                data=data,
            )


@pytest.mark.asyncio
async def test_release_alias_write_has_no_side_effects(sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    created = await _seed_collision(sessionmaker)
    writer = object.__new__(revision.CommitteeParticipant)
    writer._CommitteeParticipant__data = mock.MagicMock()
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "abc"
    writer._CommitteeParticipant__write = mock.MagicMock()
    make_temp_dir = mock.Mock()
    write_files_data = mock.AsyncMock()
    monkeypatch.setattr(revision.db, "session", sessionmaker)
    monkeypatch.setattr(revision.tempfile, "mkdtemp", make_temp_dir)
    monkeypatch.setattr(revision.attestable, "write_files_data", write_files_data)

    with pytest.raises(RuntimeError, match="Release does not exist"):
        await writer.create_revision_with_quarantine(
            safe.ProjectKey("abc-pqr"),
            safe.VersionKey("xyz-0.1"),
            "tester",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )

    make_temp_dir.assert_not_called()
    write_files_data.assert_not_awaited()
    async with sessionmaker() as data:
        revisions = (await data.execute(sqlmodel.select(sql.Revision))).scalars().all()
        tasks = (await data.execute(sqlmodel.select(sql.Task))).scalars().all()
        quarantined = (await data.execute(sqlmodel.select(sql.Quarantined))).scalars().all()
        actual = await data.release(project_key="abc", version="pqr-xyz-0.1").demand(RuntimeError())
    assert revisions == []
    assert tasks == []
    assert quarantined == []
    assert actual.activity_at == created
