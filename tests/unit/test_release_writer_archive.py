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
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.writers.release as release


class _ReleaseQuery:
    def __init__(self, result: object) -> None:
        self._result = result

    async def get(self) -> object:
        return self._result


def _make_member(release_result: object) -> release.CommitteeMember:
    mock_data = mock.MagicMock()
    mock_data.release = mock.MagicMock(return_value=_ReleaseQuery(release_result))
    mock_data.execute_query = mock.AsyncMock()
    mock_data.add = mock.MagicMock()
    mock_data.commit = mock.AsyncMock()

    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "alice"
    mock_write_as = mock.MagicMock()

    member = release.CommitteeMember(mock_write, mock_write_as, mock_data, "test")
    # Skip the filesystem-side cleanup - covered by integration tests.
    object.__setattr__(member, "_CommitteeMember__remove_from_downloads", mock.AsyncMock())
    return member


@pytest.mark.asyncio
async def test_archive_returns_error_when_release_not_found():
    member = _make_member(release_result=None)
    error = await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))
    assert error is not None
    assert "not found" in error


@pytest.mark.asyncio
async def test_archive_returns_error_when_release_not_in_release_phase():
    fake_release = SimpleNamespace(
        key="example-1.0.0",
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        archived=None,
        project_key="example",
        cycle_key="example-default",
    )
    member = _make_member(release_result=fake_release)
    error = await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))
    assert error is not None
    assert "not in the release phase" in error


@pytest.mark.asyncio
async def test_archive_returns_error_when_release_already_archived():
    fake_release = SimpleNamespace(
        key="example-1.0.0",
        phase=sql.ReleasePhase.RELEASE,
        archived=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        project_key="example",
        cycle_key="example-default",
    )
    member = _make_member(release_result=fake_release)
    error = await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))
    assert error is not None
    assert "already archived" in error


@pytest.mark.asyncio
async def test_archive_succeeds_and_writes_lifecycle_event():
    fake_release = SimpleNamespace(
        key="example-1.0.0",
        phase=sql.ReleasePhase.RELEASE,
        archived=None,
        project_key="example",
        cycle_key="example-default",
    )
    member = _make_member(release_result=fake_release)
    mock_data = member._CommitteeMember__data  # type: ignore[attr-defined]

    error = await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))
    assert error is None

    # Lifecycle event added to the session
    added_args = [call.args[0] for call in mock_data.add.call_args_list]
    lifecycle_events = [a for a in added_args if isinstance(a, sql.LifecycleEvent)]
    assert len(lifecycle_events) == 1
    event = lifecycle_events[0]
    assert event.event is sql.LifecycleEventType.ARCHIVE
    assert event.version_key == "example-1.0.0"
    assert event.cycle_key == "example-default"
    assert event.project_key == "example"

    # archived timestamp UPDATE issued
    assert mock_data.execute_query.await_count == 1
    # Commit happened
    assert mock_data.commit.await_count == 1
