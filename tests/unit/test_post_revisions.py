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

import pytest
import quart

import atr.models.safe as safe
import atr.post.revisions as revisions


@pytest.fixture
def app():
    app = quart.Quart(__name__)
    app.secret_key = "test"
    app.config["TESTING"] = True
    return app


@pytest.mark.asyncio
async def test_set_tag_uses_storage_writer(app):
    redirect_response = mock.MagicMock()
    session = mock.AsyncMock()
    session.redirect = mock.AsyncMock(return_value=redirect_response)

    set_tag_form = mock.MagicMock()
    set_tag_form.revision_number = "00001"
    set_tag_form.tag = "rc1"

    revision_writer = mock.MagicMock()
    revision_writer.set_tag = mock.AsyncMock()
    write_as = mock.MagicMock()
    write_as.revision = revision_writer
    write = mock.AsyncMock()
    write.as_project_committee_participant = mock.AsyncMock(return_value=write_as)
    context_manager = mock.AsyncMock()
    context_manager.__aenter__.return_value = write
    context_manager.__aexit__.return_value = False

    with mock.patch.object(revisions.storage, "write", return_value=context_manager):
        async with app.test_request_context("/revisions/proj/1.0"):
            result = await revisions._set_tag(
                session,
                set_tag_form,
                safe.ProjectKey("proj"),
                safe.VersionKey("1.0"),
            )

    assert result is redirect_response
    write.as_project_committee_participant.assert_awaited_once_with(safe.ProjectKey("proj"))
    revision_writer.set_tag.assert_awaited_once_with(
        safe.ProjectKey("proj"),
        safe.VersionKey("1.0"),
        "00001",
        "rc1",
    )
