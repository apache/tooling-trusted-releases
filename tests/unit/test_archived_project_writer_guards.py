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
import atr.storage as storage
import atr.storage.writers.announce as announce
import atr.storage.writers.checks as checks
import atr.storage.writers.distributions as distributions
import atr.storage.writers.release as release
import atr.storage.writers.revision as revision
import atr.storage.writers.sbom as sbom
import atr.storage.writers.vote as vote


@pytest.mark.asyncio
async def test_announce_release_blocks_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        announce.util,
        "permitted_announce_recipients",
        lambda _uid, committee_key: {"dev@project.apache.org"},
    )
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    writer = object.__new__(announce.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.release(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            "dev@project.apache.org",
            "body",
            None,
            "tester",
            "Chair",
        )


@pytest.mark.asyncio
async def test_checks_ignore_add_propagates_chokepoint_block() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(checks.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.ignore_add(safe.ProjectKey("project"))


@pytest.mark.asyncio
async def test_distributions_automate_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(distributions.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    platform = next(iter(sql.DistributionPlatform))
    with pytest.raises(storage.AccessError, match="archived"):
        await writer.automate(
            safe.ReleaseKey("project-1.0.0"),
            platform,
            "project",
            None,
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            "release",
            None,
            safe.Alphanumeric("pkg"),
            safe.VersionKey("1.0.0"),
            False,
        )


@pytest.mark.asyncio
async def test_distributions_record_blocks_retired_via_release_lookup() -> None:
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    writer = object.__new__(distributions.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "tester"
    writer._CommitteeMember__committee_key = "project"

    platform = next(iter(sql.DistributionPlatform))
    with pytest.raises(storage.AccessError, match="archived"):
        await writer.record(
            safe.ReleaseKey("project-1.0.0"),
            platform,
            None,
            safe.Alphanumeric("pkg"),
            safe.VersionKey("1.0.0"),
            False,
            False,
            None,
        )


@pytest.mark.asyncio
async def test_foundation_admin_delete_inactive_acquires_lock_and_rechecks_before_delete() -> None:
    import datetime as _dt

    active_project = SimpleNamespace(
        key="project",
        status=sql.ProjectStatus.ACTIVE,
        committee_key="project",
        display_name="Project",
        short_display_name="Project",
    )
    eligible_release = SimpleNamespace(
        key=sql.release_key("project", "1.0.0"),
        project=active_project,
        project_key="project",
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=120),
    )
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(eligible_release))
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.expire_all = mock.MagicMock()
    data.execute = mock.AsyncMock(return_value=mock.MagicMock(scalar_one=mock.MagicMock(return_value=0)))

    writer = object.__new__(release.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__write_as = mock.MagicMock()
    delete = mock.AsyncMock(return_value=None)
    object.__setattr__(writer, "_FoundationAdmin__delete", delete)

    result = await writer.delete_inactive(
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        dry_run=False,
    )
    assert result is None
    data.begin_immediate.assert_awaited_once()
    data.expire_all.assert_called_once()
    delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_foundation_admin_delete_inactive_refresh_catches_concurrent_phase_change() -> None:
    import datetime as _dt

    active_project = SimpleNamespace(
        key="project",
        status=sql.ProjectStatus.ACTIVE,
        committee_key="project",
        display_name="Project",
        short_display_name="Project",
    )
    stale_release = SimpleNamespace(
        key=sql.release_key("project", "1.0.0"),
        project=active_project,
        project_key="project",
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=120),
    )
    fresh_release = SimpleNamespace(
        key=sql.release_key("project", "1.0.0"),
        project=active_project,
        project_key="project",
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        activity_at=_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=120),
    )

    data = mock.MagicMock()
    expired = {"called": False}

    def _on_expire_all() -> None:
        expired["called"] = True

    def _release_query(**kwargs: object) -> mock.MagicMock:
        if expired["called"]:
            return _query_returning(fresh_release)
        return _query_returning(stale_release)

    data.release = mock.MagicMock(side_effect=_release_query)
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    data.expire_all = mock.MagicMock(side_effect=_on_expire_all)
    data.execute = mock.AsyncMock(return_value=mock.MagicMock(scalar_one=mock.MagicMock(return_value=0)))

    writer = object.__new__(release.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__write_as = mock.MagicMock()
    delete = mock.AsyncMock(return_value=None)
    object.__setattr__(writer, "_FoundationAdmin__delete", delete)

    result = await writer.delete_inactive(
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        dry_run=False,
    )
    assert result is not None and "not eligible" in result
    data.begin_immediate.assert_awaited_once()
    data.expire_all.assert_called_once()
    data.rollback.assert_awaited_once()
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_foundation_admin_delete_inactive_rejects_non_active_project() -> None:
    import datetime as _dt

    retired_release = SimpleNamespace(
        key=sql.release_key("project", "1.0.0"),
        project=_retired_project(),
        project_key="project",
        version="1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=_dt.datetime(2025, 1, 1, tzinfo=_dt.UTC),
    )
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(retired_release))
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()

    writer = object.__new__(release.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__write_as = mock.MagicMock()
    delete = mock.AsyncMock(return_value=None)
    object.__setattr__(writer, "_FoundationAdmin__delete", delete)

    error = await writer.delete_inactive(
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        dry_run=True,
    )
    assert error is not None and "not active" in error
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_bump_activity_blocks_ineligible_phase() -> None:
    data = mock.MagicMock()
    candidate = _active_release(phase=sql.ReleasePhase.RELEASE)
    data.release = mock.MagicMock(return_value=_query_returning(candidate))
    data.commit = mock.AsyncMock()
    data.execute = mock.AsyncMock()
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="not eligible"):
        await writer.bump_activity(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))
    data.execute.assert_not_awaited()
    data.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_bump_activity_blocks_retired() -> None:
    data = mock.MagicMock()
    retired = _retired_release()
    retired.activity_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    retired.inactivity_notice_key = "warning:old"
    retired.phase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    data.release = mock.MagicMock(return_value=_query_returning(retired))
    data.commit = mock.AsyncMock()
    data.execute = mock.AsyncMock()
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.bump_activity(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))
    data.execute.assert_not_awaited()
    data.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_bump_activity_clears_future_notice() -> None:
    data = mock.MagicMock()
    future_activity = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
    candidate = _active_release(activity_at=future_activity)
    data.release = mock.MagicMock(return_value=_query_returning(candidate))
    execute_result = mock.MagicMock()
    execute_result.rowcount = 1
    data.execute = mock.AsyncMock(return_value=execute_result)
    data.refresh = mock.AsyncMock(side_effect=lambda obj: _refresh_release_after_bump(obj, future_activity))
    data.commit = mock.AsyncMock()
    write_as = mock.MagicMock()
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__write_as = write_as
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    result = await writer.bump_activity(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))
    assert result is candidate
    assert candidate.activity_at is future_activity
    assert candidate.inactivity_notice_key is None
    data.execute.assert_awaited_once()
    data.refresh.assert_awaited_once_with(candidate)
    data.commit.assert_awaited_once()
    write_as.append_to_audit_log.assert_called_once_with(
        asf_uid="tester",
        project_key="project",
        version="1.0.0",
        previous_activity_at=future_activity.isoformat(),
        activity_at=future_activity.isoformat(),
    )


