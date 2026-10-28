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
import unittest.mock as mock

import pytest
import werkzeug.exceptions as exceptions

import atr.api as api
import atr.models.api as models_api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage

PK, VK = safe.ProjectKey("project"), safe.VersionKey("1.0.0")


@pytest.fixture
def set_commit_hash() -> mock.AsyncMock:
    release = sql.Release(
        key="project-1.0.0",
        project_key="project",
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
        commit_hash="a" * 40,
    )
    return mock.AsyncMock(return_value=release)


@pytest.fixture
def storage_write(monkeypatch: pytest.MonkeyPatch, set_commit_hash: mock.AsyncMock) -> mock.MagicMock:
    wacp = mock.MagicMock()
    wacp.release.set_commit_hash = set_commit_hash
    write = mock.AsyncMock()
    write.as_project_committee_participant = mock.AsyncMock(return_value=wacp)
    cm = mock.AsyncMock()
    cm.__aenter__.return_value = write
    patched = mock.MagicMock(return_value=cm)
    monkeypatch.setattr(api.storage, "write", patched)
    monkeypatch.setattr(api, "_jwt_asf_uid", lambda: "tester")
    return patched


@pytest.mark.parametrize(("given", "expected"), [("A" * 40, "a" * 40), (None, None), ("", None)])
async def test_release_commit_sets_for_the_caller(
    set_commit_hash: mock.AsyncMock, storage_write: mock.MagicMock, given: str | None, expected: str | None
):
    args = models_api.ReleaseCommitArgs.model_validate({"project": PK, "version": VK, "commit": given})

    body, status = await inspect.unwrap(api.release_commit)(_release_commit="release/commit", data=args)

    assert status == 200
    assert body["endpoint"] == "/release/commit"
    assert body["release"]["commit_hash"] == "a" * 40
    # The writer takes a plain string, folded to lowercase, and None to clear
    set_commit_hash.assert_awaited_once_with(PK, VK, expected)
    storage_write.assert_called_once_with("tester")


@pytest.mark.parametrize(
    ("status", "expected"), [(400, exceptions.BadRequest), (404, exceptions.NotFound), (409, exceptions.Conflict)]
)
async def test_release_commit_maps_writer_errors(
    set_commit_hash: mock.AsyncMock,
    storage_write: mock.MagicMock,
    status: int,
    expected: type[exceptions.HTTPException],
):
    set_commit_hash.side_effect = storage.AccessError("No", status=status)
    args = models_api.ReleaseCommitArgs(project=PK, version=VK, commit=safe.CommitHash("a" * 40))

    with pytest.raises(expected):
        await inspect.unwrap(api.release_commit)(_release_commit="release/commit", data=args)
