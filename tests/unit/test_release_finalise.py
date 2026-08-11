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
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Final

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.auditlog as auditlog
import atr.config as config
import atr.db as db
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.datatypes as datatypes
import atr.storage.writers.release as release_writer
import atr.tasks as tasks
import atr.tasks.svnpub as svnpub
import atr.tasks.task

INTERNAL_PUBLISH_URL: Final[str] = "https://internal.example.invalid/repos/dist/atr"


class FakeExport:
    def __init__(self, tree: dict[str, bytes]) -> None:
        self.calls: list[tuple[str, int | None]] = []
        self.tree = tree

    async def __call__(self, url: str, revision: int | None, destination: pathlib.Path) -> None:
        self.calls.append((url, revision))
        self.write(destination)

    def write(self, destination: pathlib.Path) -> None:
        destination.mkdir(parents=True)
        for rel_path, content in self.tree.items():
            path = destination / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


@pytest.fixture
async def sqlite_sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    yield sessionmaker
    await engine.dispose()


def entry_exists(path: pathlib.Path) -> bool:
    return path.exists()


def finalise_args() -> args.ReleaseFinalise:
    return args.ReleaseFinalise(
        asf_uid="alice",
        project_key=safe.ProjectKey("project"),
        version_key=safe.VersionKey("1.0.0"),
        revision_number=safe.RevisionNumber("00001"),
        svn_revision=42,
        download_path_suffix=safe.RelPath("project-1.0.0"),
        audit_until="2026-08-11T00:00:00.000Z",
    )