@pytest.mark.asyncio
async def test_release_bump_activity_rejects_concurrent_state_change() -> None:
    data = mock.MagicMock()
    candidate = _active_release()
    data.release = mock.MagicMock(return_value=_query_returning(candidate))
    execute_result = mock.MagicMock()
    execute_result.rowcount = 0
    data.execute = mock.AsyncMock(return_value=execute_result)
    data.rollback = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    write_as = mock.MagicMock()
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__write_as = write_as
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="state has changed"):
        await writer.bump_activity(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))
    data.rollback.assert_awaited_once()
    data.commit.assert_not_awaited()
    write_as.append_to_audit_log.assert_not_called()


@pytest.mark.asyncio
async def test_release_bump_activity_updates_activity() -> None:
    old_activity = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=85)
    new_activity = old_activity + datetime.timedelta(days=85)
    candidate = _active_release(activity_at=old_activity)
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(candidate))
    execute_result = mock.MagicMock()
    execute_result.rowcount = 1
    data.execute = mock.AsyncMock(return_value=execute_result)
    data.refresh = mock.AsyncMock(side_effect=lambda obj: _refresh_release_after_bump(obj, new_activity))
    data.commit = mock.AsyncMock()
    write_as = mock.MagicMock()
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__write_as = write_as
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    result = await writer.bump_activity(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))
    assert result is candidate
    assert candidate.activity_at == new_activity
    assert candidate.inactivity_notice_key is None
    data.execute.assert_awaited_once()
    data.refresh.assert_awaited_once_with(candidate)
    data.commit.assert_awaited_once()
    write_as.append_to_audit_log.assert_called_once_with(
        asf_uid="tester",
        project_key="project",
        version="1.0.0",
        previous_activity_at=old_activity.isoformat(),
        activity_at=new_activity.isoformat(),
    )


@pytest.mark.asyncio
async def test_release_delete_blocks_retired() -> None:
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.delete(safe.ProjectKey("project"), safe.VersionKey("1.0.0"))


