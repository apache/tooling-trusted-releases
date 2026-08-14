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

import atr.config as config
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.release as release

_PUBLISH_URL = "https://internal.example.invalid/repos/dist/release"


class ReleaseQuery:
    def __init__(self, result: object) -> None:
        self._result = result

    async def get(self) -> object:
        return self._result

    async def demand(self, error: BaseException) -> object:
        if self._result is None:
            raise error
        return self._result


@pytest.mark.asyncio
async def test_admin_delete_allows_announced_release(monkeypatch):
    fake_release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=SimpleNamespace(is_active=True),
    )
    mock_data = mock.MagicMock()
    mock_data.release = mock.MagicMock(return_value=ReleaseQuery(fake_release))
    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "admin"
    admin = release.FoundationAdmin(mock_write, mock.MagicMock(), mock_data)

    delete_body = mock.AsyncMock(return_value=None)
    monkeypatch.setattr(release.FoundationAdmin, "_FoundationAdmin__delete_body", delete_body)
    error = await admin.delete(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    assert error is None
    delete_body.assert_awaited_once()


@pytest.mark.asyncio
async def test_archive_rejects_a_release_already_archived():
    member = _make_member(release_result=_archive_candidate(is_archived=True))
    with pytest.raises(storage.AccessError, match="already archived"):
        await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))


@pytest.mark.asyncio
async def test_archive_rejects_a_release_not_found():
    member = _make_member(release_result=None)
    with pytest.raises(storage.AccessError, match="not found"):
        await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))


@pytest.mark.asyncio
async def test_archive_rejects_a_release_not_in_the_release_phase():
    member = _make_member(release_result=_archive_candidate(phase=sql.ReleasePhase.RELEASE_PREVIEW))
    with pytest.raises(storage.AccessError, match="not in the release phase"):
        await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))


@pytest.mark.asyncio
async def test_archive_rejects_a_release_with_an_archival_vote_in_progress():
    target = _archive_candidate(version="1.0.0")
    member = _make_member(
        release_result=target,
        siblings=[_archive_candidate(version="2.0.0")],
        approval=SimpleNamespace(status=sql.ApprovalStatus.PENDING),
    )
    with pytest.raises(storage.AccessError, match="already in progress"):
        await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))


@pytest.mark.asyncio
async def test_archive_rejects_the_latest_release_in_the_cycle():
    # The latest in a cycle may only be archived through a CAP approval vote
    target = _archive_candidate(version="2.0.0")
    member = _make_member(release_result=target, siblings=[_archive_candidate(version="1.0.0")])
    with pytest.raises(storage.AccessError, match="requires a CAP approval vote"):
        await member.archive(safe.ProjectKey("example"), safe.VersionKey("2.0.0"))


@pytest.mark.asyncio
async def test_archive_succeeds_and_writes_lifecycle_event():
    target = _archive_candidate(version="1.0.0")
    member = _make_member(release_result=target, siblings=[_archive_candidate(version="2.0.0")])
    mock_data = member._CommitteeMember__data  # type: ignore[attr-defined]
    update_result = mock.MagicMock()
    update_result.rowcount = 1
    mock_data.execute_query = mock.AsyncMock(return_value=update_result)

    await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

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


@pytest.mark.asyncio
async def test_archive_enqueues_svn_unpublish_when_published(monkeypatch):
    # Archiving a release that ATR published should queue its removal from dist
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", _PUBLISH_URL, raising=False)
    target = _archive_candidate(version="1.0.0", download_path_suffix="example/1.0.0")
    member = _make_member(release_result=target, siblings=[_archive_candidate(version="2.0.0")])
    mock_data = member._CommitteeMember__data  # type: ignore[attr-defined]
    update_result = mock.MagicMock()
    update_result.rowcount = 1
    mock_data.execute_query = mock.AsyncMock(return_value=update_result)

    await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    added_args = [call.args[0] for call in mock_data.add.call_args_list]
    tasks = [a for a in added_args if isinstance(a, sql.Task) and a.task_type is sql.TaskType.SVN_UNPUBLISH]
    assert len(tasks) == 1
    task = tasks[0]
    assert task.project_key == "example"
    assert task.version_key == "1.0.0"
    # The task only names the release; the executor resolves where the files went
    assert "download_path_suffix" not in task.task_args


@pytest.mark.asyncio
async def test_archive_enqueues_svn_unpublish_for_a_root_published_release(monkeypatch):
    # An empty suffix means the release lives at the committee dist root; it must still be removed
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", _PUBLISH_URL, raising=False)
    target = _archive_candidate(version="1.0.0", download_path_suffix="")
    member = _make_member(release_result=target, siblings=[_archive_candidate(version="2.0.0")])
    mock_data = member._CommitteeMember__data  # type: ignore[attr-defined]
    update_result = mock.MagicMock()
    update_result.rowcount = 1
    mock_data.execute_query = mock.AsyncMock(return_value=update_result)

    await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    added_args = [call.args[0] for call in mock_data.add.call_args_list]
    tasks = [a for a in added_args if isinstance(a, sql.Task) and a.task_type is sql.TaskType.SVN_UNPUBLISH]
    assert len(tasks) == 1
    assert tasks[0].version_key == "1.0.0"