def setup_state(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(release_writer.paths, "get_tmp_dir", lambda: safe.StatePath(tmp_path / "temporary"))
    monkeypatch.setattr(release_writer.paths, "get_unfinished_dir", lambda: safe.StatePath(tmp_path / "unfinished"))
    (tmp_path / "temporary").mkdir()
    for rel_path, content in files.items():
        path = tmp_path / "unfinished" / "project" / "1.0.0" / "00001" / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def writer(data: db.Session) -> release_writer.FoundationAdmin:
    result = object.__new__(release_writer.FoundationAdmin)
    result._FoundationAdmin__asf_uid = "system"
    result._FoundationAdmin__data = data
    result._FoundationAdmin__write_as = SimpleNamespace(append_to_audit_log=mock.Mock())
    result._FoundationAdmin__write = mock.MagicMock()
    return result


async def seed_released(data: db.Session) -> None:
    committee = sql.Committee(
        key="project",
        name="Project",
        is_podling=False,
        committee_members=["alice"],
        committers=["alice"],
    )
    project = sql.Project(key="project", name="Project", committee=committee)
    release = sql.Release(
        key="project-1.0.0",
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        project_key=project.key,
        version="1.0.0",
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    data.add_all([committee, project, release])
    await data.commit()


def test_finalise_wiring() -> None:
    assert tasks.resolve(sql.TaskType.RELEASE_FINALISE) is svnpub.finalise
    assert sql.TaskType.RELEASE_FINALISE.label == "Release finalisation"
    parsed = results.ResultsAdapter.validate_python({"kind": "release_finalise", "audit_events": 1, "message": "x"})
    assert isinstance(parsed, results.ReleaseFinalise)


async def test_finalise_defers_when_announce_event_missing(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {"artifact.tar.gz": b"content"})
    monkeypatch.setattr(release_writer.svn, "export", FakeExport({"artifact.tar.gz": b"content"}))
    monkeypatch.setattr(
        release_writer.auditlog,
        "write_release_log",
        mock.AsyncMock(side_effect=ValueError("No release_announce event was found")),
    )
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        with pytest.raises(atr.tasks.task.DeferredError):
            await writer(data).finalise_published_release(finalise_args())

    delete.assert_not_awaited()
    assert entry_exists(tmp_path / "unfinished" / "project" / "1.0.0" / "00001" / "artifact.tar.gz")


async def test_finalise_fails_when_publication_differs(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {"artifact.tar.gz": b"content"})
    monkeypatch.setattr(release_writer.svn, "export", FakeExport({"artifact.tar.gz": b"tampered"}))
    write_release_log = mock.AsyncMock(return_value=7)
    monkeypatch.setattr(release_writer.auditlog, "write_release_log", write_release_log)
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        with pytest.raises(datatypes.FailedError, match="differ"):
            await writer(data).finalise_published_release(finalise_args())

    delete.assert_not_awaited()
    write_release_log.assert_not_awaited()


async def test_finalise_fails_when_publication_missing_file(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {"artifact.tar.gz": b"content"})
    monkeypatch.setattr(release_writer.svn, "export", FakeExport({}))
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        with pytest.raises(datatypes.FailedError, match="missing"):
            await writer(data).finalise_published_release(finalise_args())

    delete.assert_not_awaited()


async def test_finalise_fails_when_revision_directory_missing(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {})
    (tmp_path / "unfinished" / "project" / "1.0.0").mkdir(parents=True)
    export = FakeExport({})
    monkeypatch.setattr(release_writer.svn, "export", export)
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        with pytest.raises(datatypes.FailedError, match="revision"):
            await writer(data).finalise_published_release(finalise_args())

    delete.assert_not_awaited()
    assert export.calls == []


async def test_finalise_ignores_sibling_release_with_deleting_suffix(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {})
    sibling = tmp_path / "unfinished" / "project" / "1.0.0.deleting" / "00001"
    sibling.mkdir(parents=True)
    monkeypatch.setattr(release_writer.svn, "export", FakeExport({}))
    monkeypatch.setattr(release_writer.auditlog, "write_release_log", mock.AsyncMock(return_value=7))
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        result = await writer(data).finalise_published_release(finalise_args())

    assert "already removed" in result.message
    delete.assert_not_awaited()
    assert entry_exists(sibling)


async def test_finalise_rejects_tombstone_that_is_a_file(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {})
    (tmp_path / "unfinished" / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "unfinished" / "project" / "1.0.0.deleting-").write_bytes(b"stray")
    monkeypatch.setattr(release_writer.svn, "export", FakeExport({}))
    monkeypatch.setattr(release_writer.auditlog, "write_release_log", mock.AsyncMock(return_value=7))
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        with pytest.raises(datatypes.FailedError, match="tombstone"):
            await writer(data).finalise_published_release(finalise_args())

    delete.assert_not_awaited()


async def test_finalise_rejects_version_path_symlink(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {})
    target = tmp_path / "unfinished" / "other" / "1.0.0"
    target.mkdir(parents=True)
    (tmp_path / "unfinished" / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "unfinished" / "project" / "1.0.0").symlink_to(target)
    export = FakeExport({})
    monkeypatch.setattr(release_writer.svn, "export", export)
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        with pytest.raises(datatypes.FailedError, match="not a directory"):
            await writer(data).finalise_published_release(finalise_args())

    delete.assert_not_awaited()
    assert export.calls == []


async def test_finalise_removes_stale_tombstone_when_already_removed(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {})
    (tmp_path / "unfinished" / "project" / "1.0.0.deleting-" / "00001").mkdir(parents=True)
    export = FakeExport({})
    monkeypatch.setattr(release_writer.svn, "export", export)
    write_release_log = mock.AsyncMock(return_value=7)
    monkeypatch.setattr(release_writer.auditlog, "write_release_log", write_release_log)
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        result = await writer(data).finalise_published_release(finalise_args())

    assert "already removed" in result.message
    assert export.calls == []
    delete.assert_awaited_once()
    assert str(delete.await_args.args[0]) == str(tmp_path / "unfinished" / "project" / "1.0.0.deleting-")


async def test_finalise_verifies_compiles_and_deletes(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {"artifact.tar.gz": b"content", "artifact.tar.gz.asc": b"sig"})
    export = FakeExport({"artifact.tar.gz": b"content", "artifact.tar.gz.asc": b"sig"})
    monkeypatch.setattr(release_writer.svn, "export", export)
    write_release_log = mock.AsyncMock(return_value=7)
    monkeypatch.setattr(release_writer.auditlog, "write_release_log", write_release_log)
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        result = await writer(data).finalise_published_release(finalise_args())

    assert result.kind == "release_finalise"
    assert result.audit_events == 7
    assert "removed the local files" in result.message
    assert export.calls == [(f"{INTERNAL_PUBLISH_URL}/project/project-1.0.0", 42)]
    delete.assert_awaited_once()
    assert str(delete.await_args.args[0]) == str(tmp_path / "unfinished" / "project" / "1.0.0.deleting-")
    assert not entry_exists(tmp_path / "unfinished" / "project" / "1.0.0")
    assert write_release_log.await_args.kwargs["until"] == "2026-08-11T00:00:00.000Z"
    assert write_release_log.await_args.kwargs["required_action"] == auditlog.RELEASE_ANNOUNCE_ACTION


async def test_finalise_warns_on_unexpected_publication_files(
    sqlite_sessionmaker, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_state(tmp_path, monkeypatch, {"artifact.tar.gz": b"content"})
    export = FakeExport({"artifact.tar.gz": b"content", "unexpected.txt": b"extra"})
    monkeypatch.setattr(release_writer.svn, "export", export)
    write_release_log = mock.AsyncMock(return_value=7)
    monkeypatch.setattr(release_writer.auditlog, "write_release_log", write_release_log)
    delete = mock.AsyncMock()
    monkeypatch.setattr(release_writer.util, "delete_immutable_directory", delete)
    warnings: list[str] = []
    monkeypatch.setattr(release_writer.log, "warning", warnings.append)
    async with sqlite_sessionmaker() as data:
        await seed_released(data)

        result = await writer(data).finalise_published_release(finalise_args())

    assert "removed the local files" in result.message
    assert any("unexpected.txt" in warning for warning in warnings)
    delete.assert_awaited_once()
