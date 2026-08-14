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
import atr.storage.writers.release as release_writer


@pytest.mark.asyncio
async def test_complete_archive_rejects_an_unapproved_request() -> None:
    approval = _approval(status=sql.ApprovalStatus.PENDING)
    data = _data(approval=approval)
    writer = _writer(data)

    error = await writer.complete_archive(safe.ProjectKey("alpha-one"), safe.VersionKey("1.2.0"), 7)

    assert (error is not None) and ("not ready to complete" in error)
    data.execute_query.assert_not_awaited()
    data.commit.assert_not_awaited()
    data.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_archive_rejects_a_request_for_a_different_release() -> None:
    approval = _approval(release_version="9.9.9")
    data = _data(approval=approval)
    writer = _writer(data)

    error = await writer.complete_archive(safe.ProjectKey("alpha-one"), safe.VersionKey("1.2.0"), 7)

    assert (error is not None) and ("does not match" in error)
    data.execute_query.assert_not_awaited()
    assert approval.status == sql.ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_complete_archive_rejects_a_project_scoped_approval() -> None:
    approval = _approval(action=sql.ApprovalAction.ARCHIVE, release_version=None)
    data = _data(approval=approval)
    writer = _writer(data)

    error = await writer.complete_archive(safe.ProjectKey("alpha-one"), safe.VersionKey("1.2.0"), 7)

    assert (error is not None) and ("does not match" in error)


@pytest.mark.asyncio
async def test_complete_archive_claims_and_archives_in_one_transaction() -> None:
    approval = _approval()
    data = _data(approval=approval)
    writer = _writer(data)

    error = await writer.complete_archive(safe.ProjectKey("alpha-one"), safe.VersionKey("1.2.0"), 7)

    assert error is None
    assert approval.status == sql.ApprovalStatus.COMPLETED
    data.commit.assert_awaited_once()
    data.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_archive_attributes_the_archival_to_the_requester() -> None:
    # The SVN removal must commit under a real ASF UID, so the archival is attributed to the
    # committer who requested the vote, not the system running the resolve task.
    approval = _approval()
    data = _data(approval=approval)
    write = mock.MagicMock()
    write.authorisation.asf_uid = "tester"
    write_as = mock.MagicMock()
    writer = release_writer.FoundationAdmin(write, write_as, data)

    await writer.complete_archive(safe.ProjectKey("alpha-one"), safe.VersionKey("1.2.0"), 7)

    write_as.append_to_audit_log.assert_called_once()
    assert write_as.append_to_audit_log.call_args.kwargs["asf_uid"] == "requester"


@pytest.mark.asyncio
async def test_complete_archive_takes_the_write_lock_before_reading_the_approval() -> None:
    # Without the lock two completions could both read the approval as approved
    approval = _approval(status=sql.ApprovalStatus.PENDING)
    data = _data(approval=approval)
    writer = _writer(data)

    error = await writer.complete_archive(safe.ProjectKey("alpha-one"), safe.VersionKey("1.2.0"), 7)

    assert error is not None
    data.begin_immediate.assert_awaited_once()
    assert data.begin_immediate.await_count == 1
    data.expire_all.assert_called_once()


@pytest.mark.asyncio
async def test_complete_archive_completes_an_approval_whose_release_is_already_archived() -> None:
    # The watcher or an auto-archive can get there first while the vote runs. The approval
    # must still be completed, or it blocks the release from ever being requested again
    approval = _approval()
    data = _data(approval=approval, is_archived=True)
    writer = _writer(data)

    error = await writer.complete_archive(safe.ProjectKey("alpha-one"), safe.VersionKey("1.2.0"), 7)

    assert error is None
    assert approval.status == sql.ApprovalStatus.COMPLETED
    data.commit.assert_awaited_once()
    data.rollback.assert_not_awaited()
    data.execute_query.assert_not_awaited()


def _approval(
    status: sql.ApprovalStatus = sql.ApprovalStatus.APPROVED,
    action: sql.ApprovalAction = sql.ApprovalAction.ARCHIVE_RELEASE,
    release_version: str | None = "1.2.0",
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        action=action,
        project_key="alpha-one",
        committee_key="alpha",
        release_version=release_version,
        requested_by="requester",
    )


def _data(approval: SimpleNamespace, update_rowcount: int = 1, is_archived: bool = False) -> mock.MagicMock:
    release = SimpleNamespace(
        key="alpha-one-1.2.0",
        version="1.2.0",
        project_key="alpha-one",
        cycle_key="alpha-one-default",
        phase=sql.ReleasePhase.RELEASE,
        is_archived=is_archived,
        committee=SimpleNamespace(key="alpha"),
    )
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query(get=release))
    data.approval_request = mock.MagicMock(return_value=_query(get=approval))
    # Archival queues a catalog-site regeneration, which first looks for an existing queued one.
    data.task = mock.MagicMock(return_value=_query(get=None))
    data.execute_query = mock.AsyncMock(return_value=SimpleNamespace(rowcount=update_rowcount))
    data.begin_immediate = mock.AsyncMock()
    data.expire_all = mock.MagicMock()
    data.add = mock.MagicMock()
    data.commit = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    return data


def _query(get: object = None) -> mock.MagicMock:
    query = mock.MagicMock()
    query.get = mock.AsyncMock(return_value=get)
    return query


def _writer(data: mock.MagicMock) -> release_writer.FoundationAdmin:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "tester"
    return release_writer.FoundationAdmin(write, mock.MagicMock(), data)
