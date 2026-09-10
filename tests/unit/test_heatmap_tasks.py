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
import atr.manager as manager
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.sbom.heatmap
import atr.tasks as tasks
import atr.tasks.heatmap as heatmap
import atr.tasks.maintenance as maintenance
import atr.worker as worker

_PROJECT = safe.ProjectKey("example")
_VERSION = safe.VersionKey("1.0")
_SBOM_URL = "https://downloads.apache.org/example/1.0/source.zip.cdx.json"
_NOW = datetime.datetime(2026, 9, 9, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    clock = mock.Mock(wraps=datetime)
    clock.datetime.now.return_value = _NOW
    monkeypatch.setattr(heatmap, "datetime", clock)
    monkeypatch.setattr(worker, "datetime", clock)
    return clock


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


async def test_heatmap_pacing_survives_recovery_and_does_not_block_other_tasks(database, clock) -> None:
    async with database() as data:
        queued = await heatmap.queue(data, _PROJECT, _VERSION)
        await data.commit()
        task_id = queued.id
    assert (await worker._task_next_claim())[0] == task_id
    async with database() as data:
        active = await data.task(id=task_id).get()
    assert await manager._requeue_claim(active) == 1
    await tasks.clear_scheduled()
    await heatmap.sweep()
    async with database() as data:
        ordinary = sql.Task(task_type=sql.TaskType.SBOM_QS_SCORE, task_args={}, asf_uid=constants.SYSTEM_SERVICE_UID)
        data.add(ordinary)
        await data.commit()
        ordinary_id = ordinary.id
        assert len(await data.task(task_type=sql.TaskType.SBOM_HEATMAP).all()) == 1
    clock.datetime.now.return_value = _NOW + datetime.timedelta(seconds=30)
    assert (await worker._task_next_claim())[0] == ordinary_id
    async with database() as data:
        assert await data.ns_text_get("heatmap", "next_start") == "2026-09-09T00:05:00.000000+00:00"
    clock.datetime.now.return_value = _NOW + datetime.timedelta(seconds=299)
    assert await worker._task_next_claim() is None
    clock.datetime.now.return_value = _NOW + datetime.timedelta(minutes=5)
    assert (await worker._task_next_claim())[0] == task_id


async def test_heatmap_pacing_update_is_atomic_with_claim(database, monkeypatch) -> None:
    async with database() as data:
        queued = await heatmap.queue(data, _PROJECT, _VERSION)
        await data.commit()
        task_id = queued.id
    with monkeypatch.context() as patch:
        patch.setattr(db.Session, "ns_text_set", mock.AsyncMock(side_effect=RuntimeError("unavailable")))
        with pytest.raises(RuntimeError, match="unavailable"):
            await worker._task_next_claim()
    async with database() as data:
        assert (await data.task(id=task_id).get()).status == sql.TaskStatus.QUEUED
    assert (await worker._task_next_claim())[0] == task_id


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
async def test_heatmaps_are_claimed_exclusively_across_projects(database, clock, status) -> None:
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
    if status == sql.TaskStatus.COMPLETED:
        assert (await worker._task_next_claim())[1] == sql.TaskType.CATALOG_SITE_GENERATE
    assert await worker._task_next_claim() is None
    clock.datetime.now.return_value = _NOW + datetime.timedelta(minutes=5)
    assert (await worker._task_next_claim())[0] == (ids - {claimed[0]}).pop()


async def test_maintenance_runs_heatmap_sweep(monkeypatch) -> None:
    for name in (
        "_expired_pats_maintenance",
        "_session_data_maintenance",
        "_storage_maintenance",
        "_workflow_auth_maintenance",
        "_inactivity_maintenance",
    ):
        monkeypatch.setattr(maintenance, name, mock.AsyncMock())
    monkeypatch.setattr(tasks, "schedule_next", mock.AsyncMock())
    sweep = mock.AsyncMock()
    monkeypatch.setattr(heatmap, "sweep", sweep)
    await maintenance.run({"asf_uid": constants.SYSTEM_SERVICE_UID, "next_schedule_seconds": 86400})
    sweep.assert_awaited_once_with()


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
    await heatmap.sweep()
    async with database() as data:
        assert await data.task(task_type=sql.TaskType.SBOM_HEATMAP, status=sql.TaskStatus.QUEUED).get() is None


async def test_sweep_continues_after_a_release_error(database, monkeypatch) -> None:
    failed = mock.Mock()
    monkeypatch.setattr(heatmap.log, "exception", failed)
    async with database() as data:
        saved = await _saved(data)
        await data.execute(
            sqlalchemy.text("UPDATE task SET result = :result WHERE id = :id"),
            {"result": "{", "id": saved.id},
        )
        data.add(sql.Release(project_key="example", version="2.0", phase=sql.ReleasePhase.RELEASE, created=_NOW))
        await data.flush()
        data.add(
            sql.Artifact(
                project_key="example",
                version="2.0",
                release_key="example-2.0",
                artifact_path="source.zip",
                sbom_path="source.zip.cdx.json",
                download_path_suffix="example/2.0",
            )
        )
        await data.commit()
    await heatmap.sweep()
    failed.assert_called_once_with("Heatmap sweep failed for example-1.0")
    async with database() as data:
        queued = await data.task(task_type=sql.TaskType.SBOM_HEATMAP, status=sql.TaskStatus.QUEUED).all()
        assert [task.version_key for task in queued] == ["2.0"]


@pytest.mark.parametrize("archived", [False, True])
async def test_sweep_deduplicates_concurrent_runs_and_restarts(database, archived) -> None:
    async with database() as data:
        release = await data.release(key="example-1.0").get()
        release.is_archived = archived
        await data.commit()
    await asyncio.gather(heatmap.sweep(), heatmap.sweep())
    await tasks.clear_scheduled()
    await heatmap.sweep()
    async with database() as data:
        queued = await data.task(task_type=sql.TaskType.SBOM_HEATMAP).all()
        if archived:
            assert not queued
            return
        assert len(queued) == 1
        assert queued[0].status == sql.TaskStatus.QUEUED
        assert queued[0].version_key == "1.0"
        assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is None
    await worker._task_next_claim()
    await heatmap.sweep()
    async with database() as data:
        assert len(await data.task(task_type=sql.TaskType.SBOM_HEATMAP).all()) == 1


@pytest.mark.parametrize("change", ["missing_sbom", "empty_sbom", "draft", "archived"])
async def test_sweep_ignores_ineligible_releases(database, change) -> None:
    async with database() as data:
        artifact = await data.artifact(project_key="example", version="1.0").get()
        release = await data.release(key="example-1.0").get()
        if change == "draft":
            release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
        elif change == "archived":
            release.is_archived = True
        else:
            artifact.sbom_path = None if change == "missing_sbom" else ""
        await data.commit()
    await heatmap.sweep()
    async with database() as data:
        assert await data.task(task_type=sql.TaskType.SBOM_HEATMAP).get() is None


@pytest.mark.parametrize("change", ["none", "fresh", "week", "replacement", "analysis_version"])
async def test_sweep_refreshes_only_missing_changed_or_week_old_reports(database, clock, change) -> None:
    async with database() as data:
        if change != "none":
            saved = await _saved(data)
            await catalog_site.regenerate_project(data, "example")
            if change == "fresh":
                clock.datetime.now.return_value = _NOW + datetime.timedelta(days=7, microseconds=-1)
            elif change == "week":
                clock.datetime.now.return_value = _NOW + datetime.timedelta(days=7)
            elif change == "replacement":
                artifact = await data.artifact(project_key="example", version="1.0").get()
                artifact.sbom_path = "replacement.cdx.json"
            elif change == "analysis_version":
                saved.result = saved.result.model_copy(update={"analysis_version": 0})
            await data.commit()
    await heatmap.sweep()
    async with database() as data:
        queued = await data.task(task_type=sql.TaskType.SBOM_HEATMAP, status=sql.TaskStatus.QUEUED).all()
        assert len(queued) == (0 if change == "fresh" else 1)
        if change == "fresh":
            assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is None


@pytest.mark.parametrize("change", ["removed", "withdrawn", "archived"])
async def test_sweep_removes_obsolete_publication_without_reanalysis(database, tmp_path, change) -> None:
    async with database() as data:
        await _saved(data)
        await catalog_site.regenerate_project(data, "example")
        if change == "removed":
            artifact = await data.artifact(project_key="example", version="1.0").get()
            artifact.sbom_path = None
        else:
            release = await data.release(key="example-1.0").get()
            if change == "archived":
                release.is_archived = True
            else:
                release.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
        await data.commit()
    await heatmap.sweep()
    async with database() as data:
        assert await data.task(task_type=sql.TaskType.SBOM_HEATMAP, status=sql.TaskStatus.QUEUED).get() is None
        regeneration = await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get()
        await data.delete(regeneration)
        await data.commit()
        await catalog_site.regenerate_project(data, "example")
    assert not (tmp_path / "example/1.0/heatmap.json").exists()
    assert "heatmap.html" not in (tmp_path / "example/1.0/index.html").read_text()
    await heatmap.sweep()
    async with database() as data:
        assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is None


@pytest.mark.parametrize("filename", ["heatmap.json", "heatmap.html", "heatmap-3d.html", "index.html"])
@pytest.mark.parametrize("damage", ["missing", "stale"])
async def test_sweep_repairs_publication_without_reanalysis(database, tmp_path, filename, damage) -> None:
    async with database() as data:
        await _saved(data)
        await catalog_site.regenerate_project(data, "example")
    path = tmp_path / "example/1.0" / filename
    if damage == "missing":
        path.unlink()
    else:
        timestamp = (_NOW - datetime.timedelta(seconds=1)).timestamp()
        os.utime(path, (timestamp, timestamp))
    await heatmap.sweep()
    await heatmap.sweep()
    async with database() as data:
        assert len(await data.task(task_type=sql.TaskType.SBOM_HEATMAP).all()) == 1
        regeneration = await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get()
        assert regeneration.project_key == "example"
        await data.delete(regeneration)
        await data.commit()
        await catalog_site.regenerate_project(data, "example")
    await heatmap.sweep()
    async with database() as data:
        assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is None


@pytest.mark.parametrize("status", [sql.TaskStatus.FAILED, sql.TaskStatus.BROKEN])
async def test_sweep_waits_a_day_after_failure_and_keeps_previous_report(database, clock, status) -> None:
    async with database() as data:
        saved = await _saved(data)
        saved.added = _NOW - datetime.timedelta(days=9)
        saved.completed = _NOW - datetime.timedelta(days=8)
        await data.commit()
        await catalog_site.regenerate_project(data, "example")
        failed = await heatmap.queue(data, _PROJECT, _VERSION)
        failed.added = _NOW - datetime.timedelta(days=2)
        failed.status = status
        failed.completed = _NOW
        failed.error = "unavailable"
        await data.commit()
    clock.datetime.now.return_value = _NOW + datetime.timedelta(hours=23)
    await heatmap.sweep()
    async with database() as data:
        assert await data.task(task_type=sql.TaskType.SBOM_HEATMAP, status=sql.TaskStatus.QUEUED).get() is None
        assert (await data.task(id=saved.id).get()).result == _snapshot()
        assert await data.task(task_type=sql.TaskType.CATALOG_SITE_GENERATE).get() is None
    clock.datetime.now.return_value = _NOW + datetime.timedelta(days=1)
    await heatmap.sweep()
    async with database() as data:
        assert await data.task(task_type=sql.TaskType.SBOM_HEATMAP, status=sql.TaskStatus.QUEUED).get() is not None


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
