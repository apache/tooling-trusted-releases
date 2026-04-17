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

import errno
import io
import pathlib
import stat
import tarfile
import unittest.mock as mock

import asfquart.base as base
import exarch
import pytest

import atr.archives as archives
import atr.models.args as args
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.revision as revision
import atr.tasks as tasks
import atr.tasks.quarantine as quarantine

type TarEntry = tuple[str, str, bytes | str]


@pytest.mark.asyncio
async def test_clear_quarantine_raises_when_not_found():
    mock_data = mock.AsyncMock()
    active_project = mock.MagicMock(status=sql.ProjectStatus.ACTIVE)
    active_project_query = mock.MagicMock()
    active_project_query.demand = mock.AsyncMock(return_value=active_project)
    mock_data.project = mock.MagicMock(return_value=active_project_query)
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=None)
    mock_data.quarantined = mock.MagicMock(return_value=mock_query)

    writer = _make_revision_writer(mock_data)
    with pytest.raises(RuntimeError, match="not found"):
        await writer.clear_quarantine(safe.ProjectKey("proj"), safe.VersionKey("1.0"), 999)

    mock_data.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_quarantine_transitions_failed_to_acknowledged():
    quarantined_row = mock.MagicMock(spec=sql.Quarantined)
    quarantined_row.id = 7
    quarantined_row.status = sql.QuarantineStatus.FAILED

    mock_data = mock.AsyncMock()
    active_project = mock.MagicMock(status=sql.ProjectStatus.ACTIVE)
    active_project_query = mock.MagicMock()
    active_project_query.demand = mock.AsyncMock(return_value=active_project)
    mock_data.project = mock.MagicMock(return_value=active_project_query)
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=quarantined_row)
    mock_data.quarantined = mock.MagicMock(return_value=mock_query)

    writer = _make_revision_writer(mock_data)
    await writer.clear_quarantine(safe.ProjectKey("proj"), safe.VersionKey("1.0"), 7)

    assert quarantined_row.status == sql.QuarantineStatus.ACKNOWLEDGED
    mock_data.commit.assert_awaited_once()
    mock_data.quarantined.assert_called_once_with(id=7, release_key="proj-1.0", status=sql.QuarantineStatus.FAILED)


