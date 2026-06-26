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

from __future__ import annotations

import dataclasses
import datetime
import unittest.mock as mock
from typing import Any

import pytest

import atr.models.sql as sql
import atr.tasks.inactivity as inactivity


@dataclasses.dataclass
class FakeProject:
    key: str = "example"
    status: sql.ProjectStatus = sql.ProjectStatus.ACTIVE
    committee_key: str = "example"


@dataclasses.dataclass
class FakeRelease:
    key: str
    project_key: str
    version: str
    phase: sql.ReleasePhase
    activity_at: datetime.datetime
    vote_started: datetime.datetime | None = None
    vote_resolved: datetime.datetime | None = None
    revisions: list[Any] = dataclasses.field(default_factory=list)
    project: FakeProject = dataclasses.field(default_factory=FakeProject)


def test_classify_overdue_candidate_is_delete_candidate() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE, activity_age_days=120)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.DELETE_CANDIDATE


def test_classify_overdue_draft_is_delete_candidate() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=100)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.DELETE_CANDIDATE


def test_classify_overdue_preview_escalates() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_PREVIEW, activity_age_days=200)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.PREVIEW_ESCALATE


def test_classify_preview_warning_window_warns() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_PREVIEW, activity_age_days=85)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.WARN
    assert plan.phase == sql.ReleasePhase.RELEASE_PREVIEW


def test_classify_release_phase_skips() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE, activity_age_days=500)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.SKIP


def test_classify_under_warning_skips() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=10)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.SKIP


def test_classify_warning_boundary_warns() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=80)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.WARN


def test_classify_warning_zone_warns() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=85)
    plan = inactivity.classify(release, now=_now())
    assert plan.decision == inactivity.Decision.WARN


def test_deletion_enabled_by_default() -> None:
    assert inactivity.deletion_enabled() is True


def test_notice_already_recorded_none_returns_false() -> None:
    assert inactivity._notice_already_recorded(None, "warning:x") is False


def test_notice_already_recorded_requires_exact_match_for_normal_warning() -> None:
    stored = "warning:2026-02-01T00:00:00+00:00"
    fresh = "warning:2026-03-01T00:00:00+00:00"
    assert inactivity._notice_already_recorded(stored, fresh) is False
    assert inactivity._notice_already_recorded(stored, stored) is True


def test_notice_key_format_round_trips_activity_at() -> None:
    activity_at = datetime.datetime(2026, 5, 21, 0, 0, tzinfo=datetime.UTC)
    key = inactivity._notice_key(inactivity.NOTICE_KIND_WARNING, activity_at)
    assert key == f"{inactivity.NOTICE_KIND_WARNING}:{activity_at.isoformat()}"


def test_plan_still_current_allows_preview_warning() -> None:
    activity_at = _now() - datetime.timedelta(days=85)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )
    release = _release(phase=sql.ReleasePhase.RELEASE_PREVIEW, activity_age_days=85)
    release.activity_at = activity_at
    assert inactivity._plan_still_current(plan, release, expected_kind=inactivity.NOTICE_KIND_WARNING) is True


def test_plan_still_current_passes_for_matching_state() -> None:
    activity_at = _now() - datetime.timedelta(days=85)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=85)
    release.activity_at = activity_at
    assert inactivity._plan_still_current(plan, release, expected_kind=inactivity.NOTICE_KIND_WARNING) is True


def test_plan_still_current_skips_on_activity_advance() -> None:
    activity_at = _now() - datetime.timedelta(days=85)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=1)
    assert inactivity._plan_still_current(plan, release, expected_kind=inactivity.NOTICE_KIND_WARNING) is False


def test_plan_still_current_skips_on_archived_project() -> None:
    activity_at = _now() - datetime.timedelta(days=85)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )
    release = _release(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_age_days=85,
        project_status=sql.ProjectStatus.RETIRED,
    )
    release.activity_at = activity_at
    assert inactivity._plan_still_current(plan, release, expected_kind=inactivity.NOTICE_KIND_WARNING) is False


