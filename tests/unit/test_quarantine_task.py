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

import io
import pathlib
import tarfile
import unittest.mock as mock

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks as tasks
import atr.tasks.quarantine as quarantine


@pytest.mark.asyncio
async def test_mark_failed_persists_on_managed_instance():
    # This is a regression test for a bug during development
    # Instance of issue #299
    detached = mock.MagicMock(spec=sql.Quarantined)
    detached.id = 42

    managed = mock.MagicMock(spec=sql.Quarantined)

    mock_data = mock.AsyncMock()
    mock_data.merge = mock.AsyncMock(return_value=managed)

    mock_session_ctx = mock.AsyncMock()
    mock_session_ctx.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_session_ctx.__aexit__ = mock.AsyncMock(return_value=False)

    entries = [
        sql.QuarantineFileEntryV1(rel_path="bad.tar.gz", size_bytes=100, content_hash="abc", errors=["traversal"])
    ]

    with mock.patch.object(quarantine.db, "session", return_value=mock_session_ctx):
        await quarantine._mark_failed(detached, entries)

    assert managed.status == sql.QuarantineStatus.FAILED
    assert managed.file_metadata == entries
    assert managed.completed is not None


@pytest.mark.asyncio
async def test_promote_finalises_revision_and_deletes_quarantined(tmp_path: pathlib.Path):
    quarantine_dir_path = tmp_path / "quarantine"
    quarantine_dir_path.mkdir()
    quarantine_dir = str(quarantine_dir_path)

    release = mock.MagicMock()
    release.name = "proj-1.0"

    quarantined_row = mock.MagicMock(spec=sql.Quarantined)
    quarantined_row.prior_revision_name = None
    quarantined_row.asf_uid = "testuser"
    quarantined_row.description = "test upload"

    mock_safe_ctx = mock.MagicMock()
    mock_safe_ctx.__aenter__ = mock.AsyncMock(return_value=mock.AsyncMock())
    mock_safe_ctx.__aexit__ = mock.AsyncMock(return_value=False)

    mock_release_query = mock.MagicMock()
    mock_release_query.demand = mock.AsyncMock(return_value=release)

    mock_release_data = mock.AsyncMock()
    mock_release_data.release = mock.MagicMock(return_value=mock_release_query)

    mock_release_ctx = mock.AsyncMock()
    mock_release_ctx.__aenter__ = mock.AsyncMock(return_value=mock_release_data)
    mock_release_ctx.__aexit__ = mock.AsyncMock(return_value=False)

    mock_delete_data = mock.AsyncMock()
    mock_delete_data.delete = mock.AsyncMock()
    mock_delete_ctx = mock.AsyncMock()
    mock_delete_ctx.__aenter__ = mock.AsyncMock(return_value=mock_delete_data)
    mock_delete_ctx.__aexit__ = mock.AsyncMock(return_value=False)

    session_calls = iter([mock_release_ctx, mock_delete_ctx])

    with (
        mock.patch.object(
            quarantine.attestable,
            "paths_to_hashes_and_sizes",
            new_callable=mock.AsyncMock,
            return_value=({"file.txt": "hash1"}, {"file.txt": 100}),
        ),
        mock.patch.object(quarantine.util, "paths_to_inodes", return_value={"file.txt": 12345}),
        mock.patch.object(quarantine.revision, "SafeSession", return_value=mock_safe_ctx),
        mock.patch.object(quarantine.revision, "finalise_revision", new_callable=mock.AsyncMock) as mock_finalise,
        mock.patch.object(quarantine.db, "session", side_effect=session_calls),
    ):
        await quarantine._promote(quarantined_row, "proj", "1.0", "proj-1.0", quarantine_dir)

    mock_release_data.release.assert_called_once_with(
        name="proj-1.0", _release_policy=True, _project_release_policy=True
    )
    mock_finalise.assert_awaited_once()
    call_kwargs = mock_finalise.call_args.kwargs
    assert call_kwargs["was_quarantined"] is True
    assert call_kwargs["project_name"] == "proj"
    assert call_kwargs["release"] is release
    assert call_kwargs["path_to_hash"] == {"file.txt": "hash1"}
    mock_delete_data.delete.assert_awaited_once_with(quarantined_row)


