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
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.storage.writers.revision as revision

type Maker = sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]


@pytest.fixture
async def maker() -> AsyncIterator[Maker]:
    db.install_detached_mutation_guard()
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with sessionmaker() as data:
        data.add(sql.Committee(key="tooling", name="Tooling"))
        project = sql.Project(key="tooling-atr", name="Apache Tooling ATR", committee_key="tooling")
        data.add(project)
        await data.commit()
        data.add(sql.ReleasePolicy(project=project, min_hours=24))
        data.add(_release("0.1.0"))
        await data.commit()
    yield sessionmaker
    await engine.dispose()


async def test_aexit_keeps_exception_from_block(maker: Maker) -> None:
    with pytest.raises(ValueError, match="boom"):
        async with maker() as data:
            project = await _project(data)
            project.description = "changed"
            raise ValueError("boom")


async def test_aexit_raises_for_uncommitted_change(maker: Maker) -> None:
    with pytest.raises(db.UnflushedChangesError, match="Project"):
        async with maker() as data:
            project = await _project(data)
            project.description = "first"
            await data.commit()
            project.description = "second"
    async with maker() as data:
        assert (await _project(data)).description == "first"


async def test_aexit_allows_explicit_rollback(maker: Maker) -> None:
    async with maker() as data:
        project = await _project(data)
        project.description = "changed"
        await data.rollback()


async def test_aexit_allows_no_op_assignment(maker: Maker) -> None:
    async with maker() as data:
        project = await _project(data)
        project.description = project.description


async def test_aexit_allows_read_only_block(maker: Maker) -> None:
    async with maker() as data:
        await _project(data)


async def test_detached_mutation_names_written_attribute(maker: Maker) -> None:
    async with maker() as data:
        project = await _project(data)
    with pytest.raises(db.DetachedMutationError, match=r"Project.description"):
        project.description = "changed"


async def test_detached_mutation_raises_across_sessions(maker: Maker) -> None:
    async with maker() as data:
        project = await data.project(key="tooling-atr", _release_policy=True).demand(RuntimeError("no project"))
    async with maker() as writer_data:
        with pytest.raises(db.DetachedMutationError, match=r"ReleasePolicy.min_hours"):
            project.release_policy.min_hours = 72
        await writer_data.commit()
    async with maker() as data:
        project = await data.project(key="tooling-atr", _release_policy=True).demand(RuntimeError("no project"))
        assert project.release_policy.min_hours == 24


async def test_detached_mutation_names_origin_of_backref_write(maker: Maker) -> None:
    async with maker() as data:
        project = await _project(data)
        release = await data.release(project_key="tooling-atr", version="0.1.0").demand(RuntimeError("no release"))
    with pytest.raises(db.DetachedMutationError, match=r"Release.project"):
        release.project = project


async def test_merge_then_mutate_lands(maker: Maker) -> None:
    async with maker() as data:
        project = await _project(data)
    async with maker() as data:
        merged = await data.merge(project)
        merged.description = "merged"
        await data.commit()
    async with maker() as data:
        assert (await _project(data)).description == "merged"


async def test_mutate_after_commit_inside_block_lands(maker: Maker) -> None:
    async with maker() as data:
        project = await _project(data)
        await data.commit()
        project.description = "after commit"
        await data.commit()
    async with maker() as data:
        assert (await _project(data)).description == "after commit"


async def test_set_committed_value_does_not_dirty_detached_instance(maker: Maker) -> None:
    async with maker() as data:
        project = await _project(data)
        release = await data.release(project_key="tooling-atr", version="0.1.0").demand(RuntimeError("no release"))
    db.set_committed_value(release, "project", project)
    assert release.project is project
    assert not sqlalchemy.inspect(release).modified


async def test_safe_session_keeps_exception_from_block(
    maker: Maker, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(revision.db, "session", maker)
    with pytest.raises(ValueError, match="boom") as info:
        async with revision.SafeSession(str(tmp_path)) as data:
            project = await _project(data)
            project.description = "changed"
            raise ValueError("boom")
    assert not isinstance(info.value.__context__, db.UnflushedChangesError)


async def test_safe_session_raises_for_uncommitted_change(
    maker: Maker, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(revision.db, "session", maker)
    with pytest.raises(db.UnflushedChangesError, match="Project"):
        async with revision.SafeSession(str(tmp_path)) as data:
            project = await _project(data)
            project.description = "changed"


async def test_transient_construct_and_commit_works(maker: Maker) -> None:
    async with maker() as data:
        release = _release("0.2.0")
        release.phase = sql.ReleasePhase.RELEASE_CANDIDATE
        data.add(release)
        await data.commit()
    async with maker() as data:
        stored = await data.release(project_key="tooling-atr", version="0.2.0").demand(RuntimeError("no release"))
        assert stored.phase == sql.ReleasePhase.RELEASE_CANDIDATE


async def _project(data: db.Session) -> sql.Project:
    return await data.project(key="tooling-atr").demand(RuntimeError("no project"))


def _release(version: str) -> sql.Release:
    return sql.Release(
        project_key="tooling-atr",
        version=version,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
    )
