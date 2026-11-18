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

import exarch

import atr.archives as archives
import atr.models.safe as safe


def test_compare_trees_reports_each_kind_of_difference(tmp_path: pathlib.Path) -> None:
    tar_path = tmp_path / "example-1.0.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        _add_tar_member(archive, "example-1.0/same.txt", b"same\n", 0o644)
        _add_tar_member(archive, "example-1.0/changed.txt", b"left\n", 0o644)
        _add_tar_member(archive, "example-1.0/left.txt", b"left\n", 0o644)
        _add_tar_member(archive, "example-1.0/run.sh", b"#!/bin/sh\n", 0o755)
    zip_path = tmp_path / "example-1.0.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        _add_zip_member(archive, "example-1.0/same.txt", b"same\n", 0o644)
        _add_zip_member(archive, "example-1.0/changed.txt", b"right\n", 0o644)
        _add_zip_member(archive, "example-1.0/right.txt", b"right\n", 0o644)
        _add_zip_member(archive, "example-1.0/run.sh", b"#!/bin/sh\n", 0o644)

    comparison = archives.compare_trees(
        safe.StatePath(tar_path), _extract(tar_path), safe.StatePath(zip_path), _extract(zip_path)
    )

    assert comparison == archives.TreeComparison(
        differing=[
            ("changed.txt", archives.MemberDifference.CONTENTS),
            ("left.txt", archives.MemberDifference.ONLY_LEFT),
            ("right.txt", archives.MemberDifference.ONLY_RIGHT),
            ("run.sh", archives.MemberDifference.EXECUTABLE),
        ],
        matching=["same.txt"],
    )


def _add_tar_member(archive: tarfile.TarFile, name: str, data: bytes, mode: int) -> None:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def _add_zip_member(archive: zipfile.ZipFile, name: str, data: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (0o100000 | mode) << 16
    archive.writestr(info, data)


def _extract(archive_path: pathlib.Path) -> safe.StatePath:
    extract_dir = archive_path.with_name(f"{archive_path.name}.extracted")
    extract_dir.mkdir()
    exarch.extract_archive(str(archive_path), str(extract_dir), archives.extraction_config())
    return safe.StatePath(extract_dir)
