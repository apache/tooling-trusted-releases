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

import asyncio
import datetime
import os
import unittest.mock as mock

import pytest
import sqlalchemy.event
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.catalog_site as catalog_site
import atr.constants as constants
import atr.db as db
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.sbom.heatmap
import atr.tasks as tasks
import atr.tasks.heatmap as heatmap
import atr.worker as worker

_PROJECT = safe.ProjectKey("example")
_VERSION = safe.VersionKey("1.0")
_SBOM_URL = "https://downloads.apache.org/example/1.0/source.zip.cdx.json"
_NOW = datetime.datetime(2026, 9, 9, tzinfo=datetime.UTC)


@pytest.fixture
async def database(monkeypatch, tmp_path):
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "tasks.sqlite"))
    sqlalchemy.event.listen(engine.sync_engine, "connect", _foreign_keys)
    async with engine.begin() as connection:
        await connection.execute(sqlalchemy.text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    monkeypatch.setattr(db, "session", maker)
    monkeypatch.setattr(paths, "get_catalog_site_dir", lambda: safe.StatePath(tmp_path))
    async with maker() as data:
        data.add(sql.Committee(key="example", name="Example", catalog_reviewed=True))
        await data.flush()
        data.add(sql.Project(key="example", name="Apache Example", committee_key="example"))
        await data.flush()
        data.add(sql.Release(project_key="example", version="1.0", phase=sql.ReleasePhase.RELEASE, created=_NOW))
        await data.flush()
        data.add(
            sql.Artifact(
                project_key="example",
                version="1.0",
                release_key="example-1.0",
                artifact_path="source.zip",
                sbom_path="source.zip.cdx.json",
                download_path_suffix="example/1.0",
            )
        )
        await data.commit()
    yield maker
    await engine.dispose()


@pytest.mark.parametrize("change", ["added", "removed", "replaced", "archived", "withdrawn", "analysis_version"])
async def test_catalogue_removes_inapplicable_reports(database, tmp_path, change) -> None:
    async with database() as data:
        await _saved(data)
        await catalog_site.regenerate_project(data, "example")
        assert (tmp_path / "example/1.0/heatmap.json").is_file()
        artifact = await data.artifact(project_key="example", version="1.0").get()
        release = await data.release(project_key="example", version="1.0").get()
        match change:
            case "added":
                data.add(
                    sql.Artifact(
                        project_key="example",
                        version="1.0",
                        release_key=release.key,
                        artifact_path="binary.zip",
                        sbom_path="binary.zip.cdx.json",
                        download_path_suffix="example/1.0",
                    )
                )
            case "removed":
                artifact.sbom_path = None
            case "replaced":
                artifact.sbom_path = "replacement.cdx.json"
            case "archived":
                release.is_archived = True
            case "withdrawn":
                release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
            case "analysis_version":
                saved = await data.task(task_type=sql.TaskType.SBOM_HEATMAP).get()
                saved.result = saved.result.model_copy(update={"analysis_version": 0})
        await data.commit()
        await catalog_site.regenerate_project(data, "example")
        assert not (tmp_path / "example/1.0/heatmap.json").exists()
        assert not (tmp_path / "example/1.0/heatmap.html").exists()
        assert "heatmap.html" not in (tmp_path / "example/1.0/index.html").read_text()


async def test_completion_and_publication_are_atomic(database, monkeypatch) -> None:
    async with database() as data:
        previous = await _saved(data)
        current = await heatmap.queue(data, _PROJECT, _VERSION)
        await data.commit()
        current_id = current.id
    claimed = await worker._task_next_claim()
    assert claimed[0] == current_id
    with monkeypatch.context() as patch:
        patch.setattr(catalog_site, "queue_regeneration", mock.AsyncMock(side_effect=RuntimeError("unavailable")))
        with pytest.raises(RuntimeError, match="unavailable"):
            await worker._task_result_process(current_id, _snapshot(), sql.TaskStatus.COMPLETED)
    async with database() as data:
        assert (await data.task(id=current_id).get()).status == sql.TaskStatus.ACTIVE
        assert await data.task(id=previous.id).get() is not None
        assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is None
    await worker._task_result_process(current_id, _snapshot(), sql.TaskStatus.COMPLETED)
    async with database() as data:
        assert await data.task(id=previous.id).get() is None
        assert (await data.task(id=current_id).get()).result == _snapshot()
        assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is not None


@pytest.mark.parametrize("status", [sql.TaskStatus.FAILED, sql.TaskStatus.BROKEN])
async def test_failed_refresh_and_late_completion_preserve_the_previous_result(database, status) -> None:
    async with database() as data:
        previous = await _saved(data)
        current = await heatmap.queue(data, _PROJECT, _VERSION)
        await data.commit()
        current_id = current.id
    await worker._task_next_claim()
    await worker._task_result_process(current_id, None, status, "failed refresh")
    await worker._task_result_process(current_id, _snapshot(), sql.TaskStatus.COMPLETED)
    async with database() as data:
        assert (await data.task(id=current_id).get()).status == status
        assert (await data.task(id=previous.id).get()).result == _snapshot()
        assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is None


async def test_heatmap_waiters_do_not_block_other_tasks(database) -> None:
    async with database() as data:
        older = await heatmap.queue(data, _PROJECT, _VERSION)
        older.scheduled = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
        newer = await heatmap.queue(data, _PROJECT, safe.VersionKey("2.0"))
        await data.commit()
        older_id, newer_id = older.id, newer.id
    assert (await worker._task_next_claim())[0] == newer_id
    async with database() as data:
        older = await data.task(id=older_id).get()
        older.scheduled = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
        ordinary = sql.Task(task_type=sql.TaskType.SBOM_QS_SCORE, task_args={}, asf_uid=constants.SYSTEM_SERVICE_UID)
        data.add(ordinary)
        await data.commit()
        ordinary_id = ordinary.id
    assert (await worker._task_next_claim())[0] == ordinary_id
    assert await worker._task_next_claim() is None
    async with database() as data:
        assert (await data.task(id=older_id).get()).status == sql.TaskStatus.QUEUED


@pytest.mark.parametrize("status", [sql.TaskStatus.COMPLETED, sql.TaskStatus.FAILED])
async def test_heatmaps_are_claimed_exclusively_across_projects(database, status) -> None:
    async with database() as data:
        data.add(sql.Project(key="other", name="Other", committee_key="example"))
        await data.flush()
        first = await heatmap.queue(data, _PROJECT, _VERSION)
        second = await heatmap.queue(data, safe.ProjectKey("other"), _VERSION)
        await data.commit()
        ids = {first.id, second.id}
    claims = await asyncio.gather(worker._task_next_claim(), worker._task_next_claim())
    assert claims.count(None) == 1
    claimed = next(claim for claim in claims if claim is not None)
    assert claimed[0] in ids
    assert await worker._task_next_claim() is None
    result = _snapshot() if (status == sql.TaskStatus.COMPLETED) else None
    await worker._task_result_process(claimed[0], result, status, "failed" if result is None else None)
    assert (await worker._task_next_claim())[0] == (ids - {claimed[0]}).pop()


@pytest.mark.parametrize("archived", [False, True])
async def test_queued_analysis_is_stored_and_published(database, monkeypatch, tmp_path, archived) -> None:
    sbom_url = _SBOM_URL.replace("downloads.apache.org", "archive.apache.org/dist") if archived else _SBOM_URL
    snapshot = _snapshot(sbom_url)
    analyse = mock.AsyncMock(return_value=snapshot)
    monkeypatch.setattr(atr.sbom.heatmap, "analyse", analyse)
    async with database() as data:
        release = await data.release(project_key="example", version="1.0").get()
        release.is_archived = archived
        queued = await heatmap.queue(data, _PROJECT, _VERSION)
        assert await heatmap.queue(data, _PROJECT, _VERSION) is queued
        assert queued.asf_uid == constants.SYSTEM_SERVICE_UID
        assert queued.inputs_hash is None
        await data.commit()
        task_id = queued.id
    claimed = await worker._task_next_claim()
    assert claimed[0] == task_id
    async with database() as data:
        assert (await heatmap.queue(data, _PROJECT, _VERSION)).id == task_id
    await worker._task_process(*claimed)
    analyse.assert_awaited_once_with({"source.zip": sbom_url})
    async with database() as data:
        saved = await data.task(id=task_id).get()
        assert saved.status == sql.TaskStatus.COMPLETED
        assert saved.result == snapshot
        regeneration = await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get()
        assert regeneration.project_key == "example"
        regeneration_args = regeneration.task_args
    await tasks.resolve(sql.TaskType.CATALOG_SITE_GENERATE)(regeneration_args)
    assert results.SBOMHeatmap.model_validate_json((tmp_path / "example/1.0/heatmap.json").read_text()) == snapshot
    assert "heatmap-table.js" in (tmp_path / "example/1.0/heatmap.html").read_text()
    assert "heatmap.html" in (tmp_path / "example/1.0/index.html").read_text()
    assert (tmp_path / "assets/js/src/heatmap-table.js").is_file()
    assert (tmp_path / "assets/css/heatmap.css").is_file()
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("published", [False, True])
async def test_unpublished_release_or_missing_sbom_is_not_analysed(database, monkeypatch, published) -> None:
    analyse = mock.AsyncMock()
    monkeypatch.setattr(atr.sbom.heatmap, "analyse", analyse)
    async with database() as data:
        if published:
            artifact = await data.artifact(project_key="example", version="1.0").get()
            artifact.sbom_path = None
        else:
            release = await data.release(project_key="example", version="1.0").get()
            release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
        queued = await heatmap.queue(data, _PROJECT, _VERSION)
        await data.commit()
        task_args = queued.task_args
    with pytest.raises(ValueError, match="published"):
        await tasks.resolve(sql.TaskType.SBOM_HEATMAP)(task_args)
    analyse.assert_not_awaited()


def _foreign_keys(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def _saved(data):
    saved = sql.Task(
        task_type=sql.TaskType.SBOM_HEATMAP,
        task_args={"project_key": "example", "version_key": "1.0"},
        asf_uid=constants.SYSTEM_SERVICE_UID,
        project_key="example",
        version_key="1.0",
        status=sql.TaskStatus.COMPLETED,
        completed=_NOW,
        started=_NOW,
        pid=os.getpid(),
        result=_snapshot(),
    )
    data.add(saved)
    await data.commit()
    return saved


def _snapshot(sbom_url=_SBOM_URL):
    return results.SBOMHeatmap(
        analysis_version=atr.sbom.heatmap.ANALYSIS_VERSION,
        generated_at=_NOW.isoformat(),
        sboms=[results.SBOMHeatmapSbom(artifact_path="source.zip", sbom_url=sbom_url, sha256="a" * 64)],
        rows=[],
        sources={},
        attribution="Example",
    )