def test_resolve_returns_quarantine_handler():
    handler = tasks.resolve(sql.TaskType.QUARANTINE_VALIDATE)
    assert handler is quarantine.validate


@pytest.mark.asyncio
async def test_run_safety_checks_safe_archive(tmp_path: pathlib.Path):
    archive_path = tmp_path / "safe.tar.gz"
    _create_safe_tar_gz(archive_path)

    archives = [quarantine.QuarantineArchiveEntry(rel_path="safe.tar.gz", content_hash="abc123")]
    entries, any_failed = await quarantine._run_safety_checks(archives, tmp_path)

    assert not any_failed
    assert len(entries) == 1
    assert entries[0].rel_path == "safe.tar.gz"
    assert entries[0].content_hash == "abc123"
    assert entries[0].errors == []


@pytest.mark.asyncio
async def test_run_safety_checks_unsafe_archive(tmp_path: pathlib.Path):
    archive_path = tmp_path / "unsafe.tar.gz"
    _create_traversal_tar_gz(archive_path)

    archives = [quarantine.QuarantineArchiveEntry(rel_path="unsafe.tar.gz", content_hash="def456")]
    entries, any_failed = await quarantine._run_safety_checks(archives, tmp_path)

    assert any_failed
    assert len(entries) == 1
    assert len(entries[0].errors) > 0


@pytest.mark.asyncio
async def test_validate_extraction_failure_marks_failed_and_deletes_dir(tmp_path: pathlib.Path):
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()

    row = _make_quarantined_row()
    mock_data = _make_session_returning(row)

    ok_entries = [sql.QuarantineFileEntryV1(rel_path="ok.tar.gz", size_bytes=50, content_hash="abc", errors=[])]

    with (
        mock.patch.object(quarantine.db, "session", return_value=mock_data),
        mock.patch.object(quarantine.paths, "quarantine_directory", return_value=quarantine_dir),
        mock.patch.object(
            quarantine,
            "_run_safety_checks",
            new_callable=mock.AsyncMock,
            return_value=(ok_entries, False),
        ),
        mock.patch.object(
            quarantine,
            "_extract_archives_to_cache",
            new_callable=mock.AsyncMock,
            side_effect=RuntimeError("Extraction failure"),
        ),
        mock.patch.object(quarantine, "_mark_failed", new_callable=mock.AsyncMock) as mock_mark,
        mock.patch.object(quarantine.aioshutil, "rmtree", new_callable=mock.AsyncMock) as mock_rmtree,
    ):
        result = await quarantine.validate(
            {"quarantined_id": 1, "archives": [{"rel_path": "ok.tar.gz", "content_hash": "abc"}]}
        )

    assert result is None
    mock_mark.assert_awaited_once_with(row, ok_entries, "Archive extraction to cache failed")
    mock_rmtree.assert_awaited_once_with(quarantine_dir)


@pytest.mark.asyncio
async def test_validate_missing_quarantined_row():
    mock_data = mock.AsyncMock()
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=None)
    mock_data.quarantined = mock.MagicMock(return_value=mock_query)
    mock_data.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_data.__aexit__ = mock.AsyncMock(return_value=False)

    with mock.patch.object(quarantine.db, "session", return_value=mock_data):
        result = await quarantine.validate({"quarantined_id": 999, "archives": []})

    assert result is None


@pytest.mark.asyncio
async def test_validate_non_pending_status():
    quarantined_row = mock.MagicMock()
    quarantined_row.status = sql.QuarantineStatus.FAILED

    mock_data = mock.AsyncMock()
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=quarantined_row)
    mock_data.quarantined = mock.MagicMock(return_value=mock_query)
    mock_data.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_data.__aexit__ = mock.AsyncMock(return_value=False)

    with mock.patch.object(quarantine.db, "session", return_value=mock_data):
        result = await quarantine.validate({"quarantined_id": 1, "archives": []})

    assert result is None


