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

import csv
import datetime
import io
import os
import pathlib
import time
import unittest.mock as mock
from collections.abc import AsyncIterator

import pytest
import quart.datastructures as datastructures
import sqlalchemy
import sqlalchemy.event
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.shared.catalogue_diff as catalogue_diff
import atr.shared.catalogue_import as catalogue_import
import atr.shared.catalogue_rows as catalogue_rows
import atr.storage.writers.catalogue as catalogue


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
async def test_export_table_round_trips_through_the_row_models(sessionmaker) -> None:
    # A downloaded CSV must re-parse cleanly, so the current state can be edited and re-uploaded
    async with sessionmaker() as data:
        data.add(sql.Committee(key="alpha", name="Alpha", is_podling=False))
        data.add(
            sql.Project(
                key="alpha-one",
                name="Apache One",
                committee_key="alpha",
                status=sql.ProjectStatus("active"),
                version_method=sql.VersionMethod("simple"),
            )
        )
        await data.commit()
        data.add(
            sql.Release(
                key="alpha-one-1.0.0",
                project_key="alpha-one",
                cycle_key="alpha-one-default",
                version="1.0.0",
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
                is_archived=True,
            )
        )
        await data.commit()
        data.add(
            sql.Artifact(
                project_key="alpha-one",
                version="1.0.0",
                artifact_path="a.tar.gz",
                release_key="alpha-one-1.0.0",
                download_path_suffix="alpha/1.0.0",
                managed=False,
            )
        )
        await data.commit()

        projects_csv = await catalogue_import.export_table(data, "alpha", "projects")
        releases_csv = await catalogue_import.export_table(data, "alpha", "releases")
        artifacts_csv = await catalogue_import.export_table(data, "alpha", "artifacts")

    project = catalogue_rows.ProjectRow.model_validate(next(csv.DictReader(io.StringIO(projects_csv))))
    release = catalogue_rows.ReleaseRow.model_validate(next(csv.DictReader(io.StringIO(releases_csv))))
    artifact = catalogue_rows.ArtifactRow.model_validate(next(csv.DictReader(io.StringIO(artifacts_csv))))
    assert (str(project.key), project.status) == ("alpha-one", sql.ProjectStatus("active"))
    assert (str(release.key), release.is_archived) == ("alpha-one-1.0.0", True)
    assert (str(artifact.artifact_path), artifact.managed) == ("a.tar.gz", False)


@pytest.mark.asyncio
async def test_export_leaves_out_projects_with_live_workflow_data(sessionmaker) -> None:
    # The import refuses those rows, so an export that carried them could not be uploaded again
    async with sessionmaker() as data:
        await _seed_two_projects_with_release(data, {"alpha-one": "1.0.0", "alpha-two": "2.0.0"})
        data.add(
            sql.Revision(
                release_key="alpha-two-2.0.0",
                number="00001",
                seq=1,
                asfuid="tester",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            )
        )
        await data.commit()

        projects_csv = await catalogue_import.export_table(data, "alpha", "projects")
        releases_csv = await catalogue_import.export_table(data, "alpha", "releases")

    projects = [row["key"] for row in csv.DictReader(io.StringIO(projects_csv))]
    releases = [row["key"] for row in csv.DictReader(io.StringIO(releases_csv))]
    assert projects == ["alpha-one"]
    assert releases == ["alpha-one-1.0.0"]


