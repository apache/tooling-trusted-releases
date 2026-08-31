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

import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.models.safe as safe
import atr.svn.catalog as catalog


def _changed(**paths: str) -> dict[str, dict[str, str]]:
    # A svnpubsub-style changed map: path -> {"flags": ...}. Dir paths carry a trailing slash,
    # so the caller writes the layout as it comes off the wire
    return {path.replace("__", "/"): {"flags": flags} for path, flags in paths.items()}


class _Query:
    def __init__(self, result: object) -> None:
        self._result = result

    async def get(self) -> object:
        return self._result


def _data_returning(artifact: object) -> mock.MagicMock:
    data = mock.MagicMock()
    data.artifact = mock.MagicMock(return_value=_Query(artifact))
    return data


# The structural pass collects deleted artifact files by directory and name, without deciding
# source from binary - that is the artifacts table's job at resolve time


def test_a_deleted_artifact_file_is_collected_by_directory_and_name() -> None:
    changes = catalog._structural_changes(_changed(**{"release__tomcat__apache-tomcat-1.4.3.tar.gz": "D "}))
    assert ("tomcat", "apache-tomcat-1.4.3.tar.gz") in changes.removed_files


def test_a_deleted_nested_artifact_file_keeps_its_full_directory() -> None:
    changes = catalog._structural_changes(_changed(**{"release__httpd__2.4.63__httpd-2.4.63.tar.gz": "D "}))
    assert ("httpd/2.4.63", "httpd-2.4.63.tar.gz") in changes.removed_files


def test_a_deleted_companion_is_not_collected() -> None:
    # A signature or checksum has no artifacts-table row of its own, so it never resolves
    changes = catalog._structural_changes(_changed(**{"release__tomcat__apache-tomcat-1.4.3.tar.gz.asc": "D "}))
    assert changes.removed_files == []


def test_a_deleted_binary_is_collected_and_filtered_later() -> None:
    # A binary is a real artifact file, so the structural pass keeps it; whether it retires the
    # release is decided at resolve time from its stored classification
    changes = catalog._structural_changes(_changed(**{"release__tomcat__apache-tomcat-1.4.3-bin.zip": "D "}))
    assert ("tomcat", "apache-tomcat-1.4.3-bin.zip") in changes.removed_files


def test_a_whole_version_directory_deletion_is_a_keyed_removal_not_a_file() -> None:
    changes = catalog._structural_changes(_changed(**{"release__httpd__2.4.63__": "D "}))
    assert ("httpd", None, "2.4.63") in changes.removed
    assert changes.removed_files == []


# The resolve pass looks each deleted file up in the artifacts table


@pytest.mark.asyncio
async def test_deleting_a_source_of_a_live_release_resolves_to_that_release() -> None:
    release_record = SimpleNamespace(key="tomcat-1.4.3", project_key="tomcat", version="1.4.3", is_archived=False)
    data = _data_returning(SimpleNamespace(release=release_record))
    resolved = await catalog._resolve_removed_file(data, "tomcat", "apache-tomcat-1.4.3.tar.gz")
    assert resolved is not None
    project_key, version_key, release_key = resolved
    assert (str(project_key), str(version_key), release_key) == ("tomcat", "1.4.3", "tomcat-1.4.3")


@pytest.mark.asyncio
async def test_deleting_a_source_of_an_already_archived_release_is_a_no_op() -> None:
    release_record = SimpleNamespace(key="tomcat-1.4.3", project_key="tomcat", version="1.4.3", is_archived=True)
    data = _data_returning(SimpleNamespace(release=release_record))
    assert await catalog._resolve_removed_file(data, "tomcat", "apache-tomcat-1.4.3.tar.gz") is None


@pytest.mark.asyncio
async def test_deleting_a_file_with_no_catalogued_source_row_is_a_no_op() -> None:
    # No source-classified artifact matches, e.g. a binary deletion or an uncatalogued file
    data = _data_returning(None)
    assert await catalog._resolve_removed_file(data, "tomcat", "apache-tomcat-1.4.3-bin.zip") is None


@pytest.mark.asyncio
async def test_a_source_deleted_for_a_release_republished_in_the_same_commit_is_not_archived(monkeypatch) -> None:
    monkeypatch.setattr(
        catalog,
        "_resolve_removed_file",
        mock.AsyncMock(return_value=(safe.ProjectKey("foo"), safe.VersionKey("1.2.0"), "foo-1.2.0")),
    )
    monkeypatch.setattr(catalog, "_published_release_keys", mock.AsyncMock(return_value={"foo-1.2.0"}))
    _releases, archives = await catalog._resolve_changes(
        mock.MagicMock(), added={}, removed=set(), removed_files=[("foo/1.2.0", "foo-1.2.0.tar.gz")]
    )
    assert archives == []


@pytest.mark.asyncio
async def test_a_source_deleted_for_a_release_not_republished_is_archived(monkeypatch) -> None:
    monkeypatch.setattr(
        catalog,
        "_resolve_removed_file",
        mock.AsyncMock(return_value=(safe.ProjectKey("foo"), safe.VersionKey("1.2.0"), "foo-1.2.0")),
    )
    monkeypatch.setattr(catalog, "_published_release_keys", mock.AsyncMock(return_value=set()))
    _releases, archives = await catalog._resolve_changes(
        mock.MagicMock(), added={}, removed=set(), removed_files=[("foo/1.2.0", "foo-1.2.0.tar.gz")]
    )
    assert [(str(p), str(v)) for p, v in archives] == [("foo", "1.2.0")]


@pytest.mark.asyncio
async def test_the_same_release_is_only_archived_once_across_several_deleted_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        catalog,
        "_resolve_removed_file",
        mock.AsyncMock(return_value=(safe.ProjectKey("foo"), safe.VersionKey("1.2.0"), "foo-1.2.0")),
    )
    monkeypatch.setattr(catalog, "_published_release_keys", mock.AsyncMock(return_value=set()))
    _releases, archives = await catalog._resolve_changes(
        mock.MagicMock(),
        added={},
        removed=set(),
        removed_files=[("foo/1.2.0", "foo-1.2.0-src.tar.gz"), ("foo/1.2.0", "foo-1.2.0-src.zip")],
    )
    assert len(archives) == 1