@pytest.mark.asyncio
async def test_validate_safety_failure_marks_failed_and_deletes_dir(tmp_path: pathlib.Path):
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()

    row = _make_quarantined_row()
    mock_data = _make_session_returning(row)

    fail_entries = [
        sql.QuarantineFileEntryV1(
            rel_path="unsafe.tar.gz", size_bytes=50, content_hash="def", errors=["path traversal"]
        )
    ]

    with (
        mock.patch.object(quarantine.db, "session", return_value=mock_data),
        mock.patch.object(quarantine.paths, "quarantine_directory", return_value=quarantine_dir),
        mock.patch.object(
            quarantine,
            "_run_safety_checks",
            new_callable=mock.AsyncMock,
            return_value=(fail_entries, True),
        ),
        mock.patch.object(quarantine, "_mark_failed", new_callable=mock.AsyncMock) as mock_mark,
        mock.patch.object(quarantine.aioshutil, "rmtree", new_callable=mock.AsyncMock) as mock_rmtree,
    ):
        result = await quarantine.validate(
            {"quarantined_id": 1, "archives": [{"rel_path": "unsafe.tar.gz", "content_hash": "def"}]}
        )

    assert result is None
    mock_mark.assert_awaited_once_with(row, fail_entries)
    mock_rmtree.assert_awaited_once_with(quarantine_dir)


@pytest.mark.asyncio
async def test_validate_success_calls_promote(tmp_path: pathlib.Path):
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()

    row = _make_quarantined_row()
    mock_data = _make_session_returning(row)

    ok_entries = [sql.QuarantineFileEntryV1(rel_path="ok.tar.gz", size_bytes=50, content_hash="abc", errors=[])]

    with (
        mock.patch.object(quarantine.db, "session", return_value=mock_data),
        mock.patch.object(quarantine.paths, "quarantine_directory", return_value=quarantine_dir),
        mock.patch.object(
            quarantine,
            "_run_safety_checks",
            new_callable=mock.AsyncMock,
            return_value=(ok_entries, False),
        ),
        mock.patch.object(quarantine, "_extract_archives_to_cache", new_callable=mock.AsyncMock),
        mock.patch.object(quarantine, "_promote", new_callable=mock.AsyncMock) as mock_promote,
        mock.patch.object(quarantine, "_mark_failed", new_callable=mock.AsyncMock) as mock_mark,
    ):
        result = await quarantine.validate(
            {"quarantined_id": 1, "archives": [{"rel_path": "ok.tar.gz", "content_hash": "abc"}]}
        )

    assert result is None
    mock_promote.assert_awaited_once_with(
        row, safe.ProjectName("proj"), safe.VersionName("1.0"), row.release.name, str(quarantine_dir)
    )
    mock_mark.assert_not_awaited()


def _create_safe_tar_gz(path: pathlib.Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="README.txt")
        content = b"Safe content"
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    path.write_bytes(buf.getvalue())


def _create_traversal_tar_gz(path: pathlib.Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../../../etc/passwd")
        content = b"traversal"
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    path.write_bytes(buf.getvalue())


def _make_quarantined_row() -> mock.MagicMock:
    row = mock.MagicMock(spec=sql.Quarantined)
    row.id = 1
    row.status = sql.QuarantineStatus.PENDING
    row.release = mock.MagicMock()
    row.release.name = "proj-1.0"
    row.release.safe_name = safe.ReleaseName(row.release.name)
    row.release.project_name = "proj"
    row.release.safe_project_name = safe.ProjectName(row.release.project_name)
    row.release.version = "1.0"
    row.release.safe_version_name = safe.VersionName(row.release.version)
    return row


def _make_session_returning(quarantined_row: mock.MagicMock) -> mock.AsyncMock:
    mock_data = mock.AsyncMock()
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=quarantined_row)
    mock_data.quarantined = mock.MagicMock(return_value=mock_query)
    mock_data.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_data.__aexit__ = mock.AsyncMock(return_value=False)
    return mock_data
