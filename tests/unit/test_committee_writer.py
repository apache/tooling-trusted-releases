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

import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.committee as committee


class Query:
    def __init__(self, value: object) -> None:
        self.value = value

    async def demand(self, error: Exception) -> object:
        if self.value is None:
            raise error
        return self.value


def test_committee_writer_is_member_only() -> None:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    data = mock.MagicMock()
    warm = storage.WriteAsReleaseManager(write, data, "alpha")
    wacm = storage.WriteAsCommitteeMember(write, data, "alpha")

    assert not hasattr(warm, "committee")
    assert hasattr(wacm.committee, "release_manager_add")
    assert hasattr(wacm.committee, "release_manager_remove")


@pytest.mark.asyncio
async def test_release_manager_add_designates_committer() -> None:
    row = _committee_row()
    writer, data, write_as = _writer_with_mocks(row)

    added = await writer.release_manager_add("carol")

    assert added is True
    assert row.release_managers == ["carol"]
    assert row.updated_by == "chair"
    data.commit.assert_awaited_once()
    data.rollback.assert_not_awaited()
    write_as.append_to_audit_log.assert_called_once()
    assert write_as.append_to_audit_log.call_args.kwargs["release_manager"] == "carol"


@pytest.mark.asyncio
async def test_release_manager_add_is_idempotent() -> None:
    row = _committee_row(release_managers=["carol"])
    writer, data, write_as = _writer_with_mocks(row)

    added = await writer.release_manager_add("carol")

    assert added is False
    assert row.release_managers == ["carol"]
    data.commit.assert_not_awaited()
    data.rollback.assert_awaited_once()
    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_release_manager_add_normalises_uid() -> None:
    row = _committee_row()
    writer, _data, _write_as = _writer_with_mocks(row)

    added = await writer.release_manager_add("  Carol ")

    assert added is True
    assert row.release_managers == ["carol"]


@pytest.mark.asyncio
async def test_release_manager_add_rejects_empty_uid() -> None:
    writer, data, _write_as = _writer_with_mocks(_committee_row())

    with pytest.raises(storage.AccessError, match="ASF UID is required"):
        await writer.release_manager_add("  ")

    data.begin_immediate.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_manager_add_rejects_non_committer() -> None:
    row = _committee_row()
    writer, data, write_as = _writer_with_mocks(row)

    with pytest.raises(storage.AccessError, match="not a committer"):
        await writer.release_manager_add("mallory")

    assert row.release_managers == []
    data.commit.assert_not_awaited()
    data.rollback.assert_awaited_once()
    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_release_manager_add_rejects_pmc_member() -> None:
    row = _committee_row()
    writer, data, write_as = _writer_with_mocks(row)

    with pytest.raises(storage.AccessError, match="already a release manager"):
        await writer.release_manager_add("chair")

    assert row.release_managers == []
    data.commit.assert_not_awaited()
    data.rollback.assert_awaited_once()
    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_release_manager_remove_absent_uid_is_noop() -> None:
    row = _committee_row(release_managers=["bob"])
    writer, data, write_as = _writer_with_mocks(row)

    removed = await writer.release_manager_remove("carol")

    assert removed is False
    assert row.release_managers == ["bob"]
    data.commit.assert_not_awaited()
    data.rollback.assert_awaited_once()
    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_release_manager_remove_removes_designation() -> None:
    row = _committee_row(release_managers=["bob", "carol"])
    writer, data, write_as = _writer_with_mocks(row)

    removed = await writer.release_manager_remove("carol")

    assert removed is True
    assert row.release_managers == ["bob"]
    data.commit.assert_awaited_once()
    write_as.append_to_audit_log.assert_called_once()


def _committee_row(
    committers: list[str] | None = None,
    release_managers: list[str] | None = None,
) -> sql.Committee:
    return sql.Committee(
        key="alpha",
        committee_members=["chair"],
        committers=committers if (committers is not None) else ["chair", "carol"],
        release_managers=release_managers if (release_managers is not None) else [],
    )


def _writer_with_mocks(
    committee_row: sql.Committee | None,
) -> tuple[committee.CommitteeMember, mock.MagicMock, SimpleNamespace]:
    data = mock.MagicMock()
    data.committee = mock.MagicMock(return_value=Query(committee_row))
    data.begin_immediate = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    write_as = SimpleNamespace(append_to_audit_log=mock.MagicMock())
    writer = object.__new__(committee.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__write_as = write_as
    writer._CommitteeMember__asf_uid = "chair"
    writer._CommitteeMember__committee_key = "alpha"
    return writer, data, write_as