def test_plan_still_current_skips_on_phase_change() -> None:
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=_now() - datetime.timedelta(days=85),
        decision=inactivity.Decision.WARN,
    )
    release = _release(phase=sql.ReleasePhase.RELEASE, activity_age_days=85)
    assert inactivity._plan_still_current(plan, release, expected_kind=inactivity.NOTICE_KIND_WARNING) is False


def test_plan_still_current_skips_preview_escalation_on_archived_project() -> None:
    activity_at = _now() - datetime.timedelta(days=120)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        activity_at=activity_at,
        decision=inactivity.Decision.PREVIEW_ESCALATE,
    )
    release = _release(
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        activity_age_days=120,
        project_status=sql.ProjectStatus.RETIRED,
    )
    release.activity_at = activity_at
    assert (
        inactivity._plan_still_current(plan, release, expected_kind=inactivity.NOTICE_KIND_PREVIEW_ESCALATION) is False
    )


def test_preview_escalation_body_uses_threshold_not_synthetic_date() -> None:
    body = inactivity._preview_escalation_body(_warning_plan(sql.ReleasePhase.RELEASE_PREVIEW))
    lowered = body.lower()
    assert "inactive for at least 90 days" in lowered
    assert "is being\nflagged" in lowered
    assert "2026-02-25" not in lowered


def test_private_committee_list_format() -> None:
    assert inactivity.private_committee_list("example") == "private@example.apache.org"


@pytest.mark.asyncio
async def test_send_email_returns_false_on_smtp_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inactivity.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(inactivity.storage, "audit", mock.MagicMock())
    monkeypatch.setattr(
        inactivity.mail,
        "send",
        mock.AsyncMock(return_value=("mid-456", ["550 rejected"])),
    )
    sent = await inactivity._send_email(recipient="alice@apache.org", subject="x", body="y")
    assert sent is False


@pytest.mark.asyncio
async def test_send_email_returns_true_when_no_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inactivity.config, "is_dev_environment", lambda: False)
    monkeypatch.setattr(inactivity.storage, "audit", mock.MagicMock())
    monkeypatch.setattr(inactivity.mail, "send", mock.AsyncMock(return_value=("mid-789", [])))
    sent = await inactivity._send_email(recipient="alice@apache.org", subject="x", body="y")
    assert sent is True


