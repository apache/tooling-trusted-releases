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

import atr.detection as detection
import atr.models.attestable as models

type TarArchiveEntry = tuple[str, str, bytes | str]


def test_check_archive_safety_accepts_dotenv_anywhere_in_tar_and_zip(tmp_path):
    tar_path = tmp_path / "safe-dotenv.tar.gz"
    _write_tar_gz(
        tar_path,
        [
            _tar_regular_file(".env", b"ATR_STATUS=ALPHA\n"),
            _tar_regular_file("config/.env", b"SECRET=value\n"),
        ],
    )
    zip_path = tmp_path / "safe-dotenv.zip"
    _write_zip(
        zip_path,
        [
            (".env", b"ATR_STATUS=ALPHA\n"),
            ("config/.env", b"SECRET=value\n"),
        ],
    )

    assert detection.check_archive_safety(str(tar_path)) == []
    assert detection.check_archive_safety(str(zip_path)) == []


def test_check_archive_safety_accepts_safe_tar_gz(tmp_path):
    archive_path = tmp_path / "safe.tar.gz"
    _write_tar_gz(
        archive_path,
        [
            _tar_regular_file("dist/apache-widget-1.0-src.tar.gz", b"payload"),
            _tar_regular_file("docs/readme.txt", b"hello"),
        ],
    )

    assert detection.check_archive_safety(str(archive_path)) == []


def test_check_archive_safety_accepts_safe_zip(tmp_path):
    archive_path = tmp_path / "safe.zip"
    _write_zip(
        archive_path,
        [
            ("dist/apache-widget-1.0-src.zip", b"payload"),
            ("docs/readme.txt", b"hello"),
        ],
    )

    assert detection.check_archive_safety(str(archive_path)) == []


def test_check_archive_safety_rejects_absolute_paths_in_tar_and_zip(tmp_path):
    tar_path = tmp_path / "unsafe-absolute.tar.gz"
    _write_tar_gz(
        tar_path,
        [
            _tar_regular_file("/absolute.txt", b"x"),
        ],
    )
    zip_path = tmp_path / "unsafe-absolute.zip"
    _write_zip(
        zip_path,
        [
            ("/absolute.txt", b"x"),
        ],
    )

    tar_errors = detection.check_archive_safety(str(tar_path))
    zip_errors = detection.check_archive_safety(str(zip_path))

    assert any("/absolute.txt" in error for error in tar_errors)
    assert any("path traversal" in error for error in tar_errors)
    assert any("/absolute.txt" in error for error in zip_errors)
    assert any("path traversal" in error for error in zip_errors)


def test_check_archive_safety_rejects_hardlink_target_outside_root(tmp_path):
    archive_path = tmp_path / "unsafe-hardlink.tar.gz"
    _write_tar_gz(
        archive_path,
        [
            _tar_regular_file("dist/file.txt", b"ok"),
            _tar_hardlink("dist/hard", "../../outside.txt"),
        ],
    )

    errors = detection.check_archive_safety(str(archive_path))

    assert any("dist/hard" in error for error in errors)
    assert any("escapes root" in error for error in errors)


def test_check_archive_safety_rejects_hardlink_target_resolved_from_root(tmp_path):
    archive_path = tmp_path / "unsafe-hardlink-depth.tar.gz"
    _write_tar_gz(
        archive_path,
        [
            _tar_regular_file("a/b/file.txt", b"ok"),
            _tar_hardlink("a/b/link", "../secret"),
        ],
    )

    errors = detection.check_archive_safety(str(archive_path))

    assert any("a/b/link" in error for error in errors)
    assert any("escapes root" in error for error in errors)


def test_check_archive_safety_rejects_parent_path_traversal_in_tar_and_zip(tmp_path):
    tar_path = tmp_path / "unsafe-parent.tar.gz"
    _write_tar_gz(
        tar_path,
        [
            _tar_regular_file("../outside.txt", b"x"),
        ],
    )
    zip_path = tmp_path / "unsafe-parent.zip"
    _write_zip(
        zip_path,
        [
            ("../outside.txt", b"x"),
        ],
    )

    tar_errors = detection.check_archive_safety(str(tar_path))
    zip_errors = detection.check_archive_safety(str(zip_path))

    assert any("../outside.txt" in error for error in tar_errors)
    assert any("path traversal" in error for error in tar_errors)
    assert any("../outside.txt" in error for error in zip_errors)
    assert any("path traversal" in error for error in zip_errors)


def test_check_archive_safety_rejects_symlink_target_outside_root(tmp_path):
    archive_path = tmp_path / "unsafe-symlink.tar.gz"
    _write_tar_gz(
        archive_path,
        [
            _tar_regular_file("dist/file.txt", b"ok"),
            _tar_symlink("dist/link", "../../outside.txt"),
        ],
    )

    errors = detection.check_archive_safety(str(archive_path))

    assert any("dist/link" in error for error in errors)
    assert any("escapes root" in error for error in errors)


