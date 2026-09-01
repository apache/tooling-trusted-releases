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

import datetime
import inspect
import pathlib
from types import SimpleNamespace

import pytest
import werkzeug.exceptions as exceptions

import atr.api as api
import atr.db.interaction as interaction
import atr.models.api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.published as published


class MockQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    async def demand(self, error: Exception) -> object:
        if self._value is None:
            raise error
        return self._value

    async def get(self) -> object:
        return self._value


class MockDBSession:
    def __init__(self, release: object, revision: object = None) -> None:
        self._release = release
        self._revision = revision

    async def __aenter__(self) -> "MockDBSession":
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> bool:
        return False

    def release(self, **_kwargs: object) -> MockQuery:
        return MockQuery(self._release)

    def revision(self, **_kwargs: object) -> MockQuery:
        return MockQuery(self._revision)


def _check_result(number: int) -> sql.CheckResult:
    return sql.CheckResult(
        release_key="example-0.0.1",
        revision_number="00001",
        checker=f"checker{number}",
        primary_rel_path="example-0.0.1.tar.gz",
        member_rel_path=None,
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        status=sql.CheckResultStatus.NOTE,
        message=f"message {number}",
        data={},
    )


def _release(phase: sql.ReleasePhase) -> SimpleNamespace:
    return SimpleNamespace(
        key="example-0.0.1",
        phase=phase,
        safe_latest_revision_number=safe.RevisionNumber("00001"),
    )


def _patch_checks(monkeypatch: pytest.MonkeyPatch, release: SimpleNamespace, results: list[sql.CheckResult]) -> None:
    async def fake_checks_for(*_args: object, **_kwargs: object) -> list[sql.CheckResult]:
        return results

    monkeypatch.setattr(api.db, "session", lambda: MockDBSession(release, revision=SimpleNamespace()))
    monkeypatch.setattr(interaction, "checks_for", fake_checks_for)


async def test_checks_list_default_returns_all_with_count(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
    _patch_checks(monkeypatch, release, [_check_result(n) for n in range(3)])

    handler = inspect.unwrap(api.checks_list)
    body, status = await handler(
        "checks/list",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("0.0.1"),
        query_args=atr.models.api.ChecksListQuery(),
    )

    assert status == 200
    assert body["count"] == 3
    assert [check["message"] for check in body["checks"]] == ["message 0", "message 1", "message 2"]


async def test_checks_list_revision_slices_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
    _patch_checks(monkeypatch, release, [_check_result(n) for n in range(5)])

    handler = inspect.unwrap(api.checks_list_revision)
    body, status = await handler(
        "checks/list",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("0.0.1"),
        revision=safe.RevisionNumber("00001"),
        query_args=atr.models.api.ChecksListQuery(offset=1, limit=2),
    )

    assert status == 200
    assert body["count"] == 5
    assert [check["message"] for check in body["checks"]] == ["message 1", "message 2"]


@pytest.mark.parametrize("handler_name", ["checks_list", "checks_list_revision", "release_paths"])
async def test_pagination_limit_cap_rejected(handler_name: str) -> None:
    handler = inspect.unwrap(getattr(api, handler_name))
    kwargs: dict[str, object] = {
        "project_key": safe.ProjectKey("example"),
        "version_key": safe.VersionKey("0.0.1"),
    }
    if handler_name == "checks_list_revision":
        kwargs["revision"] = safe.RevisionNumber("00001")
    if handler_name == "release_paths":
        kwargs["query_args"] = atr.models.api.ReleasePathsQuery(limit=2000)
        literal = "release/paths"
    else:
        kwargs["query_args"] = atr.models.api.ChecksListQuery(limit=2000)
        literal = "checks/list"

    with pytest.raises(exceptions.BadRequest):
        await handler(literal, **kwargs)


@pytest.mark.parametrize("branch", ["published", "latest", "revision"])
async def test_release_paths_slices_each_branch(monkeypatch: pytest.MonkeyPatch, branch: str) -> None:
    names = [f"file{n}.txt" for n in range(4)]
    if branch == "published":
        release = _release(sql.ReleasePhase.RELEASE)

        async def fake_release_files(_release: object) -> list[SimpleNamespace]:
            return [SimpleNamespace(path=name) for name in names]

        monkeypatch.setattr(published, "release_files", fake_release_files)
    else:
        release = _release(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)

        async def fake_isdir(_path: object) -> bool:
            return True

        async def fake_paths_recursive(_path: object):
            for name in names:
                yield name

        monkeypatch.setattr(api.aiofiles.os.path, "isdir", fake_isdir)
        monkeypatch.setattr(api.util, "paths_recursive", fake_paths_recursive)
        monkeypatch.setattr(api.paths, "release_directory", lambda _release: pathlib.Path("unused"))
        monkeypatch.setattr(api.paths, "release_directory_version", lambda _release: pathlib.Path("unused"))

    monkeypatch.setattr(api.db, "session", lambda: MockDBSession(release, revision=SimpleNamespace()))

    handler = inspect.unwrap(api.release_paths)
    kwargs: dict[str, object] = {
        "project_key": safe.ProjectKey("example"),
        "version_key": safe.VersionKey("0.0.1"),
        "query_args": atr.models.api.ReleasePathsQuery(offset=1, limit=2),
    }
    if branch == "revision":
        kwargs["revision"] = safe.RevisionNumber("00001")
    body, status = await handler("release/paths", **kwargs)

    assert status == 200
    assert body["count"] == 4
    assert body["rel_paths"] == ["file1.txt", "file2.txt"]
