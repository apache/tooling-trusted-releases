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

import asyncio
import inspect
import unittest.mock as mock

import pytest
import quart
import werkzeug.exceptions as exceptions

import atr.api
import atr.models.api
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.datatypes as datatypes


@pytest.fixture
def app():
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def state_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(atr.api.paths, "get_tmp_dir", lambda: safe.StatePath(tmp_path))
    return tmp_path


class StallingBody:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(3600)
        return b""


def _mock_storage_write(store_result, recorded):
    async def store_file(project, version, relpath, source, expected_revision):
        recorded["project"] = project
        recorded["version"] = version
        recorded["relpath"] = relpath
        recorded["content"] = source.read_bytes()
        recorded["expected_revision"] = expected_revision
        if isinstance(store_result, Exception):
            raise store_result
        return store_result

    wacp = mock.AsyncMock()
    wacp.release.store_file = store_file
    write = mock.AsyncMock()
    write.as_project_committee_participant = mock.AsyncMock(return_value=wacp)
    cm = mock.AsyncMock()
    cm.__aenter__.return_value = write
    return mock.MagicMock(return_value=cm)


async def test_release_store_drain_timeout(app, state_tmp):
    handler = inspect.unwrap(atr.api.release_store)
    query = atr.models.api.ReleaseStoreQuery(project="test", version="0.1", relpath="a.tar.gz")
    with mock.patch.object(atr.api, "_jwt_asf_uid", return_value="test"):
        async with app.test_request_context("/api/release/store", method="POST", data=b""):
            quart.request.body_timeout = 0.05
            quart.request.body = StallingBody()
            with pytest.raises(exceptions.RequestTimeout):
                await handler(_release_store="release/store", query_args=query)
    assert list(state_tmp.iterdir()) == []


async def test_release_store_empty_expected_revision(app, state_tmp):
    handler = inspect.unwrap(atr.api.release_store)
    query = atr.models.api.ReleaseStoreQuery(project="test", version="0.1", relpath="a.tar.gz", expected_revision="")
    with mock.patch.object(atr.api, "_jwt_asf_uid", return_value="test"):
        async with app.test_request_context("/api/release/store", method="POST", data=b""):
            with pytest.raises(exceptions.BadRequest):
                await handler(_release_store="release/store", query_args=query)
    assert list(state_tmp.iterdir()) == []


async def test_release_store_invalid_content(app, state_tmp):
    recorded = {}
    storage_write = _mock_storage_write(datatypes.ContentInvalidError("File validation failed"), recorded)
    handler = inspect.unwrap(atr.api.release_store)
    query = atr.models.api.ReleaseStoreQuery(project="test", version="0.1", relpath="a.tar.gz")
    with (
        mock.patch.object(atr.api, "_jwt_asf_uid", return_value="test"),
        mock.patch.object(atr.api.storage, "write", storage_write),
    ):
        async with app.test_request_context("/api/release/store", method="POST", data=b""):
            with pytest.raises(exceptions.BadRequest):
                await handler(_release_store="release/store", query_args=query)
    assert list(state_tmp.iterdir()) == []


async def test_release_store_invalid_metadata(app, state_tmp):
    handler = inspect.unwrap(atr.api.release_store)
    query = atr.models.api.ReleaseStoreQuery(project="NOT VALID", version="0.1", relpath="a.tar.gz")
    with mock.patch.object(atr.api, "_jwt_asf_uid", return_value="test"):
        async with app.test_request_context("/api/release/store", method="POST", data=b""):
            with pytest.raises(exceptions.BadRequest):
                await handler(_release_store="release/store", query_args=query)
    assert list(state_tmp.iterdir()) == []


async def test_release_store_quarantined(app, state_tmp):
    recorded = {}
    storage_write = _mock_storage_write(sql.Quarantined(), recorded)
    handler = inspect.unwrap(atr.api.release_store)
    query = atr.models.api.ReleaseStoreQuery(project="test", version="0.1", relpath="a.tar.gz")
    with (
        mock.patch.object(atr.api, "_jwt_asf_uid", return_value="test"),
        mock.patch.object(atr.api.storage, "write", storage_write),
    ):
        async with app.test_request_context("/api/release/store", method="POST", data=b"content"):
            body, status = await handler(_release_store="release/store", query_args=query)
    assert status == 202
    assert body["quarantined"] is True
    assert body["revision"] is None
    assert list(state_tmp.iterdir()) == []


async def test_release_store_streams_to_writer(app, state_tmp):
    recorded = {}
    storage_write = _mock_storage_write(sql.Revision(), recorded)
    handler = inspect.unwrap(atr.api.release_store)
    payload = b"artifact-bytes" * 65536
    query = atr.models.api.ReleaseStoreQuery(project="test", version="0.1", relpath="a/b.tar.gz")
    with (
        mock.patch.object(atr.api, "_jwt_asf_uid", return_value="test"),
        mock.patch.object(atr.api.storage, "write", storage_write),
    ):
        async with app.test_request_context("/api/release/store", method="POST", data=payload):
            body, status = await handler(_release_store="release/store", query_args=query)
    assert status == 201
    assert body["quarantined"] is False
    assert recorded["content"] == payload
    assert str(recorded["project"]) == "test"
    assert str(recorded["version"]) == "0.1"
    assert str(recorded["relpath"]) == "a/b.tar.gz"
    assert recorded["expected_revision"] is None
    assert list(state_tmp.iterdir()) == []