def test_extract_archive_to_dir_accepts_dotenv_anywhere(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "safe.tar.gz"
    _write_tar_gz(
        archive_path,
        [
            _tar_regular_file(".env", b"ATR_STATUS=ALPHA\n"),
            _tar_regular_file("config/.env", b"SECRET=value\n"),
        ],
    )
    quarantine._extract_archive_to_dir(
        safe.StatePath(archive_path, tmp_path),
        safe.StatePath(tmp_path / "out", tmp_path),
        safe.StatePath(tmp_path, tmp_path),
        archives.extraction_config(),
    )


def test_extract_archive_to_dir_accepts_safe_archive(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "safe.tar.gz"
    _write_tar_gz(archive_path, [_tar_regular_file("dist/file.txt", b"hello")])
    elapsed = quarantine._extract_archive_to_dir(
        safe.StatePath(archive_path, tmp_path),
        safe.StatePath(tmp_path / "out", tmp_path),
        safe.StatePath(tmp_path, tmp_path),
        archives.extraction_config(),
    )
    assert isinstance(elapsed, float)


def test_extract_archive_to_dir_rejects_absolute_path(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    _write_tar_gz(archive_path, [_tar_regular_file("/etc/passwd", b"x")])
    with pytest.raises(exarch.PathTraversalError):
        quarantine._extract_archive_to_dir(
            safe.StatePath(archive_path, tmp_path),
            safe.StatePath(tmp_path / "out", tmp_path),
            safe.StatePath(tmp_path, tmp_path),
            archives.extraction_config(),
        )


def test_extract_archive_to_dir_rejects_hardlink_escaping_root(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    _write_tar_gz(
        archive_path,
        [
            _tar_regular_file("dist/file.txt", b"ok"),
            _tar_hardlink("dist/hard", "../../outside.txt"),
        ],
    )
    with pytest.raises(exarch.SecurityViolationError):
        quarantine._extract_archive_to_dir(
            safe.StatePath(archive_path, tmp_path),
            safe.StatePath(tmp_path / "out", tmp_path),
            safe.StatePath(tmp_path, tmp_path),
            archives.extraction_config(),
        )


def test_extract_archive_to_dir_rejects_path_traversal(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    _write_tar_gz(archive_path, [_tar_regular_file("../outside.txt", b"x")])
    with pytest.raises(exarch.PathTraversalError):
        quarantine._extract_archive_to_dir(
            safe.StatePath(archive_path, tmp_path),
            safe.StatePath(tmp_path / "out", tmp_path),
            safe.StatePath(tmp_path, tmp_path),
            archives.extraction_config(),
        )


def test_extract_archive_to_dir_rejects_symlink_escaping_root(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    _write_tar_gz(
        archive_path,
        [
            _tar_regular_file("dist/file.txt", b"ok"),
            _tar_symlink("dist/link", "../../outside.txt"),
        ],
    )
    with pytest.raises(exarch.SymlinkEscapeError):
        quarantine._extract_archive_to_dir(
            safe.StatePath(archive_path, tmp_path),
            safe.StatePath(tmp_path / "out", tmp_path),
            safe.StatePath(tmp_path, tmp_path),
            archives.extraction_config(),
        )


@pytest.mark.asyncio
async def test_extract_archives_discards_staging_dir_on_enotempty_collision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    quarantine_dir = temp_dir / "quarantine"
    quarantine_dir.path.mkdir()
    archive_rel_path = "artifact.tar.gz"
    (quarantine_dir / archive_rel_path).path.write_bytes(b"archive")
    cache_root = temp_dir / "cache"
    tmp_root = temp_dir / "temporary"
    recorded: dict[str, pathlib.Path] = {}

    def extract_archive(_archive_path: str, extract_dir: str, _config: object) -> None:
        staging_dir = pathlib.Path(extract_dir)
        recorded["staging_dir"] = staging_dir
        (staging_dir / "content.txt").write_text("staged")

    def rename(src: pathlib.Path | str, dst: pathlib.Path | str) -> None:
        dst_path = pathlib.Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)
        (dst_path / "winner.txt").write_text("winner")
        raise OSError(errno.ENOTEMPTY, "Directory not empty", str(dst_path))

    monkeypatch.setattr(quarantine.paths, "get_archives_dir", lambda: cache_root)
    monkeypatch.setattr(quarantine.paths, "get_tmp_dir", lambda: tmp_root)
    monkeypatch.setattr(quarantine.exarch, "extract_archive", extract_archive)
    monkeypatch.setattr(quarantine.os, "rename", rename)

    entries = [sql.QuarantineFileEntryV1(rel_path=archive_rel_path, size_bytes=7, content_hash="blake3:ghi", errors=[])]

    await quarantine._extract_archives(
        [args.QuarantineArchiveEntry(rel_path=archive_rel_path, content_hash="blake3:ghi")],
        quarantine_dir,
        safe.ProjectKey("proj"),
        safe.VersionKey("1.0"),
        entries,
    )

    cache_dir = cache_root / "proj" / "1.0" / quarantine.hashes.filesystem_archives_key("blake3:ghi")

    assert cache_dir.path.is_dir()
    assert (cache_dir / "winner.txt").path.read_text() == "winner"
    assert not recorded["staging_dir"].exists()


@pytest.mark.asyncio
async def test_extract_archives_discards_staging_dir_when_other_worker_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    quarantine_dir = temp_dir / "quarantine"
    quarantine_dir.path.mkdir()
    archive_rel_path = "artifact.tar.gz"
    (quarantine_dir / archive_rel_path).path.write_bytes(b"archive")
    cache_root = temp_dir / "cache"
    tmp_root = temp_dir / "temporary"
    recorded: dict[str, pathlib.Path] = {}

    def extract_archive(_archive_path: str, extract_dir: str, _config: object) -> None:
        staging_dir = pathlib.Path(extract_dir)
        recorded["staging_dir"] = staging_dir
        (staging_dir / "content.txt").write_text("staged")

    def rename(src: pathlib.Path | str, dst: pathlib.Path | str) -> None:
        dst_path = pathlib.Path(dst)
        dst_path.mkdir(parents=True, exist_ok=True)
        (dst_path / "winner.txt").write_text("winner")
        raise FileExistsError(dst)

    monkeypatch.setattr(quarantine.paths, "get_archives_dir", lambda: cache_root)
    monkeypatch.setattr(quarantine.paths, "get_tmp_dir", lambda: tmp_root)
    monkeypatch.setattr(quarantine.exarch, "extract_archive", extract_archive)
    monkeypatch.setattr(quarantine.os, "rename", rename)

    entries = [sql.QuarantineFileEntryV1(rel_path=archive_rel_path, size_bytes=7, content_hash="blake3:def", errors=[])]

    await quarantine._extract_archives(
        [args.QuarantineArchiveEntry(rel_path=archive_rel_path, content_hash="blake3:def")],
        quarantine_dir,
        safe.ProjectKey("proj"),
        safe.VersionKey("1.0"),
        entries,
    )

    cache_dir = cache_root / "proj" / "1.0" / quarantine.hashes.filesystem_archives_key("blake3:def")

    assert cache_dir.path.is_dir()
    assert (cache_dir / "winner.txt").path.read_text() == "winner"
    assert not recorded["staging_dir"].exists()


@pytest.mark.asyncio
async def test_extract_archives_propagates_exarch_error_to_file_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    quarantine_dir = temp_dir / "quarantine"
    quarantine_dir.path.mkdir()
    archive_rel_path = "artifact.tar.gz"
    (quarantine_dir / archive_rel_path).path.write_bytes(b"archive")
    cache_root = temp_dir / "cache"
    tmp_root = temp_dir / "temporary"

    def extract_archive(_archive_path: str, _extract_dir: str, _config: object) -> None:
        raise RuntimeError("unsafe zip detected")

    monkeypatch.setattr(quarantine.paths, "get_archives_dir", lambda: cache_root)
    monkeypatch.setattr(quarantine.paths, "get_tmp_dir", lambda: tmp_root)
    monkeypatch.setattr(quarantine.exarch, "extract_archive", extract_archive)

    entries = [sql.QuarantineFileEntryV1(rel_path=archive_rel_path, size_bytes=7, content_hash="blake3:bad", errors=[])]

    with pytest.raises(RuntimeError, match="unsafe zip detected"):
        await quarantine._extract_archives(
            [args.QuarantineArchiveEntry(rel_path=archive_rel_path, content_hash="blake3:bad")],
            quarantine_dir,
            safe.ProjectKey("proj"),
            safe.VersionKey("1.0"),
            entries,
        )

    assert len(entries[0].errors) == 1
    assert "unsafe zip detected" in entries[0].errors[0]


@pytest.mark.asyncio
async def test_extract_archives_stages_in_temporary_then_promotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    quarantine_dir = temp_dir / "quarantine"
    quarantine_dir.path.mkdir()
    archive_rel_path = "artifact.tar.gz"
    (quarantine_dir / archive_rel_path).path.write_bytes(b"archive")
    cache_root = temp_dir / "cache"
    tmp_root = temp_dir / "temporary"
    recorded: dict[str, str] = {}

    def extract_archive(archive_path: str, extract_dir: str, _config: object) -> None:
        recorded["archive_path"] = archive_path
        recorded["extract_dir"] = extract_dir
        extract_path = pathlib.Path(extract_dir)
        (extract_path / "content.txt").write_text("cached")

    monkeypatch.setattr(quarantine.paths, "get_archives_dir", lambda: cache_root)
    monkeypatch.setattr(quarantine.paths, "get_tmp_dir", lambda: tmp_root)
    monkeypatch.setattr(quarantine.exarch, "extract_archive", extract_archive)

    entries = [sql.QuarantineFileEntryV1(rel_path=archive_rel_path, size_bytes=7, content_hash="blake3:abc", errors=[])]

    await quarantine._extract_archives(
        [args.QuarantineArchiveEntry(rel_path=archive_rel_path, content_hash="blake3:abc")],
        quarantine_dir,
        safe.ProjectKey("proj"),
        safe.VersionKey("1.0"),
        entries,
    )

    cache_dir = cache_root / "proj" / "1.0" / quarantine.hashes.filesystem_archives_key("blake3:abc")
    staging_base = tmp_root

    assert recorded["archive_path"] == str(quarantine_dir / archive_rel_path)
    assert pathlib.Path(recorded["extract_dir"]).parent == staging_base.path
    assert pathlib.Path(recorded["extract_dir"]) != cache_dir
    assert cache_dir.path.is_dir()
    assert (cache_dir / "content.txt").path.read_text() == "cached"
    assert list(staging_base.path.iterdir()) == []
    assert stat.S_IMODE(cache_dir.path.stat().st_mode) == 0o555
    assert stat.S_IMODE((cache_dir / "content.txt").path.stat().st_mode) == 0o444


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
    quarantined_row.prior_revision_key = None
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
        key="proj-1.0", _release_policy=True, _project_release_policy=True
    )
    mock_finalise.assert_awaited_once()
    call_kwargs = mock_finalise.call_args.kwargs
    assert call_kwargs["was_quarantined"] is True
    assert call_kwargs["project_key"] == "proj"
    assert call_kwargs["release"] is release
    assert call_kwargs["path_to_hash"] == {"file.txt": "hash1"}
    mock_delete_data.delete.assert_awaited_once_with(quarantined_row)


def test_resolve_returns_quarantine_handler():
    handler = tasks.resolve(sql.TaskType.QUARANTINE_VALIDATE)
    assert handler is quarantine.validate


@pytest.mark.asyncio
async def test_set_tag_raises_404_when_revision_missing():
    mock_data = mock.AsyncMock()
    update_result = mock.MagicMock()
    update_result.rowcount = 0
    mock_data.execute = mock.AsyncMock(return_value=update_result)
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=None)
    mock_data.revision = mock.MagicMock(return_value=mock_query)
    _attach_active_release(mock_data)

    writer = _make_revision_writer(mock_data)
    with pytest.raises(base.ASFQuartException, match="Revision 00001 not found") as exc_info:
        await writer.set_tag(safe.ProjectKey("proj"), safe.VersionKey("1.0"), "00001", "rc1")

    assert getattr(exc_info.value, "errorcode", None) == 404
    mock_data.commit.assert_not_awaited()
    mock_data.rollback.assert_awaited_once()
    mock_data.revision.assert_called_once_with(release_key="proj-1.0", number="00001")


@pytest.mark.asyncio
async def test_set_tag_rejects_invalid_tag():
    mock_data = mock.AsyncMock()

    writer = _make_revision_writer(mock_data)
    with pytest.raises(
        storage.AccessError, match="Tag must contain only letters, numbers, plus, underscore, dot, or hyphen"
    ):
        await writer.set_tag(safe.ProjectKey("proj"), safe.VersionKey("1.0"), "00001", "bad tag")

    mock_data.execute.assert_not_awaited()
    mock_data.commit.assert_not_awaited()
    mock_data.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_tag_rejects_revision_with_existing_tag():
    revision_row = mock.MagicMock(spec=sql.Revision)
    revision_row.tag = "rc0"

    mock_data = mock.AsyncMock()
    update_result = mock.MagicMock()
    update_result.rowcount = 0
    mock_data.execute = mock.AsyncMock(return_value=update_result)
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=revision_row)
    mock_data.revision = mock.MagicMock(return_value=mock_query)
    _attach_active_release(mock_data)

    writer = _make_revision_writer(mock_data)
    with pytest.raises(storage.AccessError, match="already has a tag and cannot be changed"):
        await writer.set_tag(safe.ProjectKey("proj"), safe.VersionKey("1.0"), "00001", "rc1")

    mock_data.commit.assert_not_awaited()
    mock_data.rollback.assert_awaited_once()
    mock_data.revision.assert_called_once_with(release_key="proj-1.0", number="00001")


@pytest.mark.asyncio
async def test_set_tag_updates_untagged_revision():
    mock_data = mock.AsyncMock()
    update_result = mock.MagicMock()
    update_result.rowcount = 1
    mock_data.execute = mock.AsyncMock(return_value=update_result)
    _attach_active_release(mock_data)

    writer, mock_write_as = _make_revision_writer_pair(mock_data)
    await writer.set_tag(safe.ProjectKey("proj"), safe.VersionKey("1.0"), "00001", "rc1")

    mock_data.commit.assert_awaited_once()
    mock_write_as.append_to_audit_log.assert_called_once_with(
        asf_uid="testuser",
        project_key="proj",
        version_key="1.0",
        revision_number="00001",
        tag="rc1",
    )


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
            "_build_file_entries",
            new_callable=mock.AsyncMock,
            return_value=ok_entries,
        ),
        mock.patch.object(
            quarantine,
            "_extract_archives",
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
    mock_mark.assert_awaited_once_with(row, ok_entries, "Archive extraction failed: Extraction failure")
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
            "_build_file_entries",
            new_callable=mock.AsyncMock,
            return_value=ok_entries,
        ),
        mock.patch.object(quarantine, "_extract_archives", new_callable=mock.AsyncMock),
        mock.patch.object(quarantine, "_promote", new_callable=mock.AsyncMock) as mock_promote,
        mock.patch.object(quarantine, "_mark_failed", new_callable=mock.AsyncMock) as mock_mark,
    ):
        result = await quarantine.validate(
            {"quarantined_id": 1, "archives": [{"rel_path": "ok.tar.gz", "content_hash": "abc"}]}
        )

    assert result is None
    mock_promote.assert_awaited_once_with(
        row, safe.ProjectKey("proj"), safe.VersionKey("1.0"), row.release.key, str(quarantine_dir)
    )
    mock_mark.assert_not_awaited()


