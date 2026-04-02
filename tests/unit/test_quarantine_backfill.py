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
import stat
import tarfile

import pytest

import atr.hashes as hashes
import atr.models.safe as safe
import atr.tasks.quarantine as quarantine


def test_backfill_already_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    revision_dir = unfinished_dir / "proj" / "1.0" / "00001"
    revision_dir.path.mkdir(parents=True)
    archive_path = revision_dir / "artifact.tar.gz"
    _create_tar_gz(archive_path)

    content_hash = hashes.compute_file_hash_sync(archive_path)
    cache_key = hashes.filesystem_archives_key(content_hash)
    existing_cache = cache_dir / "proj" / "1.0" / cache_key
    existing_cache.path.mkdir(parents=True)

    result = quarantine.backfill_archive_cache()

    assert result == []


def test_backfill_continues_after_extraction_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    revision_dir = unfinished_dir / "proj" / "1.0" / "00001"
    revision_dir.path.mkdir(parents=True)
    (revision_dir / "bad.tar.gz").path.write_bytes(b"not a valid archive")
    _create_tar_gz(revision_dir / "good.tar.gz")

    result = quarantine.backfill_archive_cache()

    assert len(result) == 1
    assert "good.tar.gz" in result[0][0]

    good_hash = hashes.compute_file_hash_sync(revision_dir / "good.tar.gz")
    good_cache = cache_dir / "proj" / "1.0" / hashes.filesystem_archives_key(good_hash)
    assert good_cache.path.is_dir()

    bad_hash = hashes.compute_file_hash_sync(revision_dir / "bad.tar.gz")
    bad_cache = cache_dir / "proj" / "1.0" / hashes.filesystem_archives_key(bad_hash)
    assert not bad_cache.path.exists()


def test_backfill_deduplicates_within_same_version(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    revision_1 = unfinished_dir / "proj" / "1.0" / "00001"
    revision_1.path.mkdir(parents=True)
    _create_tar_gz(revision_1 / "artifact.tar.gz")

    revision_2 = unfinished_dir / "proj" / "1.0" / "00002"
    revision_2.path.mkdir(parents=True)
    (revision_2 / "artifact.tar.gz").path.write_bytes((revision_1 / "artifact.tar.gz").path.read_bytes())

    result = quarantine.backfill_archive_cache()

    assert len(result) == 1


def test_backfill_empty_unfinished_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    result = quarantine.backfill_archive_cache()

    assert result == []
    assert (tmp_path / "cache" / "archive-backfill.done").is_file()


def test_backfill_extracts_same_content_into_different_namespaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    revision_a = unfinished_dir / "projA" / "1.0" / "00001"
    revision_a.path.mkdir(parents=True)
    _create_tar_gz(revision_a / "artifact.tar.gz")

    revision_b = unfinished_dir / "projB" / "2.0" / "00001"
    revision_b.path.mkdir(parents=True)
    (revision_b / "artifact.tar.gz").path.write_bytes((revision_a / "artifact.tar.gz").path.read_bytes())

    result = quarantine.backfill_archive_cache()

    assert len(result) == 2

    content_hash = hashes.compute_file_hash_sync(revision_a / "artifact.tar.gz")
    cache_key = hashes.filesystem_archives_key(content_hash)
    assert (cache_dir / "projA" / "1.0" / cache_key).path.is_dir()
    assert (cache_dir / "projB" / "2.0" / cache_key).path.is_dir()


def test_backfill_extracts_uncached_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    revision_dir = unfinished_dir / "proj" / "1.0" / "00001"
    revision_dir.path.mkdir(parents=True)
    archive_path = revision_dir / "artifact.tar.gz"
    _create_tar_gz(archive_path)
    (revision_dir / "artifact.tar.gz.sha512").path.write_text("somehash  artifact.tar.gz")

    result = quarantine.backfill_archive_cache()

    assert len(result) == 1
    archive_path_str, result_cache_dir, duration = result[0]
    assert archive_path_str == str(archive_path)
    assert result_cache_dir.path.is_dir()
    assert (result_cache_dir / "README.txt").path.read_text() == "Hello"
    assert duration >= 0
    assert (tmp_path / "cache" / "archive-backfill.done").is_file()
    assert stat.S_IMODE(result_cache_dir.path.stat().st_mode) == 0o555
    assert stat.S_IMODE((result_cache_dir / "README.txt").path.stat().st_mode) == 0o444


def test_backfill_skips_non_archive_files(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    revision_dir = unfinished_dir / "proj" / "1.0" / "00001"
    revision_dir.path.mkdir(parents=True)
    (revision_dir / "artifact.tar.gz.sha512").path.write_text("somehash  artifact.tar.gz")
    (revision_dir / "artifact.tar.gz.asc").path.write_bytes(b"signature")

    result = quarantine.backfill_archive_cache()

    assert result == []


def test_backfill_skips_scan_when_done_file_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    unfinished_dir, cache_dir = _setup_dirs(tmp_path)
    _patch_paths(monkeypatch, tmp_path, unfinished_dir, cache_dir)

    done_file = tmp_path / "cache" / "archive-backfill.done"
    done_file.touch()

    def fail_if_called(*args, **kwargs) -> None:
        raise AssertionError("backfill scan should be skipped when the done file exists")

    monkeypatch.setattr(quarantine, "_backfill_revision", fail_if_called)

    result = quarantine.backfill_archive_cache()

    assert result == []


def _create_tar_gz(path: safe.StatePath) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="README.txt")
        content = b"Hello"
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    path.path.write_bytes(buf.getvalue())


def _patch_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    unfinished_dir: safe.StatePath,
    archives_dir: safe.StatePath,
) -> None:
    temp_dir = safe.StatePath(tmp_path)
    monkeypatch.setattr(quarantine.paths, "get_unfinished_dir", lambda: unfinished_dir)
    monkeypatch.setattr(quarantine.paths, "get_archives_dir", lambda: archives_dir)
    monkeypatch.setattr(quarantine.paths, "get_tmp_dir", lambda: temp_dir / "temporary")
    monkeypatch.setattr(quarantine, "_backfill_done_file", lambda: temp_dir / "cache" / "archive-backfill.done")


def _setup_dirs(tmp_path: pathlib.Path) -> tuple[safe.StatePath, safe.StatePath]:
    temp_dir = safe.StatePath(tmp_path)
    unfinished_dir = temp_dir / "unfinished"
    archives_dir = temp_dir / "archives"
    cache_dir = temp_dir / "cache"
    staging_dir = temp_dir / "temporary"
    for d in [unfinished_dir, archives_dir, cache_dir, staging_dir]:
        d.path.mkdir(parents=True)
    return unfinished_dir, archives_dir
