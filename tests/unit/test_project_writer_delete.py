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
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.project as project


@pytest.mark.asyncio
async def test_archive_committee_mismatch_rolls_back() -> None:
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.approval_request = mock.MagicMock(
        return_value=_query(get=_approval(sql.ApprovalAction.ARCHIVE, committee_key="beta"))
    )
    writer = _writer(data)

    with pytest.raises(storage.AccessError, match="different committee"):
        await writer.archive(safe.ProjectKey("alpha-one"), 7)

    data.begin_immediate.assert_awaited_once()
    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_sole_active_project_locks_then_rolls_back() -> None:
    target = SimpleNamespace(
        key="alpha-one", committee_key="alpha", status=sql.ProjectStatus.ACTIVE, releases_including_embargoed=[]
    )
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.approval_request = mock.MagicMock(return_value=_query(get=_approval(sql.ApprovalAction.ARCHIVE)))
    data.project = mock.MagicMock(side_effect=[_query(get=target), _query(all_=[target])])
    writer = _writer(data)

    with pytest.raises(storage.AccessError, match="only project"):
        await writer.archive(safe.ProjectKey("alpha-one"), 7)

    data.begin_immediate.assert_awaited_once()
    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()
    assert target.status == sql.ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_archive_with_drafts_rolls_back() -> None:
    draft = SimpleNamespace(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
    target = SimpleNamespace(
        key="alpha-one", committee_key="alpha", status=sql.ProjectStatus.ACTIVE, releases_including_embargoed=[draft]
    )
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.approval_request = mock.MagicMock(return_value=_query(get=_approval(sql.ApprovalAction.ARCHIVE)))
    data.project = mock.MagicMock(side_effect=[_query(get=target)])
    writer = _writer(data)

    with pytest.raises(storage.AccessError, match="draft releases"):
        await writer.archive(safe.ProjectKey("alpha-one"), 7)

    data.begin_immediate.assert_awaited_once()
    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()
    assert target.status == sql.ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_delete_non_sole_project_commits() -> None:
    target = SimpleNamespace(key="alpha-one", committee_key="alpha", releases_including_embargoed=[])
    other = SimpleNamespace(key="alpha-two", committee_key="alpha", releases_including_embargoed=[])
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.delete = mock.AsyncMock()
    data.approval_request = mock.MagicMock(return_value=_query(get=_approval(sql.ApprovalAction.DELETE)))
    data.project = mock.MagicMock(side_effect=[_query(get=target), _query(all_=[target, other])])
    writer = _writer(data)

    await writer.delete(safe.ProjectKey("alpha-one"), 7)

    data.begin_immediate.assert_awaited_once()
    data.delete.assert_awaited_once_with(target)
    data.commit.assert_awaited_once()
    data.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_sole_active_project_locks_then_rolls_back() -> None:
    target = SimpleNamespace(key="alpha-one", committee_key="alpha", releases_including_embargoed=[])
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.delete = mock.AsyncMock()
    data.approval_request = mock.MagicMock(return_value=_query(get=_approval(sql.ApprovalAction.DELETE)))
    data.project = mock.MagicMock(side_effect=[_query(get=target), _query(all_=[target])])
    writer = _writer(data)

    with pytest.raises(storage.AccessError, match="only project"):
        await writer.delete(safe.ProjectKey("alpha-one"), 7)

    data.begin_immediate.assert_awaited_once()
    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()
    data.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_with_releases_rolls_back() -> None:
    target = SimpleNamespace(key="alpha-one", committee_key="alpha", releases_including_embargoed=[object()])
    other = SimpleNamespace(key="alpha-two", committee_key="alpha", releases_including_embargoed=[])
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.delete = mock.AsyncMock()
    data.approval_request = mock.MagicMock(return_value=_query(get=_approval(sql.ApprovalAction.DELETE)))
    data.project = mock.MagicMock(side_effect=[_query(get=target), _query(all_=[target, other])])
    writer = _writer(data)

    with pytest.raises(storage.AccessError, match="associated releases"):
        await writer.delete(safe.ProjectKey("alpha-one"), 7)

    data.begin_immediate.assert_awaited_once()
    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()
    data.delete.assert_not_awaited()


def _approval(action: sql.ApprovalAction, committee_key: str = "alpha") -> SimpleNamespace:
    return SimpleNamespace(
        status=sql.ApprovalStatus.APPROVED, project_key="alpha-one", committee_key=committee_key, action=action
    )


def _query(get: object = None, all_: list | None = None) -> mock.MagicMock:
    query = mock.MagicMock()
    query.get = mock.AsyncMock(return_value=get)
    query.all = mock.AsyncMock(return_value=all_ or [])
    return query


def _writer(data: mock.MagicMock) -> project.CommitteeMember:
    writer = object.__new__(project.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__write_as = mock.MagicMock()
    writer._CommitteeMember__committee_key = "alpha"
    return writer
