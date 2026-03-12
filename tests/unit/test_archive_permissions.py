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

import os
import pathlib
import stat

import atr.tasks.quarantine as quarantine


def test_set_archive_permissions_locks_files_and_directories(tmp_path: pathlib.Path) -> None:
    archive_dir = tmp_path / "extracted"
    nested_dir = archive_dir / "src" / "main"
    nested_dir.mkdir(parents=True)
    (archive_dir / "README.txt").write_text("hello")
    (nested_dir / "App.java").write_text("class App {}")

    quarantine._set_archive_permissions(archive_dir)

    assert stat.S_IMODE(archive_dir.stat().st_mode) == 0o555
    assert stat.S_IMODE((archive_dir / "src").stat().st_mode) == 0o555
    assert stat.S_IMODE(nested_dir.stat().st_mode) == 0o555
    assert stat.S_IMODE((archive_dir / "README.txt").stat().st_mode) == 0o444
    assert stat.S_IMODE((nested_dir / "App.java").stat().st_mode) == 0o444


def test_set_archive_permissions_repairs_world_writable(tmp_path: pathlib.Path) -> None:
    archive_dir = tmp_path / "extracted"
    archive_dir.mkdir()
    file_path = archive_dir / "file.txt"
    file_path.write_text("content")
    os.chmod(file_path, 0o666)
    os.chmod(archive_dir, 0o777)

    quarantine._set_archive_permissions(archive_dir)

    assert stat.S_IMODE(file_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(archive_dir.stat().st_mode) == 0o555