@pytest.mark.asyncio
async def test_release_import_from_svn_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.import_from_svn(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RelPath("svn/url"),
            "r1",
            None,
        )


@pytest.mark.asyncio
async def test_release_promote_to_candidate_blocks_retired() -> None:
    data = mock.MagicMock()
    data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    data.begin_immediate = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    writer = object.__new__(release.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    # promote_to_candidate catches AccessError internally and returns it as a string
    result = await writer.promote_to_candidate(
        safe.ReleaseKey("project-1.0.0"),
        safe.RevisionNumber("00001"),
        allowed_vote_modes=frozenset({sql.VoteMode.MANUAL}),
    )
    assert result is not None and "archived" in result


@pytest.mark.asyncio
async def test_revision_clear_quarantine_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(revision.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.clear_quarantine(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            42,
        )


@pytest.mark.asyncio
async def test_revision_create_revision_with_quarantine_blocks_retired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = object.__new__(revision.CommitteeParticipant)
    writer._CommitteeParticipant__data = mock.MagicMock()
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    inner_data = mock.MagicMock()
    inner_data.release = mock.MagicMock(return_value=_query_returning(_retired_release()))
    monkeypatch.setattr(revision.db, "session", lambda: _async_context_manager(inner_data))

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.create_revision_with_quarantine(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            "tester",
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
        )


@pytest.mark.asyncio
async def test_sbom_augment_blocks_retired() -> None:
    data = mock.MagicMock()
    data.project = mock.MagicMock(return_value=_query_returning(_retired_project()))
    writer = object.__new__(sbom.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"

    with pytest.raises(storage.AccessError, match="archived"):
        await writer.augment_cyclonedx(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            safe.RelPath("artifact.tar.gz"),
        )


@pytest.mark.asyncio
async def test_vote_resolve_release_blocks_retired_after_merge() -> None:
    data = mock.MagicMock()
    merged = _retired_release()
    data.merge = mock.AsyncMock(return_value=merged)
    writer = object.__new__(vote.CommitteeMember)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "tester"
    writer._ReleaseManager__committee_key = "project"

    stub_release = SimpleNamespace(project=SimpleNamespace(status=sql.ProjectStatus.ACTIVE, key="project"))
    with pytest.raises(storage.AccessError, match="archived"):
        await writer._ReleaseManager__resolve_release(
            safe.ProjectKey("project"),
            stub_release,
            None,
            "passed",
            SimpleNamespace(),
            "Chair",
            "body",
        )
    data.merge.assert_awaited_once_with(stub_release)


@pytest.mark.asyncio
async def test_vote_send_resolution_blocks_retired() -> None:
    writer = object.__new__(vote.CommitteeMember)
    writer._ReleaseManager__data = mock.MagicMock()
    writer._ReleaseManager__asf_uid = "tester"
    writer._ReleaseManager__committee_key = "project"

    rel = _retired_release()
    with pytest.raises(storage.AccessError, match="archived"):
        await writer._ReleaseManager__send_resolution(
            rel,
            "passed",
            "body",
            "tester",
            "Chair",
            SimpleNamespace(),
        )


def _active_project(key: str = "project") -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        status=sql.ProjectStatus.ACTIVE,
        committee_key=key,
        display_name=key.capitalize(),
        short_display_name=key.capitalize(),
    )


def _active_release(
    phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
    activity_at: datetime.datetime | None = None,
) -> SimpleNamespace:
    activity_at_missing = activity_at is None
    if activity_at_missing:
        activity_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=85)
    return SimpleNamespace(
        key=sql.release_key("project", "1.0.0"),
        project=_active_project(),
        project_key="project",
        version="1.0.0",
        phase=phase,
        activity_at=activity_at,
        inactivity_notice_key="warning:old",
    )


def _async_context_manager(value: object) -> mock.MagicMock:
    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=value)
    ctx.__aexit__ = mock.AsyncMock(return_value=None)
    return ctx


def _query_returning(obj: object) -> mock.MagicMock:
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=obj)
    query.get = mock.AsyncMock(return_value=obj)
    return query


def _refresh_release_after_bump(release_obj: SimpleNamespace, activity_at: datetime.datetime) -> None:
    release_obj.activity_at = activity_at
    release_obj.inactivity_notice_key = None


def _retired_project(key: str = "project") -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        status=sql.ProjectStatus.RETIRED,
        committee_key=key,
        display_name=key.capitalize(),
        short_display_name=key.capitalize(),
    )


def _retired_release(project_key: str = "project", version: str = "1.0.0") -> SimpleNamespace:
    return SimpleNamespace(
        key=sql.release_key(project_key, version),
        project=_retired_project(project_key),
        version=version,
    )
