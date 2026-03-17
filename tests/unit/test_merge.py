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
import unittest.mock as mock

import pytest

import atr.merge as merge
import atr.util as util


@pytest.mark.asyncio
async def test_case_09_prior_adds_file(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (prior_dir / "added.txt").write_text("new content")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes: dict[str, str] = {}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes: dict[str, str] = {}
    n_sizes: dict[str, int] = {}

    mock_prior_hashes = {"added.txt": "blake3:abc123"}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value=mock_prior_hashes):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "added.txt").exists()
    assert (temp_dir / "added.txt").read_text() == "new content"
    assert n_hashes["added.txt"] == "blake3:abc123"
    assert n_sizes["added.txt"] == len("new content")


@pytest.mark.asyncio
async def test_case_09_prior_adds_file_in_subdirectory(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (prior_dir / "apple").mkdir()
    (prior_dir / "apple" / "banana.txt").write_text("nested")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes: dict[str, str] = {}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes: dict[str, str] = {}
    n_sizes: dict[str, int] = {}

    mock_prior_hashes = {"apple/banana.txt": "blake3:xyz890"}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value=mock_prior_hashes):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "apple" / "banana.txt").exists()
    assert n_hashes["apple/banana.txt"] == "blake3:xyz890"


@pytest.mark.asyncio
async def test_case_10_prior_deletion_via_hash(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (base_dir / "removed.txt").write_text("same content")
    (temp_dir / "removed.txt").write_text("same content")

    base_inodes = util.paths_to_inodes(base_dir)
    n_inodes = util.paths_to_inodes(temp_dir)

    assert base_inodes["removed.txt"] != n_inodes["removed.txt"]

    base_hashes = {"removed.txt": "blake3:matching"}
    n_hashes = {"removed.txt": "blake3:matching"}
    n_sizes = {"removed.txt": len("same content")}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value={}):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert not (temp_dir / "removed.txt").exists()
    assert "removed.txt" not in n_hashes
    assert "removed.txt" not in n_sizes


@pytest.mark.asyncio
async def test_case_10_prior_deletion_via_inode(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (base_dir / "removed.txt").write_text("to be deleted")
    os.link(base_dir / "removed.txt", temp_dir / "removed.txt")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes = {"removed.txt": "blake3:aaa"}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes = {"removed.txt": "blake3:aaa"}
    n_sizes = {"removed.txt": len("to be deleted")}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value={}):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert not (temp_dir / "removed.txt").exists()
    assert "removed.txt" not in n_hashes
    assert "removed.txt" not in n_sizes


@pytest.mark.asyncio
async def test_case_11_prior_replacement_via_hash(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (base_dir / "shared.txt").write_text("original")
    (temp_dir / "shared.txt").write_text("original")
    (prior_dir / "shared.txt").write_text("updated by prior")

    base_inodes = util.paths_to_inodes(base_dir)
    n_inodes = util.paths_to_inodes(temp_dir)
    prior_inodes = util.paths_to_inodes(prior_dir)

    assert base_inodes["shared.txt"] != n_inodes["shared.txt"]
    assert base_inodes["shared.txt"] != prior_inodes["shared.txt"]
    assert n_inodes["shared.txt"] != prior_inodes["shared.txt"]

    base_hashes = {"shared.txt": "blake3:original"}
    n_hashes = {"shared.txt": "blake3:original"}
    n_sizes = {"shared.txt": len("original")}

    mock_prior_hashes = {"shared.txt": "blake3:updated"}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value=mock_prior_hashes):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "shared.txt").read_text() == "updated by prior"
    assert n_hashes["shared.txt"] == "blake3:updated"
    assert n_sizes["shared.txt"] == len("updated by prior")


@pytest.mark.asyncio
async def test_case_11_prior_replacement_via_inode(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (base_dir / "shared.txt").write_text("original")
    os.link(base_dir / "shared.txt", temp_dir / "shared.txt")
    (prior_dir / "shared.txt").write_text("updated by prior")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes = {"shared.txt": "blake3:original"}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes = {"shared.txt": "blake3:original"}
    n_sizes = {"shared.txt": len("original")}

    mock_prior_hashes = {"shared.txt": "blake3:updated"}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value=mock_prior_hashes):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "shared.txt").read_text() == "updated by prior"
    assert n_hashes["shared.txt"] == "blake3:updated"
    assert n_sizes["shared.txt"] == len("updated by prior")