@pytest.mark.asyncio
async def test_send_warning_does_not_record_on_partial_recipient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_at = _now() - datetime.timedelta(days=85)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )

    release_stub = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=85)
    release_stub.activity_at = activity_at
    release_stub.inactivity_notice_key = None

    data = mock.MagicMock()
    monkeypatch.setattr(inactivity, "_load_release", mock.AsyncMock(return_value=release_stub))
    monkeypatch.setattr(
        inactivity,
        "warning_recipients_for",
        mock.AsyncMock(return_value=["alice@apache.org", "bob@apache.org"]),
    )
    monkeypatch.setattr(inactivity.db, "session", lambda: _async_context_manager(data))

    send_mock = mock.AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(inactivity, "_send_email", send_mock)
    record_mock = mock.AsyncMock()
    monkeypatch.setattr(inactivity, "_record_notice_sent", record_mock)

    await inactivity._send_warning(plan)

    assert send_mock.await_count == 2
    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_warning_does_not_record_when_mail_send_returns_smtp_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_at = _now() - datetime.timedelta(days=85)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )

    release_stub = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=85)
    release_stub.activity_at = activity_at
    release_stub.inactivity_notice_key = None

    data = mock.MagicMock()
    monkeypatch.setattr(inactivity, "_load_release", mock.AsyncMock(return_value=release_stub))
    monkeypatch.setattr(
        inactivity,
        "warning_recipients_for",
        mock.AsyncMock(return_value=["alice@apache.org"]),
    )
    monkeypatch.setattr(inactivity.db, "session", lambda: _async_context_manager(data))

    audit_mock = mock.MagicMock()
    monkeypatch.setattr(inactivity.storage, "audit", audit_mock)
    monkeypatch.setattr(inactivity.config, "is_dev_environment", lambda: False)

    mail_send = mock.AsyncMock(return_value=("mid-123", ["550 mailbox unavailable"]))
    monkeypatch.setattr(inactivity.mail, "send", mail_send)
    record_mock = mock.AsyncMock()
    monkeypatch.setattr(inactivity, "_record_notice_sent", record_mock)

    await inactivity._send_warning(plan)

    mail_send.assert_awaited_once()
    audit_mock.assert_called_once()
    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_warning_records_when_all_recipients_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_at = _now() - datetime.timedelta(days=85)
    plan = inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )

    release_stub = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=85)
    release_stub.activity_at = activity_at
    release_stub.inactivity_notice_key = None

    data = mock.MagicMock()
    monkeypatch.setattr(inactivity, "_load_release", mock.AsyncMock(return_value=release_stub))
    monkeypatch.setattr(
        inactivity,
        "warning_recipients_for",
        mock.AsyncMock(return_value=["alice@apache.org", "bob@apache.org"]),
    )
    monkeypatch.setattr(inactivity.db, "session", lambda: _async_context_manager(data))

    send_mock = mock.AsyncMock(return_value=True)
    monkeypatch.setattr(inactivity, "_send_email", send_mock)
    record_mock = mock.AsyncMock()
    monkeypatch.setattr(inactivity, "_record_notice_sent", record_mock)

    await inactivity._send_warning(plan)

    assert send_mock.await_count == 2
    record_mock.assert_awaited_once_with(plan.release_key, inactivity.NOTICE_KIND_WARNING, activity_at)


def test_thresholds_are_80_and_90() -> None:
    warning_days, delete_days = inactivity.thresholds()
    assert warning_days == 80
    assert delete_days == 90


def test_warning_body_for_candidate_keeps_cleanup_copy() -> None:
    body = inactivity._warning_body(_warning_plan(sql.ReleasePhase.RELEASE_CANDIDATE))
    lowered = body.lower()
    assert "80 days" in lowered
    assert "will be deleted in 10 days" in lowered
    assert "flagged" not in lowered
    assert "2026-02-25" not in lowered


def test_warning_body_for_draft_keeps_cleanup_copy() -> None:
    body = inactivity._warning_body(_warning_plan(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT))
    lowered = body.lower()
    assert "80 days" in lowered
    assert "will be deleted in 10 days" in lowered
    assert "flagged" not in lowered
    assert "2026-02-25" not in lowered


def test_warning_body_for_preview_does_not_say_deleted() -> None:
    body = inactivity._warning_body(_warning_plan(sql.ReleasePhase.RELEASE_PREVIEW))
    lowered = body.lower()
    assert "80 days" in lowered
    assert "will be deleted in" not in lowered
    assert "flagged in\n10 days" in lowered
    assert "not automatically deleted" in lowered
    assert "2026-02-25" not in lowered


def _async_context_manager(value: object) -> mock.MagicMock:
    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=value)
    ctx.__aexit__ = mock.AsyncMock(return_value=None)
    return ctx


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 5, 21, 12, 0, tzinfo=datetime.UTC)


def _release(
    *,
    phase: sql.ReleasePhase,
    activity_age_days: int,
    project_status: sql.ProjectStatus = sql.ProjectStatus.ACTIVE,
) -> FakeRelease:
    now = _now()
    activity_at = now - datetime.timedelta(days=activity_age_days)
    return FakeRelease(
        key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=phase,
        activity_at=activity_at,
        project=FakeProject(key="example", status=project_status, committee_key="example"),
    )


def _warning_plan(phase: sql.ReleasePhase) -> inactivity.Plan:
    activity_at = _now() - datetime.timedelta(days=85)
    return inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=phase,
        activity_at=activity_at,
        decision=inactivity.Decision.WARN,
    )
