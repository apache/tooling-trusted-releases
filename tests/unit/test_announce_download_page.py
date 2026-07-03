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

import types
import unittest.mock as mock

import pydantic
import pytest

import atr.models.safe as safe
import atr.shared.announce as shared_announce
import atr.storage.writers.project as project


def parsed(url: str) -> pydantic.HttpUrl:
    return pydantic.TypeAdapter(pydantic.HttpUrl).validate_python(url)


@pytest.mark.asyncio
async def test_set_download_page_keeps_existing_value() -> None:
    release_manager = writer()
    project_row = types.SimpleNamespace(
        key="example",
        download_page="https://existing.apache.org/download",
        mark_updated=mock.MagicMock(),
    )
    release_manager._ReleaseManager__validate_project_in_committee = mock.AsyncMock(return_value=project_row)

    result = await release_manager.set_download_page(safe.ProjectKey("example"), "https://example.apache.org/other")

    assert result == "https://existing.apache.org/download"
    assert project_row.download_page == "https://existing.apache.org/download"
    release_manager._ReleaseManager__data.rollback.assert_awaited_once()
    release_manager._ReleaseManager__data.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_download_page_sets_unset_value() -> None:
    release_manager = writer()
    project_row = types.SimpleNamespace(key="example", download_page=None, mark_updated=mock.MagicMock())
    release_manager._ReleaseManager__validate_project_in_committee = mock.AsyncMock(return_value=project_row)

    result = await release_manager.set_download_page(safe.ProjectKey("example"), "https://example.apache.org/download")

    assert result is None
    assert project_row.download_page == "https://example.apache.org/download"
    release_manager._ReleaseManager__data.begin_immediate.assert_awaited_once()
    release_manager._ReleaseManager__data.commit.assert_awaited_once()
    release_manager._ReleaseManager__write_as.append_to_audit_log.assert_called_once_with(
        asf_uid="alice",
        project_key="example",
        download_page="https://example.apache.org/download",
    )


def test_validate_download_page_accepts_https_hostname() -> None:
    value = parsed("https://example.apache.org/download")
    assert shared_announce.AnnounceForm._validate_download_page(value) == value
    assert shared_announce.AnnounceForm._validate_download_page(None) is None


def test_validate_download_page_rejects_http_and_ip_addresses() -> None:
    with pytest.raises(ValueError, match="https"):
        shared_announce.AnnounceForm._validate_download_page(parsed("http://example.apache.org/"))
    with pytest.raises(ValueError, match="IP address"):
        shared_announce.AnnounceForm._validate_download_page(parsed("https://127.0.0.1/download"))
    with pytest.raises(ValueError, match="IP address"):
        shared_announce.AnnounceForm._validate_download_page(parsed("https://[::1]/download"))


def writer() -> project.ReleaseManager:
    release_manager = object.__new__(project.ReleaseManager)
    data = mock.AsyncMock()
    data.expire_all = mock.MagicMock()
    release_manager._ReleaseManager__data = data
    release_manager._ReleaseManager__write_as = mock.MagicMock()
    release_manager._ReleaseManager__asf_uid = "alice"
    return release_manager