@pytest.mark.asyncio
async def test_case_13_new_wins_when_prior_deletes(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (base_dir / "modified.txt").write_text("original")
    (temp_dir / "modified.txt").write_text("new content")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes = {"modified.txt": "blake3:original"}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes = {"modified.txt": "blake3:new"}
    n_sizes = {"modified.txt": len("new content")}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value={}):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "modified.txt").exists()
    assert (temp_dir / "modified.txt").read_text() == "new content"
    assert n_hashes["modified.txt"] == "blake3:new"
    assert n_sizes["modified.txt"] == len("new content")


@pytest.mark.asyncio
async def test_noop_when_base_and_prior_agree(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (base_dir / "unchanged.txt").write_text("same")
    os.link(base_dir / "unchanged.txt", prior_dir / "unchanged.txt")
    (temp_dir / "unchanged.txt").write_text("modified by new")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes = {"unchanged.txt": "blake3:same"}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes = {"unchanged.txt": "blake3:modified"}
    n_sizes = {"unchanged.txt": len("modified by new")}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value={}) as mock_load:
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )
        mock_load.assert_not_awaited()

    assert (temp_dir / "unchanged.txt").read_text() == "modified by new"
    assert n_hashes["unchanged.txt"] == "blake3:modified"


@pytest.mark.asyncio
async def test_type_conflict_prior_file_vs_new_directory(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (prior_dir / "docs").write_text("a file in prior")
    (temp_dir / "docs").mkdir()
    (temp_dir / "docs" / "guide.txt").write_text("a file under a directory in new")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes: dict[str, str] = {}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes = {"docs/guide.txt": "blake3:guide"}
    n_sizes = {"docs/guide.txt": len("a file under a directory in new")}

    with mock.patch("atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value={"docs": "blake3:docs"}):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "docs").is_dir()
    assert (temp_dir / "docs" / "guide.txt").read_text() == "a file under a directory in new"
    assert "docs" not in n_hashes
    assert n_hashes["docs/guide.txt"] == "blake3:guide"


@pytest.mark.asyncio
async def test_type_conflict_prior_file_vs_new_empty_directory(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (prior_dir / "empty").write_text("a file in prior")
    (temp_dir / "empty").mkdir()

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes: dict[str, str] = {}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes: dict[str, str] = {}
    n_sizes: dict[str, int] = {}

    with mock.patch(
        "atr.merge._prior_hashes_load", new_callable=mock.AsyncMock, return_value={"empty": "blake3:empty"}
    ):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "empty").is_dir()
    assert "empty" not in n_hashes


@pytest.mark.asyncio
async def test_type_conflict_prior_subdir_vs_new_file(tmp_path: pathlib.Path):
    base_dir, prior_dir, temp_dir = _setup_dirs(tmp_path)

    (prior_dir / "docs").mkdir()
    (prior_dir / "docs" / "guide.txt").write_text("a file under a directory in prior")
    (temp_dir / "docs").write_text("a file in new")

    base_inodes = util.paths_to_inodes(base_dir)
    base_hashes: dict[str, str] = {}
    n_inodes = util.paths_to_inodes(temp_dir)
    n_hashes = {"docs": "blake3:docs"}
    n_sizes = {"docs": len("a file in new")}

    with mock.patch(
        "atr.merge._prior_hashes_load",
        new_callable=mock.AsyncMock,
        return_value={"docs/guide.txt": "blake3:guide"},
    ):
        await merge.merge(
            mock.AsyncMock(),
            base_inodes,
            base_hashes,
            prior_dir,
            "proj",
            "ver",
            2,
            temp_dir,
            n_inodes,
            n_hashes,
            n_sizes,
        )

    assert (temp_dir / "docs").is_file()
    assert (temp_dir / "docs").read_text() == "a file in new"
    assert n_hashes["docs"] == "blake3:docs"
    assert "docs/guide.txt" not in n_hashes


def _setup_dirs(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    base_dir = tmp_path / "base"
    prior_dir = tmp_path / "prior"
    temp_dir = tmp_path / "new"
    base_dir.mkdir()
    prior_dir.mkdir()
    temp_dir.mkdir()
    return base_dir, prior_dir, temp_dir
