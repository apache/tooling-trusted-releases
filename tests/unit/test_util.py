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
import os
import pathlib
import stat
import types
import unittest.mock as mock

import aiohttp
import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util


def test_announce_recipients_use_users_list_for_maven(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("atr.config.get", lambda: types.SimpleNamespace(ATR_STATUS="PRODUCTION"))

    configurable = util.configurable_recipients(sql.RecipientAction.ANNOUNCE, "maven", is_podling=False)
    permitted = util.permitted_announce_recipients("testuser", "maven")

    assert "users@maven.apache.org" in configurable
    assert "users@maven.apache.org" in permitted
    assert "user@maven.apache.org" not in configurable
    assert "user@maven.apache.org" not in permitted


def test_chmod_files_does_not_change_directory_permissions(tmp_path: pathlib.Path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    os.chmod(subdir, 0o700)
    test_file = subdir / "test.txt"
    test_file.write_text("content")

    util.chmod_files(tmp_path, 0o444)

    dir_mode = stat.S_IMODE(subdir.stat().st_mode)
    assert dir_mode == 0o700


def test_chmod_files_handles_empty_directory(tmp_path: pathlib.Path):
    util.chmod_files(tmp_path, 0o444)


def test_chmod_files_handles_multiple_files(tmp_path: pathlib.Path):
    files = [tmp_path / f"file{i}.txt" for i in range(5)]
    for f in files:
        f.write_text("content")
        os.chmod(f, 0o644)

    util.chmod_files(tmp_path, 0o400)

    for f in files:
        file_mode = stat.S_IMODE(f.stat().st_mode)
        assert file_mode == 0o400


def test_chmod_files_handles_nested_directories(tmp_path: pathlib.Path):
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


def test_chmod_files_sets_custom_permissions(tmp_path: pathlib.Path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")
    os.chmod(test_file, 0o644)

    util.chmod_files(tmp_path, 0o400)

    file_mode = stat.S_IMODE(test_file.stat().st_mode)
    assert file_mode == 0o400


def test_chmod_files_sets_default_permissions(tmp_path: pathlib.Path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("content")
    os.chmod(test_file, 0o644)

    util.chmod_files(tmp_path, 0o444)

    file_mode = stat.S_IMODE(test_file.stat().st_mode)
    assert file_mode == 0o444


async def test_create_hard_link_clone_reuses_existing_destination_directory(tmp_path: pathlib.Path):
    first_source = tmp_path / "first"
    first_source.mkdir()
    (first_source / "apache-39.pom").write_text("first")
    second_source = tmp_path / "second"
    second_source.mkdir()
    (second_source / "maven-parent-49.pom").write_text("second")

    dest = safe.StatePath(tmp_path) / "downloads" / "maven" / "pom"
    await util.create_hard_link_clone(safe.StatePath(first_source), dest, exist_ok=False)
    await util.create_hard_link_clone(safe.StatePath(second_source), dest, exist_ok=False)

    dest_path = pathlib.Path(dest)
    assert (dest_path / "apache-39.pom").read_text() == "first"
    assert (dest_path / "maven-parent-49.pom").read_text() == "second"
    assert (dest_path / "apache-39.pom").stat().st_ino == (first_source / "apache-39.pom").stat().st_ino


def test_download_page_url_error_accepts_https():
    assert util.download_page_url_error("https://example.apache.org/download") is None


def test_download_page_url_error_rejects_http():
    assert util.download_page_url_error("http://example.apache.org/download") == "Must use https"


def test_download_page_url_error_rejects_literal_ip():
    assert util.download_page_url_error("https://192.0.2.1/download") == "Must not use a literal IP address"
    assert util.download_page_url_error("https://[2001:db8::1]/download") == "Must not use a literal IP address"


def test_json_for_script_element_escapes_correctly():
    payload = ["example.txt", "</script><script>alert(1)</script>", "apple&banana"]

    serialized = util.json_for_script_element(payload)

    assert "</script>" not in serialized
    assert "<script>" not in serialized
    assert "apple&banana" not in serialized
    assert "apple\\u0026banana" in serialized
    assert json.loads(serialized) == payload


def test_normalized_url_distinguishes_different_pages():
    assert util.normalized_url("https://example.org/a") != util.normalized_url("https://example.org/b")


def test_normalized_url_equates_cosmetic_differences():
    normalized = util.normalized_url("https://Example.org:443/download/")
    assert normalized == util.normalized_url("https://example.org/download")


async def test_number_of_release_files_counts_paths_with_spaces(monkeypatch, tmp_path: pathlib.Path):
    (tmp_path / "filename with spaces.txt").write_text("content")
    nested = tmp_path / "nested directory"
    nested.mkdir()
    (nested / "nested file with spaces.txt").write_text("content")

    monkeypatch.setattr(util.paths, "release_directory_revision", lambda release: tmp_path)

    assert await util.number_of_release_files(object()) == 2


async def test_public_resolver_allows_global_addresses():
    resolver = util.PublicResolver()
    inner = mock.AsyncMock()
    inner.resolve.return_value = [{"host": "93.184.216.34", "port": 443}]
    resolver._PublicResolver__resolver = inner

    assert await resolver.resolve("example.apache.org", 443) == inner.resolve.return_value


async def test_public_resolver_rejects_non_global_addresses():
    resolver = util.PublicResolver()
    inner = mock.AsyncMock()
    inner.resolve.return_value = [{"host": "10.0.0.1", "port": 443}]
    resolver._PublicResolver__resolver = inner

    with pytest.raises(aiohttp.ClientConnectionError):
        await resolver.resolve("internal.apache.org", 443)


def test_version_key_error_rejects_too_long():
    assert util.version_key_error("1" * safe.MAX_VERSION_LENGTH) is None
    assert util.version_key_error("1" * (safe.MAX_VERSION_LENGTH + 1)) is not None
