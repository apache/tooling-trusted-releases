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

import json
import pathlib
import unittest.mock as mock

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.tasks.checks.targz as targz
import atr.tasks.checks.zipformat as zipformat
import tests.unit.recorders as recorders


@pytest.mark.asyncio
async def test_targz_structure_accepts_npm_pack_root(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree_with_contents(
        tmp_path,
        {
            "package/package.json": json.dumps({"name": "example", "version": "1.2.3"}),
            "package/README.txt": "hello",
        },
    )
    recorder, args = await _targz_structure_args(tmp_path, "example-1.2.3.tgz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.NOTE.value for status, _, _ in recorder.messages)
    assert not any(
        status
        in {
            sql.CheckResultStatus.CONCERN.value,
            sql.CheckResultStatus.SUGGESTION.value,
            sql.CheckResultStatus.EXCEPTION.value,
        }
        for status, _, _ in recorder.messages
    )


@pytest.mark.asyncio
async def test_targz_structure_accepts_source_release_suffix_variant(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["executor-1.0.0/README.txt"])
    recorder, args = await _targz_structure_args(tmp_path, "executor-1.0.0-source-release.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.NOTE.value for status, _, _ in recorder.messages)
    assert not any(
        status
        in {
            sql.CheckResultStatus.CONCERN.value,
            sql.CheckResultStatus.SUGGESTION.value,
            sql.CheckResultStatus.EXCEPTION.value,
        }
        for status, _, _ in recorder.messages
    )


@pytest.mark.asyncio
async def test_targz_structure_accepts_source_suffix_variant(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3/README.txt"])
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3-source.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.NOTE.value for status, _, _ in recorder.messages)
    assert not any(
        status
        in {
            sql.CheckResultStatus.CONCERN.value,
            sql.CheckResultStatus.SUGGESTION.value,
            sql.CheckResultStatus.EXCEPTION.value,
        }
        for status, _, _ in recorder.messages
    )


@pytest.mark.asyncio
async def test_targz_structure_accepts_src_suffix_variant(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3/README.txt"])
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3-src.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.NOTE.value for status, _, _ in recorder.messages)
    assert not any(
        status
        in {
            sql.CheckResultStatus.CONCERN.value,
            sql.CheckResultStatus.SUGGESTION.value,
            sql.CheckResultStatus.EXCEPTION.value,
        }
        for status, _, _ in recorder.messages
    )


@pytest.mark.asyncio
async def test_targz_structure_fails_when_cache_unavailable(tmp_path: pathlib.Path) -> None:
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=None)):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.EXCEPTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)
    assert any("extracted archive tree is not available" in message.lower() for _, message, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_npm_pack_filename_mismatch(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree_with_contents(
        tmp_path,
        {
            "package/package.json": json.dumps({"name": "different", "version": "1.2.3"}),
            "package/README.txt": "hello",
        },
    )
    recorder, args = await _targz_structure_args(tmp_path, "example-1.2.3.tgz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.SUGGESTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)
    assert any("npm pack layout detected" in message for _, message, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_package_root_without_package_json(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree_with_contents(
        tmp_path,
        {
            "package/README.txt": "hello",
        },
    )
    recorder, args = await _targz_structure_args(tmp_path, "example-1.2.3.tgz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.SUGGESTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_source_root_when_filename_has_no_suffix(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3-source/README.txt"])
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.SUGGESTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_source_root_when_filename_has_src_suffix(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3-source/README.txt"])
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3-src.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.SUGGESTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_src_root_when_filename_has_no_suffix(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3-src/README.txt"])
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.SUGGESTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_src_root_when_filename_has_source_suffix(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3-src/README.txt"])
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3-source.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.SUGGESTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_symlink_root(tmp_path: pathlib.Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "apache-example-1.2.3").symlink_to(cache_dir / "missing-target")
    recorder, args = await _targz_structure_args(tmp_path, "apache-example-1.2.3.tar.gz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.EXCEPTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_targz_structure_rejects_symlinked_package_json(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree_with_contents(
        tmp_path,
        {
            "package/README.txt": "hello",
            "metadata.json": json.dumps({"name": "example", "version": "1.2.3"}),
        },
    )
    (cache_dir / "package" / "package.json").symlink_to(cache_dir / "metadata.json")
    recorder, args = await _targz_structure_args(tmp_path, "example-1.2.3.tgz")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await targz.structure(args)

    assert any(status == sql.CheckResultStatus.EXCEPTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)


def test_zipformat_structure_accepts_bin_suffix_variant(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-maven-3.9.15/README.txt"])

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "apache-maven-3.9.15-bin.zip")

    assert result.get("error") is None
    assert result.get("root_dir") == "apache-maven-3.9.15"


def test_zipformat_structure_accepts_npm_pack_root(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree_with_contents(
        tmp_path,
        {
            "package/package.json": json.dumps({"name": "example", "version": "1.2.3"}),
            "package/README.txt": "hello",
        },
    )

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "example-1.2.3.zip")

    assert result.get("error") is None
    assert result.get("root_dir") == "package"


def test_zipformat_structure_accepts_source_release_suffix_variant(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["executor-1.0.0/README.txt"])

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "executor-1.0.0-source-release.zip")

    assert result.get("error") is None
    assert result.get("root_dir") == "executor-1.0.0"


def test_zipformat_structure_accepts_src_suffix_variant(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3/README.txt"])

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "apache-example-1.2.3-src.zip")

    assert result.get("error") is None
    assert result.get("root_dir") == "apache-example-1.2.3"


@pytest.mark.asyncio
async def test_zipformat_structure_fails_when_cache_unavailable(tmp_path: pathlib.Path) -> None:
    recorder, args = await _zipformat_structure_args(tmp_path, "apache-example-1.2.3.zip")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=None)):
        await zipformat.structure(args)

    assert any(status == sql.CheckResultStatus.EXCEPTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)
    assert any("extracted archive tree is not available" in message.lower() for _, message, _ in recorder.messages)


def test_zipformat_structure_rejects_dated_src_suffix(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3/README.txt"])

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "apache-example-1.2.3-src-20251202.zip")

    assert "error" in result
    assert "Root directory mismatch" in result["error"]


def test_zipformat_structure_rejects_npm_pack_filename_mismatch(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree_with_contents(
        tmp_path,
        {
            "package/package.json": json.dumps({"name": "different", "version": "1.2.3"}),
            "package/README.txt": "hello",
        },
    )

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "example-1.2.3.zip")

    assert result.get("error") is not None
    assert "npm pack layout detected" in result["error"]
    assert result.get("root_dir") == "package"


def test_zipformat_structure_rejects_package_root_without_package_json(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree_with_contents(
        tmp_path,
        {
            "package/README.txt": "hello",
        },
    )

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "example-1.2.3.zip")

    assert result.get("error") is not None
    assert "Root directory mismatch" in result["error"]


def test_zipformat_structure_rejects_top_level_symlink(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["example-1.2.3/README.txt"])
    (cache_dir / "link").symlink_to(cache_dir / "missing-target")

    result = zipformat._structure_check_core_logic(safe.StatePath(cache_dir), "example-1.2.3.zip")

    assert result.get("error") is not None
    assert "Files found directly in root" in result["error"]


@pytest.mark.asyncio
async def test_zipformat_structure_reports_root_mismatch_as_suggestion(tmp_path: pathlib.Path) -> None:
    cache_dir = _make_cache_tree(tmp_path, ["apache-example-1.2.3-source/README.txt"])
    recorder, args = await _zipformat_structure_args(tmp_path, "apache-example-1.2.3.zip")

    with mock.patch.object(checks, "resolve_archive_dir", new=mock.AsyncMock(return_value=safe.StatePath(cache_dir))):
        await zipformat.structure(args)

    assert any(status == sql.CheckResultStatus.SUGGESTION.value for status, _, _ in recorder.messages)
    assert not any(status == sql.CheckResultStatus.CONCERN.value for status, _, _ in recorder.messages)
    assert any("Root directory mismatch" in message for _, message, _ in recorder.messages)


def _make_cache_tree(base: pathlib.Path, members: list[str]) -> pathlib.Path:
    """Create a directory tree simulating the quarantine extraction cache."""
    cache_dir = base / "cache"
    cache_dir.mkdir()
    for name in members:
        path = cache_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"data-{name}")
    return cache_dir


def _make_cache_tree_with_contents(base: pathlib.Path, members: dict[str, str]) -> pathlib.Path:
    """Create a directory tree with specific file contents."""
    cache_dir = base / "cache"
    cache_dir.mkdir()
    for name, content in members.items():
        path = cache_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return cache_dir


async def _targz_structure_args(
    tmp_path: pathlib.Path, archive_filename: str
) -> tuple[recorders.RecorderStub, checks.FunctionArguments]:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / archive_filename
    recorder = recorders.RecorderStub(archive_path, "tests.unit.test_archive_root_variants")
    args = checks.FunctionArguments(
        recorder=recorders.get_recorder(recorder),
        asf_uid="",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("test"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=safe.RelPath(archive_filename),
        extra_args={},
    )
    return recorder, args


async def _zipformat_structure_args(
    tmp_path: pathlib.Path, archive_filename: str
) -> tuple[recorders.RecorderStub, checks.FunctionArguments]:
    temp_dir = safe.StatePath(tmp_path)
    archive_path = temp_dir / archive_filename
    recorder = recorders.RecorderStub(archive_path, "tests.unit.test_archive_root_variants")
    args = checks.FunctionArguments(
        recorder=recorders.get_recorder(recorder),
        asf_uid="",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("test"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=safe.RelPath(archive_filename),
        extra_args={},
    )
    return recorder, args