def _attach_active_release(mock_data: mock.AsyncMock) -> None:
    active_project = mock.MagicMock(status=sql.ProjectStatus.ACTIVE)
    release = mock.MagicMock(project=active_project)
    release_query = mock.MagicMock()
    release_query.demand = mock.AsyncMock(return_value=release)
    mock_data.release = mock.MagicMock(return_value=release_query)


def _make_quarantined_row() -> mock.MagicMock:
    row = mock.MagicMock(spec=sql.Quarantined)
    row.id = 1
    row.status = sql.QuarantineStatus.PENDING
    row.release = mock.MagicMock()
    row.release.key = "proj-1.0"
    row.release.safe_key = safe.ReleaseKey(row.release.key)
    row.release.project_key = "proj"
    row.release.safe_project_key = safe.ProjectKey(row.release.project_key)
    row.release.version = "1.0"
    row.release.safe_version_key = safe.VersionKey(row.release.version)
    return row


def _make_revision_writer(mock_data: mock.AsyncMock) -> revision.CommitteeParticipant:
    writer, _mock_write_as = _make_revision_writer_pair(mock_data)
    return writer


def _make_revision_writer_pair(mock_data: mock.AsyncMock) -> tuple[revision.CommitteeParticipant, mock.MagicMock]:
    mock_write = mock.MagicMock(spec=storage.Write)
    mock_write.authorisation.asf_uid = "testuser"
    mock_write_as = mock.MagicMock(spec=storage.WriteAsCommitteeParticipant)
    writer = revision.CommitteeParticipant(mock_write, mock_write_as, mock_data, "committee")
    return writer, mock_write_as


