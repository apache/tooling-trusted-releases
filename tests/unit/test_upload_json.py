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
import unittest.mock as mock

import pytest
import quart

import atr.post.upload as upload


@pytest.fixture
def app():
    app = quart.Quart(__name__)
    app.secret_key = "test"
    app.config["TESTING"] = True
    return app


@pytest.mark.asyncio
async def test_add_files_html_redirect_on_success(app):
    redirect_response = mock.MagicMock()
    session = mock.AsyncMock()
    session.redirect = mock.AsyncMock(return_value=redirect_response)

    storage_write = _mock_storage_write(None, 2, False)

    with mock.patch.object(upload.storage, "write", storage_write):
        async with app.test_request_context("/upload/test/1.0"):
            result = await upload._add_files(
                session,
                mock.MagicMock(),
                mock.MagicMock(),
                mock.MagicMock(),
                wants_json=False,
            )

    assert result is redirect_response
    session.redirect.assert_called_once()


@pytest.mark.asyncio
async def test_add_files_json_creation_error(app):
    storage_write = _mock_storage_write("No files provided", 0, False)

    with mock.patch.object(upload.storage, "write", storage_write):
        async with app.test_request_context("/upload/test/1.0"):
            result = await upload._add_files(
                mock.AsyncMock(),
                mock.MagicMock(),
                mock.MagicMock(),
                mock.MagicMock(),
                wants_json=True,
            )

    response, status = result
    assert status == 400
    data = json.loads(await response.data)
    assert data["ok"] is False
    assert data["message"] == "No files provided"


@pytest.mark.asyncio
async def test_add_files_json_success(app):
    storage_write = _mock_storage_write(None, 2, False)

    with (
        mock.patch.object(upload.storage, "write", storage_write),
        mock.patch.object(upload.util, "as_url", return_value="/compose/test/1.0"),
    ):
        async with app.test_request_context("/upload/test/1.0"):
            result = await upload._add_files(
                mock.AsyncMock(),
                mock.MagicMock(),
                mock.MagicMock(),
                mock.MagicMock(),
                wants_json=True,
            )

    response, status = result
    assert status == 200
    data = json.loads(await response.data)
    assert data["ok"] is True
    assert data["next_url"] == "/compose/test/1.0"
    assert "2 files" in data["message"]


def _mock_storage_write(creation_error, number_of_files, was_quarantined):
    wacp = mock.AsyncMock()
    wacp.release.upload_files = mock.AsyncMock(
        return_value=(creation_error, number_of_files, was_quarantined),
    )
    write = mock.AsyncMock()
    write.as_project_committee_participant = mock.AsyncMock(return_value=wacp)

    cm = mock.AsyncMock()
    cm.__aenter__.return_value = write

    return mock.MagicMock(return_value=cm)
