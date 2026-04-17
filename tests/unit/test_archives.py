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
import zipfile

import pytest

import atr.archives as archives
import atr.models.safe as safe


def test_extract_tar_gz_returns_total_size_and_empty_paths_by_default(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.tar.gz"
    _make_tar_gz(archive_path, [("a.txt", b"aaaa"), ("dir/b.txt", b"bbbbbb")])
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    total, extracted = archives.extract(
        safe.StatePath(archive_path),
        str(extract_dir),
        max_size=1024 * 1024,
        chunk_size=1024,
    )

    assert total == 10
    assert extracted == []
    assert (extract_dir / "a.txt").read_bytes() == b"aaaa"
    assert (extract_dir / "dir" / "b.txt").read_bytes() == b"bbbbbb"


def test_extract_zip_returns_total_size_and_empty_paths_by_default(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.zip"
    _make_zip(archive_path, [("a.txt", b"aaaa"), ("dir/b.txt", b"bbbbbb")])
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    total, extracted = archives.extract(
        safe.StatePath(archive_path),
        str(extract_dir),
        max_size=1024 * 1024,
        chunk_size=1024,
    )

    assert total == 10
    assert extracted == []
    assert (extract_dir / "a.txt").read_bytes() == b"aaaa"


def test_extract_tar_gz_track_files_records_matching_basenames(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.tar.gz"
    _make_tar_gz(archive_path, [("root/pom.xml", b"<x/>"), ("root/README.md", b"r"), ("root/src/x.java", b"j")])
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    extracted = archives.extract(
        safe.StatePath(archive_path),
        str(extract_dir),
        max_size=1024 * 1024,
        chunk_size=1024,
        track_files={"pom.xml"},
    )[1]

    assert extracted == ["root/pom.xml"]


def test_extract_zip_track_files_records_matching_basenames(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.zip"
    _make_zip(archive_path, [("root/pom.xml", b"<x/>"), ("root/README.md", b"r")])
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    extracted = archives.extract(
        safe.StatePath(archive_path),
        str(extract_dir),
        max_size=1024 * 1024,
        chunk_size=1024,
        track_files={"pom.xml"},
    )[1]

    assert extracted == ["root/pom.xml"]


def test_extract_preserves_dot_underscore_metadata_files_tar(tmp_path: pathlib.Path) -> None:
    # Exarch faithfully extracts AppleDouble (._*) metadata members.
    # The previous hand-rolled implementation silently dropped them; quarantine
    # extraction has always retained them, so this aligns SBOM with quarantine.
    archive_path = tmp_path / "sample.tar.gz"
    _make_tar_gz(archive_path, [("a.txt", b"aaaa"), ("._a.txt", b"metadata")])
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    total, _ = archives.extract(
        safe.StatePath(archive_path),
        str(extract_dir),
        max_size=1024 * 1024,
        chunk_size=1024,
    )

    assert total == 12
    assert (extract_dir / "a.txt").exists()
    assert (extract_dir / "._a.txt").exists()


def test_extract_raises_when_exceeding_max_size(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.tar.gz"
    _make_tar_gz(archive_path, [("big.bin", b"x" * 5000)])
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()

    with pytest.raises(archives.ExtractionError):
        archives.extract(
            safe.StatePath(archive_path),
            str(extract_dir),
            max_size=1000,
            chunk_size=1024,
        )


@pytest.mark.asyncio
async def test_list_archive_tar_gz_returns_sorted_member_names(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.tar.gz"
    _make_tar_gz(archive_path, [("b.txt", b"b"), ("a.txt", b"a"), ("dir/c.txt", b"c")])

    result = await archives.list_archive(safe.StatePath(archive_path))

    assert result == ["a.txt", "b.txt", "dir/c.txt"]


@pytest.mark.asyncio
async def test_list_archive_tgz_extension_is_recognised(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.tgz"
    _make_tar_gz(archive_path, [("a.txt", b"a")])

    result = await archives.list_archive(safe.StatePath(archive_path))

    assert result == ["a.txt"]


@pytest.mark.asyncio
async def test_list_archive_zip_returns_sorted_member_names(tmp_path: pathlib.Path) -> None:
    archive_path = tmp_path / "sample.zip"
    _make_zip(archive_path, [("b.txt", b"b"), ("a.txt", b"a")])

    result = await archives.list_archive(safe.StatePath(archive_path))

    assert result == ["a.txt", "b.txt"]


@pytest.mark.asyncio
async def test_list_archive_returns_none_for_missing_file(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nope.tar.gz"

    # StatePath requires the root to exist; use tmp_path as root to keep the path valid.
    result = await archives.list_archive(safe.StatePath(missing, root=tmp_path))

    assert result is None


@pytest.mark.asyncio
async def test_list_archive_returns_none_for_unsupported_extension(tmp_path: pathlib.Path) -> None:
    other = tmp_path / "sample.rar"
    other.write_bytes(b"not an archive")

    result = await archives.list_archive(safe.StatePath(other))

    assert result is None


@pytest.mark.asyncio
async def test_list_archive_returns_none_for_corrupt_archive(tmp_path: pathlib.Path) -> None:
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not actually a zip")

    result = await archives.list_archive(safe.StatePath(corrupt))

    assert result is None


def _make_tar_gz(path: pathlib.Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _make_zip(path: pathlib.Path, members: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
