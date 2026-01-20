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
import tempfile

import atr.util as util


def test_chmod_files_does_not_change_directory_permissions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        os.chmod(subdir, 0o700)
        test_file = subdir / "test.txt"
        test_file.write_text("content")

        util.chmod_files(tmp_path, 0o444)

        dir_mode = stat.S_IMODE(subdir.stat().st_mode)
        assert dir_mode == 0o700


def test_chmod_files_handles_empty_directory():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        util.chmod_files(tmp_path, 0o444)


def test_chmod_files_handles_multiple_files():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        files = [tmp_path / f"file{i}.txt" for i in range(5)]
        for f in files:
            f.write_text("content")
            os.chmod(f, 0o644)

        util.chmod_files(tmp_path, 0o400)

        for f in files:
            file_mode = stat.S_IMODE(f.stat().st_mode)
            assert file_mode == 0o400


def test_chmod_files_handles_nested_directories():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        nested_dir = tmp_path / "subdir" / "nested"
        nested_dir.mkdir(parents=True)
        file1 = tmp_path / "root.txt"
        file2 = tmp_path / "subdir" / "mid.txt"
        file3 = nested_dir / "deep.txt"
        for f in [file1, file2, file3]:
            f.write_text("content")
            os.chmod(f, 0o644)

        util.chmod_files(tmp_path, 0o444)

        for f in [file1, file2, file3]:
            file_mode = stat.S_IMODE(f.stat().st_mode)
            assert file_mode == 0o444


def test_chmod_files_sets_custom_permissions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        os.chmod(test_file, 0o644)

        util.chmod_files(tmp_path, 0o400)

        file_mode = stat.S_IMODE(test_file.stat().st_mode)
        assert file_mode == 0o400


def test_chmod_files_sets_default_permissions():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = pathlib.Path(tmp_dir)
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        os.chmod(test_file, 0o644)

        util.chmod_files(tmp_path, 0o444)

        file_mode = stat.S_IMODE(test_file.stat().st_mode)
        assert file_mode == 0o444