def test_deduplicate_quarantine_archives_different_extensions_kept_separately():
    paths_list = ["a/src.tar.gz", "a/src.zip"]
    path_to_hash = {"a/src.tar.gz": "h1", "a/src.zip": "h1"}

    result = detection.deduplicate_quarantine_archives(paths_list, path_to_hash)

    assert len(result) == 2


def test_deduplicate_quarantine_archives_empty_input():
    result = detection.deduplicate_quarantine_archives([], {})

    assert result == []


def test_deduplicate_quarantine_archives_keeps_smallest_rel_path_per_hash_suffix():
    paths_list = ["b/archive.tar.gz", "a/archive.tar.gz", "c/other.zip"]
    path_to_hash = {
        "a/archive.tar.gz": "h1",
        "b/archive.tar.gz": "h1",
        "c/other.zip": "h2",
    }

    result = detection.deduplicate_quarantine_archives(paths_list, path_to_hash)

    assert result == [("a/archive.tar.gz", "h1"), ("c/other.zip", "h2")]


def test_deduplicate_quarantine_archives_tgz_normalises_to_tar_gz():
    paths_list = ["a/src.tgz", "b/src.tar.gz"]
    path_to_hash = {"a/src.tgz": "h1", "b/src.tar.gz": "h1"}

    result = detection.deduplicate_quarantine_archives(paths_list, path_to_hash)

    assert len(result) == 1
    assert result[0][1] == "h1"


def test_detect_archives_requiring_quarantine_known_hash_and_different_extension():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tgz": "h1"},
        hashes={"h1": models.HashEntry(size=100, uploaders=[("alice", "00001")], basenames=["old-src.tgz"])},
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0.zip": "h1"},
        previous_attestable=previous,
    )

    assert result == ["dist/apache-widget-1.0.zip"]


def test_detect_archives_requiring_quarantine_known_hash_and_same_extension():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        hashes={
            "h1": models.HashEntry(
                size=100,
                uploaders=[("alice", "00001")],
                basenames=["apache-widget-0.9-src.tar.gz"],
            )
        },
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        previous_attestable=previous,
    )

    assert result == []


def test_detect_archives_requiring_quarantine_missing_historical_basenames():
    hash_entry = models.HashEntry(size=100, uploaders=[("alice", "00001")])
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        hashes={"h1": hash_entry},
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.1-src.tar.gz": "h1"},
        previous_attestable=previous,
    )

    assert "basenames" not in hash_entry.model_fields_set
    assert result == ["dist/apache-widget-1.1-src.tar.gz"]


def test_detect_archives_requiring_quarantine_new_hash_new_extension():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h_old"},
        hashes={"h_old": models.HashEntry(size=100, uploaders=[("alice", "00001")], basenames=["old-src.tar.gz"])},
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.1.zip": "h_new"},
        previous_attestable=previous,
    )

    assert result == ["dist/apache-widget-1.1.zip"]


def test_detect_archives_requiring_quarantine_no_previous_attestable():
    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        previous_attestable=None,
    )

    assert result == ["dist/apache-widget-1.0-src.tar.gz"]


def test_detect_archives_requiring_quarantine_non_archive_files_are_ignored():
    previous = models.AttestableV1(paths={}, hashes={}, policy={})

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/README.md": "h1", "dist/KEYS": "h2"},
        previous_attestable=previous,
    )

    assert result == []


def test_detect_archives_requiring_quarantine_tgz_and_tar_gz_are_equivalent():
    previous = models.AttestableV1(
        paths={"dist/apache-widget-1.0-src.tar.gz": "h1"},
        hashes={
            "h1": models.HashEntry(
                size=100,
                uploaders=[("alice", "00001")],
                basenames=["apache-widget-1.0-src.tar.gz"],
            )
        },
        policy={},
    )

    result = detection.detect_archives_requiring_quarantine(
        path_to_hash={"dist/apache-widget-1.0-src.tgz": "h1"},
        previous_attestable=previous,
    )

    assert result == []


def _tar_hardlink(name: str, link_target: str) -> TarArchiveEntry:
    return ("hardlink", name, link_target)


def _tar_regular_file(name: str, data: bytes) -> TarArchiveEntry:
    return ("file", name, data)


def _tar_symlink(name: str, link_target: str) -> TarArchiveEntry:
    return ("symlink", name, link_target)


def _write_tar_gz(archive_path: pathlib.Path, members: list[TarArchiveEntry]) -> None:
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


def _write_zip(archive_path: pathlib.Path, members: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(archive_path, "w") as archive:
        for member_name, member_data in members:
            archive.writestr(member_name, member_data)