@pytest.mark.asyncio
async def test_replace_deletes_artifacts_the_file_does_not_list(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_two_projects_with_release(data, {"alpha-one": "1.0.0"})
        for path in ("keep.tgz", "drop.tgz"):
            data.add(
                sql.Artifact(
                    project_key="alpha-one",
                    version="1.0.0",
                    artifact_path=path,
                    release_key="alpha-one-1.0.0",
                    download_path_suffix=f"alpha/{path}",
                )
            )
        await data.commit()

        keep = _artifact_row("alpha-one", "1.0.0", "keep.tgz")
        keep["download_path_suffix"] = "alpha/keep.tgz"
        diff = await _writer(data).import_catalogue_csvs({"artifacts": [keep]}, "alpha", catalogue_diff.Mode.REPLACE)

        # Both are cleared, and only the listed one is re-added
        assert diff.counts["delete"] == 2
        assert diff.counts["add"] == 1
        assert diff.counts["conflict"] == 0
        assert await data.get(sql.Artifact, ("alpha-one", "1.0.0", "keep.tgz")) is not None
        assert await data.get(sql.Artifact, ("alpha-one", "1.0.0", "drop.tgz")) is None
        # The release itself is untouched: releases.csv was not uploaded
        assert await data.get(sql.Release, "alpha-one-1.0.0") is not None


@pytest.mark.asyncio
async def test_import_hangs_a_new_project_under_its_longest_prefix_sibling(sessionmaker) -> None:
    # The CSV does not carry super_project_key, so it is derived the way the creation path derives it
    async with sessionmaker() as data:
        data.add(sql.Committee(key="alpha", name="Alpha", is_podling=False))
        data.add(sql.Project(key="alpha", name="Apache Alpha", committee_key="alpha"))
        await data.commit()

        rows = {"projects": [_project_row("alpha-one", "alpha"), _project_row("alpha-one-extra", "alpha")]}
        await _writer(data).import_catalogue_csvs(rows, "alpha", catalogue_diff.Mode.ADDITIVE)

        data.expire_all()
        one = await data.get(sql.Project, "alpha-one")
        extra = await data.get(sql.Project, "alpha-one-extra")
        assert one is not None and extra is not None
        assert one.super_project_key == "alpha"
        assert extra.super_project_key == "alpha-one"


@pytest.mark.asyncio
async def test_replace_reparents_a_sub_project_it_does_not_delete(sessionmaker) -> None:
    # Deleting a project clears the super pointer of everything beneath it, so a sub-project the
    # replace keeps must end up under whatever the file puts back in its parent's place
    async with sessionmaker() as data:
        data.add(sql.Committee(key="alpha", name="Alpha", is_podling=False))
        data.add(sql.Project(key="alpha-one", name="Apache One", committee_key="alpha"))
        await data.commit()
        data.add(
            sql.Project(
                key="alpha-one-live",
                name="Apache One Live",
                committee_key="alpha",
                super_project_key="alpha-one",
            )
        )
        await data.commit()
        # A revision makes alpha-one-live managed, so the replace leaves it alone
        data.add(
            sql.Release(
                key="alpha-one-live-1.0.0",
                project_key="alpha-one-live",
                cycle_key="alpha-one-live-default",
                version="1.0.0",
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()
        data.add(
            sql.Revision(
                release_key="alpha-one-live-1.0.0",
                number="00001",
                seq=1,
                asfuid="tester",
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            )
        )
        await data.commit()

        rows = {"projects": [_project_row("alpha-one", "alpha")]}
        await _writer(data).import_catalogue_csvs(rows, "alpha", catalogue_diff.Mode.REPLACE)

        data.expire_all()
        live = await data.get(sql.Project, "alpha-one-live")
        assert live is not None
        assert live.super_project_key == "alpha-one"


@pytest.mark.asyncio
async def test_an_artifact_repoint_survives_a_repoint_of_the_release_it_came_from(sessionmaker) -> None:
    # A repointed release takes its artifacts, so an artifact the file sends elsewhere must leave first
    async with sessionmaker() as data:
        await _seed_two_projects_with_release(data, {"alpha-one": "1.0.0"})
        data.add(sql.Project(key="alpha-three", name="Apache Three", committee_key="alpha"))
        await data.commit()
        data.add(
            sql.Release(
                key="alpha-three-1.0.0",
                project_key="alpha-three",
                cycle_key="alpha-three-default",
                version="1.0.0",
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()
        for path in ("a.tgz", "b.tgz"):
            data.add(
                sql.Artifact(
                    project_key="alpha-one",
                    version="1.0.0",
                    artifact_path=path,
                    release_key="alpha-one-1.0.0",
                    download_path_suffix=f"alpha/{path}",
                )
            )
        await data.commit()

        release_repoint = _release_row("alpha-one", "1.0.0")
        release_repoint["project_key"] = "alpha-two"
        artifact_repoint = _artifact_row("alpha-three", "1.0.0", "a.tgz")
        artifact_repoint["download_path_suffix"] = "alpha/a.tgz"
        diff = await _writer(data).import_catalogue_csvs(
            {"releases": [release_repoint], "artifacts": [artifact_repoint]}, "alpha", catalogue_diff.Mode.ADDITIVE
        )

        assert (diff.counts["release_repoint"], diff.counts["artifact_repoint"], diff.counts["conflict"]) == (1, 1, 0)
        data.expire_all()
        # The repointed artifact goes where the file asked; the other follows its release
        assert await data.get(sql.Artifact, ("alpha-three", "1.0.0", "a.tgz")) is not None
        assert await data.get(sql.Artifact, ("alpha-two", "1.0.0", "b.tgz")) is not None


@pytest.mark.asyncio
async def test_import_catalogue_csvs_classifies_and_applies_under_one_lock(sessionmaker) -> None:
    # The production entry point: build the snapshot, classify, and apply in one transaction
    async with sessionmaker() as data:
        data.add(sql.Committee(key="alpha", name="Alpha", is_podling=False))
        data.add(sql.Project(key="alpha-one", name="Apache One", committee_key="alpha"))
        await data.commit()

        rows = {
            "projects": [_project_row("alpha-two", "alpha")],
            "releases": [_release_row("alpha-two", "1.0.0")],
            "artifacts": [_artifact_row("alpha-two", "1.0.0", "a.tar.gz")],
        }
        diff = await _writer(data).import_catalogue_csvs(rows, "alpha", catalogue_diff.Mode.ADDITIVE)

        assert diff.counts["add"] == 3
        assert diff.counts["conflict"] == 0
        assert await data.get(sql.Project, "alpha-two") is not None
        assert await data.get(sql.Release, "alpha-two-1.0.0") is not None
        assert await data.get(sql.Artifact, ("alpha-two", "1.0.0", "a.tar.gz")) is not None


@pytest.mark.asyncio
async def test_import_repoints_a_release_and_its_artifact_to_another_project(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_two_projects_with_release(data, {"alpha-one": "1.0.0"})
        data.add(
            sql.Artifact(
                project_key="alpha-one",
                version="1.0.0",
                artifact_path="a.tar.gz",
                release_key="alpha-one-1.0.0",
                download_path_suffix="alpha/1.0.0",
            )
        )
        await data.commit()

        # Keep the existing release key, retarget its project -> classified as a release_repoint
        move_row = _release_row("alpha-one", "1.0.0")
        move_row["project_key"] = "alpha-two"
        diff = await _writer(data).import_catalogue_csvs(
            {"releases": [move_row]}, "alpha", catalogue_diff.Mode.ADDITIVE
        )

        assert diff.counts["release_repoint"] == 1
        assert await data.get(sql.Release, "alpha-two-1.0.0") is not None
        assert await data.get(sql.Release, "alpha-one-1.0.0") is None
        artifact = await data.get(sql.Artifact, ("alpha-two", "1.0.0", "a.tar.gz"))
        assert artifact is not None
        assert artifact.release_key == "alpha-two-1.0.0"


@pytest.mark.asyncio
async def test_import_refuses_a_move_onto_an_existing_target_release(sessionmaker) -> None:
    # The target project already holds the version, so re-keying would collide; the release_repoint is a
    # conflict the preview shows rather than a failure the apply hits
    async with sessionmaker() as data:
        await _seed_two_projects_with_release(data, {"alpha-one": "1.0.0", "alpha-two": "1.0.0"})

        move_row = _release_row("alpha-one", "1.0.0")
        move_row["project_key"] = "alpha-two"
        diff = await _writer(data).import_catalogue_csvs(
            {"releases": [move_row]}, "alpha", catalogue_diff.Mode.ADDITIVE
        )

        assert diff.counts["release_repoint"] == 0
        assert diff.counts["conflict"] == 1
        assert await data.get(sql.Release, "alpha-one-1.0.0") is not None


@pytest.mark.asyncio
async def test_import_repoints_an_artifact(sessionmaker) -> None:
    async with sessionmaker() as data:
        await _seed_two_projects_with_release(data, {"alpha-one": "1.0.0", "alpha-two": "1.0.0"})
        data.add(
            sql.Artifact(
                project_key="alpha-one",
                version="1.0.0",
                artifact_path="a.tar.gz",
                release_key="alpha-one-1.0.0",
                download_path_suffix="alpha/1.0.0",
            )
        )
        await data.commit()

        # Same dist identity, different owning project -> classified as a repoint
        repoint_row = _artifact_row("alpha-two", "1.0.0", "a.tar.gz")
        repoint_row["download_path_suffix"] = "alpha/1.0.0"
        diff = await _writer(data).import_catalogue_csvs(
            {"artifacts": [repoint_row]}, "alpha", catalogue_diff.Mode.ADDITIVE
        )

        assert diff.counts["artifact_repoint"] == 1
        assert await data.get(sql.Artifact, ("alpha-two", "1.0.0", "a.tar.gz")) is not None
        assert await data.get(sql.Artifact, ("alpha-one", "1.0.0", "a.tar.gz")) is None


@pytest.mark.asyncio
async def test_store_uploads_saves_each_picker_and_reads_back(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setattr(catalogue_import, "IMPORT_ROOT", tmp_path / "imports")
    files = {
        "projects": datastructures.FileStorage(
            stream=io.BytesIO(b"key,committee_key\nalpha-one,alpha\n"), filename="projects.csv"
        )
    }
    token = await catalogue_import.store_uploads(files, "alpha", catalogue_diff.Mode.REPLACE)
    rows = catalogue_import.read_uploads(token)
    assert rows["projects"] == [{"key": "alpha-one", "committee_key": "alpha"}]
    assert catalogue_import.committee_of(token) == "alpha"
    catalogue_import.discard(token)
    assert not (catalogue_import.IMPORT_ROOT / token).exists()


def test_sweep_removes_stale_upload_dirs_and_keeps_recent_ones(tmp_path: pathlib.Path, monkeypatch) -> None:
    # A previewed-but-never-applied upload is reclaimed once it ages past the TTL
    monkeypatch.setattr(catalogue_import, "IMPORT_ROOT", tmp_path / "imports")
    catalogue_import.IMPORT_ROOT.mkdir()
    stale = catalogue_import.IMPORT_ROOT / ("a" * 32)
    recent = catalogue_import.IMPORT_ROOT / ("b" * 32)
    stale.mkdir()
    recent.mkdir()
    aged = time.time() - catalogue_import._UPLOAD_TTL_SECONDS - 60
    os.utime(stale, (aged, aged))
    catalogue_import._sweep_stale()
    assert not stale.exists()
    assert recent.exists()


def test_uploads_reject_a_path_traversal_token(tmp_path: pathlib.Path, monkeypatch) -> None:
    # The token reaches the path from form input, so anything but a uuid hex string is refused
    monkeypatch.setattr(catalogue_import, "IMPORT_ROOT", tmp_path / "imports")
    for bad in ("../../etc", "..", "a/b", "deadbeef", ""):
        with pytest.raises(KeyError):
            catalogue_import.read_uploads(bad)
        with pytest.raises(KeyError):
            catalogue_import.discard(bad)


def _artifact_row(project: str, version: str, path: str) -> dict[str, str]:
    return {
        "project_key": project,
        "version": version,
        "artifact_path": path,
        "download_path_suffix": f"{project}/{version}",
        "managed": "false",
    }


def _project_row(key: str, committee: str) -> dict[str, str]:
    return {
        "key": key,
        "name": f"Apache {key}",
        "status": "active",
        "committee_key": committee,
        "version_method": "simple",
    }


def _release_row(project: str, version: str) -> dict[str, str]:
    return {
        "key": f"{project}-{version}",
        "project_key": project,
        "version": version,
        "phase": "release",
        "created": "2020-01-01",
        "release_status": "active",
    }


async def _seed_two_projects_with_release(data: db.Session, versioned_projects: dict[str, str]) -> None:
    data.add(sql.Committee(key="alpha", name="Alpha", is_podling=False))
    data.add(sql.Project(key="alpha-one", name="Apache One", committee_key="alpha"))
    data.add(sql.Project(key="alpha-two", name="Apache Two", committee_key="alpha"))
    await data.commit()
    for project, version in versioned_projects.items():
        data.add(
            sql.Release(
                key=f"{project}-{version}",
                project_key=project,
                cycle_key=f"{project}-default",
                version=version,
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
            )
        )
    await data.commit()


@pytest.mark.asyncio
async def test_import_refuses_to_write_a_diff_that_was_not_the_one_previewed(sessionmaker) -> None:
    # The watcher catalogues releases while the page is open, and a replace deletes whatever the
    # files do not list, so the apply must write only what the admin was shown
    async with sessionmaker() as data:
        await _seed_two_projects_with_release(data, {"alpha-one": "1.0.0"})
        rows = {"releases": [_release_row("alpha-one", "1.0.0")]}
        snapshot = await catalogue_import.build_snapshot(data, "alpha")
        previewed = catalogue_diff.fingerprint(
            catalogue_diff.classify(rows, snapshot, "alpha", catalogue_diff.Mode.REPLACE)
        )

        # A release lands under the committee between the preview and the apply
        data.add(
            sql.Release(
                key="alpha-two-9.0.0",
                project_key="alpha-two",
                cycle_key="alpha-two-default",
                version="9.0.0",
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()

        with pytest.raises(catalogue_import.StaleError):
            await _writer(data).import_catalogue_csvs(rows, "alpha", catalogue_diff.Mode.REPLACE, previewed)

        # The release the preview never showed is still there
        data.expire_all()
        assert await data.get(sql.Release, "alpha-two-9.0.0") is not None


def _writer(data: db.Session) -> catalogue.FoundationAdmin:
    writer = object.__new__(catalogue.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__asf_uid = "tester"
    writer._FoundationAdmin__write_as = mock.MagicMock()
    return writer
