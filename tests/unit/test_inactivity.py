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
import sqlalchemy
import sqlmodel

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
    created: datetime.datetime
    vote_started: datetime.datetime | None = None
    vote_resolved: datetime.datetime | None = None
    revisions: list[Any] = dataclasses.field(default_factory=list)
    project: FakeProject = dataclasses.field(default_factory=FakeProject)


def test_classify_legacy_only_when_historical_overdue() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=1)
    historical = _now() - datetime.timedelta(days=70)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=historical)
    assert plan.decision == inactivity.Decision.SKIP


def test_classify_legacy_warns_when_fresh_activity_but_old_history() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=1, created_age_days=200)
    historical = _now() - datetime.timedelta(days=200)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=historical)
    assert plan.decision == inactivity.Decision.LEGACY_WARN
    assert plan.activity_at == historical


def test_classify_overdue_candidate_is_delete_candidate() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE, activity_age_days=120)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=None)
    assert plan.decision == inactivity.Decision.DELETE_CANDIDATE


def test_classify_overdue_draft_is_delete_candidate() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=100)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=None)
    assert plan.decision == inactivity.Decision.DELETE_CANDIDATE


def test_classify_overdue_preview_escalates() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_PREVIEW, activity_age_days=200)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=None)
    assert plan.decision == inactivity.Decision.PREVIEW_ESCALATE


def test_classify_preview_warning_window_warns() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_PREVIEW, activity_age_days=85)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=None)
    assert plan.decision == inactivity.Decision.WARN
    assert plan.phase == sql.ReleasePhase.RELEASE_PREVIEW


def test_classify_release_phase_skips() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE, activity_age_days=500)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=None)
    assert plan.decision == inactivity.Decision.SKIP


def test_classify_under_warning_skips() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=10)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=None)
    assert plan.decision == inactivity.Decision.SKIP


def test_classify_warning_zone_warns() -> None:
    release = _release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, activity_age_days=85)
    plan = inactivity.classify(release, now=_now(), legacy_historical_activity=None)
    assert plan.decision == inactivity.Decision.WARN


def test_deletion_disabled_by_default() -> None:
    assert inactivity.deletion_enabled() is False


def test_legacy_warning_body_for_candidate_keeps_cleanup_copy() -> None:
    body = inactivity._legacy_warning_body(_legacy_warning_plan(sql.ReleasePhase.RELEASE_CANDIDATE))
    lowered = body.lower()
    assert "cleaned up automatically" in lowered
    assert "escalated to the pmc" not in lowered


def test_legacy_warning_body_for_preview_does_not_say_deleted() -> None:
    body = inactivity._legacy_warning_body(_legacy_warning_plan(sql.ReleasePhase.RELEASE_PREVIEW))
    lowered = body.lower()
    assert "cleaned up automatically" not in lowered
    assert "not automatically deleted" in lowered
    assert "escalated to the pmc" in lowered


def test_notice_already_recorded_none_returns_false() -> None:
    assert inactivity._notice_already_recorded(None, inactivity.NOTICE_KIND_LEGACY_WARNING, "legacy_warning:x") is False
    assert inactivity._notice_already_recorded(None, inactivity.NOTICE_KIND_WARNING, "warning:x") is False


def test_notice_already_recorded_recognises_any_legacy_marker() -> None:
    stored = "legacy_warning:2025-12-01T00:00:00+00:00"
    fresh = "legacy_warning:2025-12-02T00:00:00+00:00"
    assert inactivity._notice_already_recorded(stored, inactivity.NOTICE_KIND_LEGACY_WARNING, fresh) is True


def test_notice_already_recorded_recognises_combined_markers() -> None:
    legacy = "legacy_warning:2025-12-01T00:00:00+00:00"
    warning = "warning:2026-03-01T00:00:00+00:00"
    stored = f"{legacy}{sql.INACTIVITY_NOTICE_KEY_SEPARATOR}{warning}"
    assert inactivity._notice_already_recorded(stored, inactivity.NOTICE_KIND_LEGACY_WARNING, legacy) is True
    assert inactivity._notice_already_recorded(stored, inactivity.NOTICE_KIND_WARNING, warning) is True


def test_notice_already_recorded_requires_exact_match_for_normal_warning() -> None:
    stored = "warning:2026-02-01T00:00:00+00:00"
    fresh = "warning:2026-03-01T00:00:00+00:00"
    assert inactivity._notice_already_recorded(stored, inactivity.NOTICE_KIND_WARNING, fresh) is False
    assert inactivity._notice_already_recorded(stored, inactivity.NOTICE_KIND_WARNING, stored) is True


def test_notice_key_format_round_trips_activity_at() -> None:
    activity_at = datetime.datetime(2026, 5, 21, 0, 0, tzinfo=datetime.UTC)
    key = inactivity._notice_key(inactivity.NOTICE_KIND_WARNING, activity_at)
    assert key == f"{inactivity.NOTICE_KIND_WARNING}:{activity_at.isoformat()}"