@pytest.mark.asyncio
async def test_archive_queues_svn_unpublish_without_a_suffix(monkeypatch):
    # A catalogued release carries no release-level suffix but its artifacts still record
    # dist locations, so a removal is queued and the executor works out what to take out.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", _PUBLISH_URL, raising=False)
    target = _archive_candidate(version="1.0.0", download_path_suffix=None)
    member = _make_member(release_result=target, siblings=[_archive_candidate(version="2.0.0")])
    mock_data = member._CommitteeMember__data  # type: ignore[attr-defined]
    update_result = mock.MagicMock()
    update_result.rowcount = 1
    mock_data.execute_query = mock.AsyncMock(return_value=update_result)

    await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    added_args = [call.args[0] for call in mock_data.add.call_args_list]
    tasks = [a for a in added_args if isinstance(a, sql.Task) and a.task_type is sql.TaskType.SVN_UNPUBLISH]
    assert len(tasks) == 1
    assert tasks[0].version_key == "1.0.0"


@pytest.mark.asyncio
async def test_archive_skips_svn_unpublish_when_svn_not_configured(monkeypatch):
    # Without an SVN publish target there is nothing to remove, so no task
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", None, raising=False)
    target = _archive_candidate(version="1.0.0", download_path_suffix="example/1.0.0")
    member = _make_member(release_result=target, siblings=[_archive_candidate(version="2.0.0")])
    mock_data = member._CommitteeMember__data  # type: ignore[attr-defined]
    update_result = mock.MagicMock()
    update_result.rowcount = 1
    mock_data.execute_query = mock.AsyncMock(return_value=update_result)

    await member.archive(safe.ProjectKey("example"), safe.VersionKey("1.0.0"))

    added_args = [call.args[0] for call in mock_data.add.call_args_list]
    tasks = [a for a in added_args if isinstance(a, sql.Task) and a.task_type is sql.TaskType.SVN_UNPUBLISH]
    assert tasks == []


@pytest.mark.asyncio
async def test_archive_core_skips_svn_unpublish_for_a_dist_watcher_archival(monkeypatch):
    # The dist watcher archives a release because its files already left dist, so
    # there is nothing to remove and no task to enqueue
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", _PUBLISH_URL, raising=False)
    monkeypatch.setattr(release.catalog_site, "queue_regeneration", mock.AsyncMock())
    target = _archive_candidate(version="1.0.0", download_path_suffix="example/1.0.0")
    mock_data = mock.MagicMock()
    update_result = mock.MagicMock()
    update_result.rowcount = 1
    mock_data.execute_query = mock.AsyncMock(return_value=update_result)
    mock_data.add = mock.MagicMock()
    mock_data.commit = mock.AsyncMock()

    error = await release.archive_release_core(
        mock_data,
        mock.MagicMock(),
        "alice",
        safe.ProjectKey("example"),
        safe.VersionKey("1.0.0"),
        target,
        sql.ArchiveSource.DIST_WATCHER,
    )

    assert error is None
    added_args = [call.args[0] for call in mock_data.add.call_args_list]
    tasks = [a for a in added_args if isinstance(a, sql.Task) and a.task_type is sql.TaskType.SVN_UNPUBLISH]
    assert tasks == []


def _archive_candidate(
    version: str = "1.0.0",
    phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE,
    is_archived: bool = False,
    download_path_suffix: str | None = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        key=f"example-{version}",
        version=version,
        phase=phase,
        archived=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC) if is_archived else None,
        is_archived=is_archived,
        released=datetime.datetime(2026, 1, int(version[0]) or 1, tzinfo=datetime.UTC),
        project_key="example",
        cycle_key="example-default",
        download_path_suffix=download_path_suffix,
    )


def _make_member(
    release_result: object,
    siblings: list[SimpleNamespace] | None = None,
    approval: object = None,
) -> release.CommitteeMember:
    # A release is only archivable without a vote when a later sibling supersedes it
    releases = list(siblings or [])
    if isinstance(release_result, SimpleNamespace):
        releases.append(release_result)
    project = SimpleNamespace(
        key="example",
        cycle_match=None,
        calver_format=None,
        version_method=sql.VersionMethod.SIMPLE,
        releases_including_embargoed=releases,
    )

    mock_data = mock.MagicMock()
    mock_data.release = mock.MagicMock(return_value=ReleaseQuery(release_result))
    mock_data.project = mock.MagicMock(return_value=ReleaseQuery(project))
    mock_data.approval_request = mock.MagicMock(return_value=ReleaseQuery(approval))
    # Archival queues a catalog-site regeneration, which first looks for an existing queued one.
    mock_data.task = mock.MagicMock(return_value=ReleaseQuery(None))
    mock_data.execute_query = mock.AsyncMock()
    mock_data.begin_immediate = mock.AsyncMock()
    mock_data.expire_all = mock.MagicMock()
    mock_data.rollback = mock.AsyncMock()
    mock_data.add = mock.MagicMock()
    mock_data.commit = mock.AsyncMock()

    mock_write = mock.MagicMock()
    mock_write.authorisation.asf_uid = "alice"
    mock_write_as = mock.MagicMock()

    member = release.CommitteeMember(mock_write, mock_write_as, mock_data, "test")
    return member
