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

import pathlib

import atr.util as util


def write_tree(root: pathlib.Path, files: dict[str, bytes]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


async def test_tree_differences_content(tmp_path: pathlib.Path) -> None:
    write_tree(tmp_path / "base", {"a.txt": b"same size", "b.txt": b"short"})
    write_tree(tmp_path / "other", {"a.txt": b"SAME SIZE", "b.txt": b"much longer"})

    differences = await util.tree_differences(tmp_path / "base", tmp_path / "other")

    assert differences.only_in_base == []
    assert differences.only_in_other == []
    assert differences.differing == ["a.txt", "b.txt"]
    assert differences.identical is False


async def test_tree_differences_identical(tmp_path: pathlib.Path) -> None:
    files = {"a.txt": b"alpha", "sub/dir/b.bin": b"\x00\x01"}
    write_tree(tmp_path / "base", files)
    write_tree(tmp_path / "other", files)
    (tmp_path / "other" / "empty").mkdir()

    differences = await util.tree_differences(tmp_path / "base", tmp_path / "other")

    assert differences.identical is True


async def test_tree_differences_membership(tmp_path: pathlib.Path) -> None:
    write_tree(tmp_path / "base", {"shared.txt": b"shared", "sub/base-only.txt": b"base"})
    write_tree(tmp_path / "other", {"shared.txt": b"shared", "other-only.txt": b"other"})

    differences = await util.tree_differences(tmp_path / "base", tmp_path / "other")

    assert differences.only_in_base == ["sub/base-only.txt"]
    assert differences.only_in_other == ["other-only.txt"]
    assert differences.differing == []
    assert differences.identical is False


async def test_tree_differences_symlink(tmp_path: pathlib.Path) -> None:
    write_tree(tmp_path / "base", {"a.txt": b"alpha", "target.txt": b"alpha"})
    write_tree(tmp_path / "other", {"target.txt": b"alpha"})
    (tmp_path / "other" / "a.txt").symlink_to(tmp_path / "other" / "target.txt")

    differences = await util.tree_differences(tmp_path / "base", tmp_path / "other")

    assert differences.differing == ["a.txt"]
    assert differences.identical is False