def test_notice_key_merge_adds_legacy_without_losing_current_window() -> None:
    legacy = "legacy_warning:2025-12-01T00:00:00+00:00"
    warning = "warning:2026-03-01T00:00:00+00:00"
    merged = inactivity._notice_key_merge(warning, legacy)
    assert merged == f"{legacy}{sql.INACTIVITY_NOTICE_KEY_SEPARATOR}{warning}"


def test_notice_key_merge_preserves_legacy_and_replaces_current_window() -> None:
    legacy = "legacy_warning:2025-12-01T00:00:00+00:00"
    old_warning = "warning:2026-03-01T00:00:00+00:00"
    new_warning = "warning:2026-04-01T00:00:00+00:00"
    stored = f"{legacy}{sql.INACTIVITY_NOTICE_KEY_SEPARATOR}{old_warning}"
    merged = inactivity._notice_key_merge(stored, new_warning)
    assert merged == f"{legacy}{sql.INACTIVITY_NOTICE_KEY_SEPARATOR}{new_warning}"


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

    await inactivity._send_warning(plan, kind=inactivity.NOTICE_KIND_WARNING)

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

    await inactivity._send_warning(plan, kind=inactivity.NOTICE_KIND_WARNING)

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

    await inactivity._send_warning(plan, kind=inactivity.NOTICE_KIND_WARNING)

    assert send_mock.await_count == 2
    record_mock.assert_awaited_once_with(plan.release_key, inactivity.NOTICE_KIND_WARNING, activity_at)


def test_sql_legacy_notice_key_expression_extracts_only_durable_marker() -> None:
    legacy = "legacy_warning:2025-12-01T00:00:00+00:00"
    warning = "warning:2026-03-01T00:00:00+00:00"
    stored = f"{legacy}{sql.INACTIVITY_NOTICE_KEY_SEPARATOR}{warning}"
    engine = sqlmodel.create_engine("sqlite://")
    with engine.connect() as connection:
        result = connection.execute(
            sqlalchemy.select(sql.inactivity_notice_legacy_key_expression(sqlalchemy.literal(stored)))
        )
        assert result.scalar_one() == legacy


def test_sql_legacy_notice_key_extracts_only_durable_marker() -> None:
    legacy = "legacy_warning:2025-12-01T00:00:00+00:00"
    warning = "warning:2026-03-01T00:00:00+00:00"
    stored = f"{legacy}{sql.INACTIVITY_NOTICE_KEY_SEPARATOR}{warning}"
    assert sql.inactivity_notice_legacy_key(stored) == legacy
    assert sql.inactivity_notice_legacy_key(warning) is None
    assert sql.inactivity_notice_legacy_key(None) is None


def test_thresholds_are_80_and_90() -> None:
    warning_days, delete_days = inactivity.thresholds()
    assert warning_days == 80
    assert delete_days == 90


def test_warning_body_for_candidate_keeps_cleanup_copy() -> None:
    body = inactivity._warning_body(_warning_plan(sql.ReleasePhase.RELEASE_CANDIDATE))
    lowered = body.lower()
    assert "cleaned up automatically" in lowered
    assert "escalated to the pmc" not in lowered


def test_warning_body_for_draft_keeps_cleanup_copy() -> None:
    body = inactivity._warning_body(_warning_plan(sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT))
    lowered = body.lower()
    assert "cleaned up automatically" in lowered
    assert "escalated to the pmc" not in lowered


def test_warning_body_for_preview_does_not_say_deleted() -> None:
    body = inactivity._warning_body(_warning_plan(sql.ReleasePhase.RELEASE_PREVIEW))
    lowered = body.lower()
    assert "cleaned up automatically" not in lowered
    assert "escalated to the pmc" in lowered
    assert "not\nautomatically deleted" in lowered


def _async_context_manager(value: object) -> mock.MagicMock:
    ctx = mock.MagicMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=value)
    ctx.__aexit__ = mock.AsyncMock(return_value=None)
    return ctx


def _legacy_warning_plan(phase: sql.ReleasePhase) -> inactivity.Plan:
    activity_at = _now() - datetime.timedelta(days=120)
    return inactivity.Plan(
        release_key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=phase,
        activity_at=activity_at,
        decision=inactivity.Decision.LEGACY_WARN,
    )


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 5, 21, 12, 0, tzinfo=datetime.UTC)


def _release(
    *,
    phase: sql.ReleasePhase,
    activity_age_days: int,
    created_age_days: int | None = None,
    project_status: sql.ProjectStatus = sql.ProjectStatus.ACTIVE,
) -> FakeRelease:
    now = _now()
    activity_at = now - datetime.timedelta(days=activity_age_days)
    created = now - datetime.timedelta(days=created_age_days if (created_age_days is not None) else activity_age_days)
    return FakeRelease(
        key="example-0.0.1",
        project_key="example",
        version="0.0.1",
        phase=phase,
        activity_at=activity_at,
        created=created,
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
