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

import contextlib
import hashlib
import pathlib
import unittest.mock as mock
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest

import atr.api
import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql


class MockQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    async def all(self) -> list[object]:
        if isinstance(self._value, list):
            return self._value
        return [self._value] if (self._value is not None) else []

    async def get(self) -> object:
        return self._value


class MockDBSession:
    def __init__(self, projects: dict[str, object], releases: dict[str, list[object]]) -> None:
        self._projects = projects
        self._releases = releases

    def project(self, **kwargs: object) -> MockQuery:
        key = kwargs.get("key")
        committee_key = kwargs.get("committee_key")
        if key is not None:
            return MockQuery(self._projects.get(str(key)))
        if committee_key is not None:
            matching = [p for p in self._projects.values() if getattr(p, "committee_key", None) == committee_key]
            return MockQuery(matching)
        return MockQuery(None)

    def release(self, **kwargs: object) -> MockQuery:
        project_key = kwargs.get("project_key")
        version = kwargs.get("version")
        releases = self._releases.get(str(project_key), []) if (project_key is not None) else []
        if version is not None:
            match = next((r for r in releases if getattr(r, "version", None) == str(version)), None)
            return MockQuery(match)
        return MockQuery(releases)


def test_args_accepts_scoping_fields() -> None:
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("0.0.1"),
    )
    assert str(args.project_key) == "example"
    assert str(args.version_key) == "0.0.1"


def test_args_defaults_scoping_fields_to_none() -> None:
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")
    assert args.project_key is None
    assert args.version_key is None


def test_committee_keys_path_podling(tmp_path: pathlib.Path) -> None:
    downloads = safe.StatePath(tmp_path)
    committee = SimpleNamespace(key="myproject", is_podling=True)
    with mock.patch.object(atr.api.paths, "get_downloads_dir", return_value=downloads):
        result = atr.api._committee_keys_path(committee)
    assert str(result).endswith("/incubator/myproject/KEYS")


def test_committee_keys_path_regular(tmp_path: pathlib.Path) -> None:
    downloads = safe.StatePath(tmp_path)
    committee = SimpleNamespace(key="myproject", is_podling=False)
    with mock.patch.object(atr.api.paths, "get_downloads_dir", return_value=downloads):
        result = atr.api._committee_keys_path(committee)
    assert str(result).endswith("/myproject/KEYS")
    assert "/incubator/" not in str(result)


def test_committee_keys_url_podling() -> None:
    committee = SimpleNamespace(key="myproject", is_podling=True)
    result = atr.api._committee_keys_url("atr.example.org", committee)
    assert result == "https://atr.example.org/downloads/incubator/myproject/KEYS"


def test_committee_keys_url_regular() -> None:
    committee = SimpleNamespace(key="myproject", is_podling=False)
    result = atr.api._committee_keys_url("atr.example.org", committee)
    assert result == "https://atr.example.org/downloads/myproject/KEYS"


@pytest.mark.asyncio
async def test_match_release_matches_file_and_hash(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    sig_content = b"fake signature content"
    sig_hash = hashlib.sha3_256(sig_content).hexdigest()

    (tmp_path / "example-0.0.1.tar.gz.asc").write_bytes(sig_content)

    args = _make_args("example-0.0.1.tar.gz.asc", sig_hash)
    assert await atr.api._match_release(release_dir, args) is True


@pytest.mark.asyncio
async def test_match_release_matches_in_subdirectory(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    sig_content = b"nested signature"
    sig_hash = hashlib.sha3_256(sig_content).hexdigest()

    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "example-0.0.1.tar.gz.asc").write_bytes(sig_content)

    args = _make_args("example-0.0.1.tar.gz.asc", sig_hash)
    assert await atr.api._match_release(release_dir, args) is True


@pytest.mark.asyncio
async def test_match_release_no_match_empty_directory(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")
    assert await atr.api._match_release(release_dir, args) is False


@pytest.mark.asyncio
async def test_match_release_no_match_missing_directory(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nonexistent"
    release_dir = safe.StatePath(missing)
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")
    assert await atr.api._match_release(release_dir, args) is False


@pytest.mark.asyncio
async def test_match_release_no_match_wrong_hash(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    (tmp_path / "example-0.0.1.tar.gz.asc").write_bytes(b"actual content")

    wrong_hash = hashlib.sha3_256(b"different content").hexdigest()
    args = _make_args("example-0.0.1.tar.gz.asc", wrong_hash)
    assert await atr.api._match_release(release_dir, args) is False


@pytest.mark.asyncio
async def test_scoped_finds_committee_with_project_and_version(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("0.0.1"),
    )

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=True)),
    ):
        result = await atr.api._match_committees_scoped([committee], args)

    assert len(result) == 1
    assert result[0].key == "example-pmc"


@pytest.mark.asyncio
async def test_scoped_finds_committee_with_project_only(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
    )

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=True)),
    ):
        result = await atr.api._match_committees_scoped([committee], args)

    assert len(result) == 1
    assert result[0].key == "example-pmc"


@pytest.mark.asyncio
async def test_scoped_returns_empty_when_committee_not_linked() -> None:
    unlinked_committee = SimpleNamespace(key="other-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    mock_data = MockDBSession(
        projects={"example": project},
        releases={},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
    )

    with mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)):
        result = await atr.api._match_committees_scoped([unlinked_committee], args)

    assert result == []


@pytest.mark.asyncio
async def test_scoped_returns_empty_when_project_not_found() -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    mock_data = MockDBSession(projects={}, releases={})
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("nonexistent"),
    )

    with mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)):
        result = await atr.api._match_committees_scoped([committee], args)

    assert result == []


@pytest.mark.asyncio
async def test_scoped_returns_empty_when_version_not_found() -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": []},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("9.9.9"),
    )

    with mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)):
        result = await atr.api._match_committees_scoped([committee], args)

    assert result == []


@pytest.mark.asyncio
async def test_unscoped_finds_matching_committee(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=True)),
    ):
        result = await atr.api._match_committees([committee], args)

    assert len(result) == 1
    assert result[0].key == "example-pmc"


@pytest.mark.asyncio
async def test_unscoped_returns_empty_when_no_match(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=False)),
    ):
        result = await atr.api._match_committees([committee], args)

    assert result == []


def _make_args(
    signature_file_name: str,
    signature_sha3_256: str,
    *,
    project_key: safe.ProjectKey | None = None,
    version_key: safe.VersionKey | None = None,
) -> api.SignatureProvenanceArgs:
    return api.SignatureProvenanceArgs(
        signature_file_name=signature_file_name,
        signature_asc_text="-----BEGIN PGP SIGNATURE-----\ntest\n-----END PGP SIGNATURE-----\n",
        signature_sha3_256=signature_sha3_256,
        project_key=project_key,
        version_key=version_key,
    )


@contextlib.asynccontextmanager
async def _mock_db_session(db_data: MockDBSession) -> AsyncGenerator[MockDBSession]:
    yield db_data


def _mock_session_factory(db_data: MockDBSession):
    def session() -> contextlib.AbstractAsyncContextManager[MockDBSession]:
        return _mock_db_session(db_data)

    return session