def _make_session_returning(quarantined_row: mock.MagicMock) -> mock.AsyncMock:
    mock_data = mock.AsyncMock()
    mock_query = mock.MagicMock()
    mock_query.get = mock.AsyncMock(return_value=quarantined_row)
    mock_data.quarantined = mock.MagicMock(return_value=mock_query)
    mock_data.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_data.__aexit__ = mock.AsyncMock(return_value=False)
    return mock_data


def _tar_hardlink(name: str, link_target: str) -> TarEntry:
    return ("hardlink", name, link_target)


def _tar_regular_file(name: str, data: bytes) -> TarEntry:
    return ("file", name, data)


def _tar_symlink(name: str, link_target: str) -> TarEntry:
    return ("symlink", name, link_target)


def _write_tar_gz(archive_path: pathlib.Path, members: list[TarEntry]) -> None:
    with tarfile.open(archive_path, "w:gz") as archive:
        for member_type, member_name, member_data in members:
            if member_type == "file":
                if not isinstance(member_data, bytes):
                    raise ValueError("Tar regular file data must be bytes")
                info = tarfile.TarInfo(member_name)
                info.size = len(member_data)
                archive.addfile(info, io.BytesIO(member_data))
                continue
            if member_type == "symlink":
                if not isinstance(member_data, str):
                    raise ValueError("Tar symlink data must be a path string")
                info = tarfile.TarInfo(member_name)
                info.type = tarfile.SYMTYPE
                info.linkname = member_data
                archive.addfile(info)
                continue
            if member_type == "hardlink":
                if not isinstance(member_data, str):
                    raise ValueError("Tar hardlink data must be a path string")
                info = tarfile.TarInfo(member_name)
                info.type = tarfile.LNKTYPE
                info.linkname = member_data
                archive.addfile(info)
                continue
            raise ValueError(f"Unsupported tar member type: {member_type}")
