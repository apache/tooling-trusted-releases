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

import atr.models.safe as safe
import atr.post.release as release
import atr.storage as storage


@pytest.mark.asyncio
async def test_activity_converts_access_error_to_redirect() -> None:
    redirect_response = mock.MagicMock()
    session = mock.MagicMock()
    session.redirect = mock.AsyncMock(return_value=redirect_response)
    write = mock.MagicMock()
    write.as_project_committee_participant = mock.AsyncMock(side_effect=storage.AccessError("No access"))
    context_manager = _async_context_manager(write)

    with mock.patch.object(release.storage, "write", return_value=context_manager):
        result = await release._bump_activity(
            session,
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
        )

    assert result is redirect_response
    session.redirect.assert_awaited_once_with(release.get.root.index, error="No access")


@pytest.mark.asyncio
async def test_activity_redirects_with_success() -> None:
    redirect_response = mock.MagicMock()
    release_row = mock.MagicMock()
    session = mock.MagicMock()
    writer = mock.MagicMock()
    writer.bump_activity = mock.AsyncMock(return_value=release_row)
    write_as = mock.MagicMock()
    write_as.release = writer
    write = mock.MagicMock()
    write.as_project_committee_participant = mock.AsyncMock(return_value=write_as)
    context_manager = _async_context_manager(write)
    flash = mock.AsyncMock()
    release_as_redirect = mock.AsyncMock(return_value=redirect_response)

    with (
        mock.patch.object(release.storage, "write", return_value=context_manager),
        mock.patch.object(release.quart, "flash", flash),
        mock.patch.object(release.mapping, "release_as_redirect", release_as_redirect),
    ):
        result = await release._bump_activity(
            session,
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
        )

    assert result is redirect_response
    writer.bump_activity.assert_awaited_once_with(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))
    flash.assert_awaited_once_with("Inactivity clock reset", "success")
    release_as_redirect.assert_awaited_once_with(session, release_row)


def _async_context_manager(value: object) -> mock.MagicMock:
    context_manager = mock.MagicMock()
    context_manager.__aenter__ = mock.AsyncMock(return_value=value)
    context_manager.__aexit__ = mock.AsyncMock(return_value=None)
    return context_manager
