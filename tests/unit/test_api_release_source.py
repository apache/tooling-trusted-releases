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
def set_archive_sources() -> mock.AsyncMock:
    revision = sql.Revision(number="00003", asfuid="tester", phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
    return mock.AsyncMock(return_value=revision)


@pytest.fixture
def storage_write(monkeypatch: pytest.MonkeyPatch, set_archive_sources: mock.AsyncMock) -> mock.MagicMock:
    wacp = mock.MagicMock()
    wacp.release.set_archive_sources = set_archive_sources
    write = mock.AsyncMock()
    write.as_project_committee_participant = mock.AsyncMock(return_value=wacp)
    cm = mock.AsyncMock()
    cm.__aenter__.return_value = write
    patched = mock.MagicMock(return_value=cm)
    monkeypatch.setattr(api.storage, "write", patched)
    return patched


async def test_release_source_declares_for_the_caller(
    monkeypatch: pytest.MonkeyPatch, set_archive_sources: mock.AsyncMock, storage_write: mock.MagicMock
):
    monkeypatch.setattr(api, "_jwt_asf_uid", lambda: "tester")
    args = models_api.ReleaseSourceArgs(
        project=PK, version=VK, sources=[_declaration()], expected_revision=safe.RevisionNumber("00002")
    )

    body, status = await inspect.unwrap(api.release_source)(_release_source="release/source", data=args)

    assert status == 201
    assert body["endpoint"] == "/release/source"
    assert body["revision"]["number"] == "00003"
    set_archive_sources.assert_awaited_once_with(PK, VK, args.sources, safe.RevisionNumber("00002"))
    storage_write.assert_called_once_with("tester")


@pytest.mark.parametrize(
    ("status", "expected"), [(400, exceptions.BadRequest), (404, exceptions.NotFound), (409, exceptions.Conflict)]
)
async def test_release_source_maps_writer_errors(
    monkeypatch: pytest.MonkeyPatch,
    set_archive_sources: mock.AsyncMock,
    storage_write: mock.MagicMock,
    status: int,
    expected: type[exceptions.HTTPException],
):
    monkeypatch.setattr(api, "_jwt_asf_uid", lambda: "tester")
    set_archive_sources.side_effect = storage.AccessError("No", status=status)
    args = models_api.ReleaseSourceArgs(project=PK, version=VK, sources=[_declaration()])

    with pytest.raises(expected):
        await inspect.unwrap(api.release_source)(_release_source="release/source", data=args)


def _declaration() -> models_api.SourceDeclaration:
    return models_api.SourceDeclaration(
        relpath=safe.RelPath("a-src.tar.gz"), repository="apache/first", commit=safe.CommitHash("a" * 40)
    )
