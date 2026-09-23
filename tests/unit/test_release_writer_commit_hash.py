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

import pytest
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.attestable as attestable
import atr.config as config
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.source as source
import atr.storage as storage
import atr.storage.writers.release as release
import atr.storage.writers.revision as revision


@pytest.fixture
async def data(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    engine = sqlalchemy.ext.asyncio.create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setattr(config.get(), "STATE_DIR", str(tmp_path))
    for attribute, directory in [
        ("UNFINISHED_STORAGE_DIR", "unfinished"),
        ("ATTESTABLE_STORAGE_DIR", "attestable"),
        ("ARCHIVES_STORAGE_DIR", "archives"),
    ]:
        monkeypatch.setattr(config.get(), attribute, str(tmp_path / directory))
    (tmp_path / "temporary").mkdir()
    monkeypatch.setattr(
        db,
        "_global_atr_sessionmaker",
        sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False),
    )
    monkeypatch.setattr(revision.tasks, "draft_checks", mock.AsyncMock())
    monkeypatch.setattr(storage, "audit", mock.Mock())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
        async with db.Session(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.parametrize("commit_hash", ["b" * 40, None])
@pytest.mark.parametrize("phase", list(sql.ReleasePhase))
async def test_set_commit_hash_only_updates_drafts(data: db.Session, phase: sql.ReleasePhase, commit_hash: str | None):
    committee = sql.Committee(key="project", name="Project")
    project = sql.Project(
        key="project", name="Apache Project", committee=committee, repositories=["https://github.com/apache/project"]
    )
    release_row = sql.Release(
        key="project-1.0.0",
        project=project,
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
        commit_hash="a" * 40,
    )
    data.add(release_row)
    await data.commit()
    await data.execute(sqlmodel.update(sql.Release).values(phase=phase).execution_options(synchronize_session=False))
    await data.commit()
    assert release_row.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT

    write = mock.MagicMock()
    write.authorisation.asf_uid = "tester"
    write_as = mock.MagicMock()
    write_as.revision = revision.CommitteeParticipant(write, write_as, data, "project")
    writer = release.CommitteeParticipant(write, write_as, data, "project")
    expected_commit_hash = "a" * 40
    if phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        result = await writer.set_commit_hash(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), commit_hash)
        assert result is release_row
        expected_commit_hash = commit_hash
        recorded = await source.read(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), safe.RevisionNumber("00001"))
        assert recorded.override == commit_hash
        assert recorded.repository == "apache/project"
    else:
        with pytest.raises(storage.AccessError) as exc:
            await writer.set_commit_hash(safe.ProjectKey("project"), safe.VersionKey("1.0.0"), commit_hash)
        assert exc.value.status == 409
        write_as.append_to_audit_log.assert_not_called()

    await data.refresh(release_row)
    assert release_row.phase == phase
    assert release_row.commit_hash == expected_commit_hash


async def test_set_commit_hash_rechecks_new_revision(data: db.Session):
    project = sql.Project(
        key="project",
        name="Apache Project",
        committee=sql.Committee(key="project", name="Project"),
        repositories=["https://github.com/apache/project"],
    )
    release_row = sql.Release(
        key=sql.release_key("project", "1.0.0"),
        project=project,
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
    )
    data.add(release_row)
    await data.commit()
    write = mock.MagicMock()
    write.authorisation.asf_uid = "tester"
    write_as = mock.MagicMock()
    write_as.revision = revision.CommitteeParticipant(write, write_as, data, "project")
    writer = release.CommitteeParticipant(write, write_as, data, "project")
    pk, vk = safe.ProjectKey("project"), safe.VersionKey("1.0.0")

    await writer.set_commit_hash(pk, vk, "a" * 40)
    first = await attestable.load(pk, vk, safe.RevisionNumber("00001"))
    first_revision = await data.revision(release_key=release_row.key, number="00001").demand(RuntimeError())
    with mock.patch.object(
        revision.interaction, "latest_revision", new=mock.AsyncMock(side_effect=[None, first_revision])
    ):
        with pytest.raises(storage.AccessError, match="release has changed"):
            await writer.set_commit_hash(pk, vk, "c" * 40)
    await writer.set_commit_hash(pk, vk, "b" * 40)
    with mock.patch.object(
        revision.detection, "detect_archives_requiring_quarantine", return_value=[safe.RelPath("source.tar.gz")]
    ):
        with pytest.raises(storage.AccessError, match="need validation"):
            await writer.set_commit_hash(pk, vk, "c" * 40)

    assert await attestable.load(pk, vk, safe.RevisionNumber("00001")) == first
    assert (await source.read(pk, vk, safe.RevisionNumber("00002"))).sha == "b" * 40
    assert await source.inputs(pk, vk, safe.RevisionNumber("00001")) != await source.inputs(
        pk, vk, safe.RevisionNumber("00002")
    )
    assert revision.tasks.draft_checks.await_count == 2
