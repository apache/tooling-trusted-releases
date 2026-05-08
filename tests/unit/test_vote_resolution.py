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

import contextlib
import datetime
import unittest.mock as mock
from types import SimpleNamespace

import pytest
import quart
import sqlalchemy.engine as engine

import atr.api
import atr.db.interaction as interaction
import atr.get.manual as manual
import atr.get.resolve as resolve
import atr.get.vote
import atr.htm as htm
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.tabulate as models_tabulate
import atr.sessions as sessions
import atr.shared as shared
import atr.storage as storage
import atr.storage.writers.vote as vote


@pytest.fixture
def render_app() -> quart.Quart:
    app = quart.Quart(__name__)
    app.secret_key = "test-secret"
    app.config["TESTING"] = True
    return app


def test_automatic_vote_resolve_section_links_to_standard_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-manual vote releases link to the standard resolution page."""

    def fake_as_url(endpoint, **kwargs) -> str:
        if endpoint is manual.resolve_selected:
            return f"/manual/resolve/{kwargs['project_key']}/{kwargs['version_key']}"
        if endpoint is resolve.selected:
            return f"/resolve/{kwargs['project_key']}/{kwargs['version_key']}"
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    monkeypatch.setattr(atr.get.vote.util, "as_url", fake_as_url)

    page = htm.Block()
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        vote_mode=sql.VoteMode.EMAIL,
        effective_vote_mode=sql.VoteMode.EMAIL,
        release_policy=SimpleNamespace(vote_mode=sql.VoteMode.EMAIL),
        project=SimpleNamespace(key="project"),
        version="1.0.0",
    )

    atr.get.vote._render_section_resolve(page, release, atr.get.vote.UserCategory.COMMITTER_RM)

    html = str(page.collect())
    assert 'href="/resolve/project/1.0.0"' in html
    assert 'href="/manual/resolve/project/1.0.0"' not in html


@pytest.mark.asyncio
async def test_cancelled_resolve_release_clears_podling_thread_id() -> None:
    """Candidate cancelled clears podling_thread_id."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release(podling_thread_id="abc123")
    data.merge = mock.AsyncMock(return_value=release)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        vote_mode=None,
        vote_resolved=None,
        podling_thread_id=None,
    )

    await writer.resolve_release(
        _project_key(),
        release,
        None,
        "cancelled",
        _latest_vote_task(),
        "Chair",
        "The vote has been cancelled.",
    )

    assert release.podling_thread_id is None


@pytest.mark.asyncio
async def test_cancelled_resolve_release_produces_correct_message() -> None:
    """Candidate cancelled produces 'Vote marked as cancelled'."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    data.merge = mock.AsyncMock(return_value=release)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        vote_mode=None,
        vote_resolved=None,
        podling_thread_id=None,
    )

    _release, _round, success, _error = await writer.resolve_release(
        _project_key(),
        release,
        None,
        "cancelled",
        _latest_vote_task(),
        "Chair",
        "The vote has been cancelled.",
    )

    assert success == "Vote marked as cancelled"


@pytest.mark.asyncio
async def test_cancelled_resolve_release_returns_to_draft() -> None:
    """Candidate cancelled returns the release to draft and does not create a preview revision."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    data.merge = mock.AsyncMock(return_value=release)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        vote_mode=None,
        vote_resolved=None,
        podling_thread_id=None,
    )

    _release, _round, success, _error = await writer.resolve_release(
        _project_key(),
        release,
        None,
        "cancelled",
        _latest_vote_task(),
        "Chair",
        "The vote has been cancelled.",
    )

    assert release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    assert release.vote_mode is None
    assert release.vote_resolved is None
    assert success == "Vote marked as cancelled"
    write_as.revision.create_revision_with_quarantine.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_resolve_page_does_not_query_ballot_receipts(monkeypatch: pytest.MonkeyPatch) -> None:
    ballot_receipt_message_ids = mock.AsyncMock(return_value={"receipt@apache.org"})

    _context, _form_render, _archive_lookup, _vote_committee, vote_details = await _render_standard_resolve_page(
        monkeypatch,
        ballot_receipt_message_ids=ballot_receipt_message_ids,
    )

    ballot_receipt_message_ids.assert_not_awaited()
    vote_details.assert_awaited_once()
    assert vote_details.await_args.kwargs["excluded_message_ids"] is None


@pytest.mark.asyncio
async def test_failed_resolve_release_clears_podling_thread_id() -> None:
    """Candidate failed also clears podling_thread_id (bug fix)."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release(podling_thread_id="abc123")
    data.merge = mock.AsyncMock(return_value=release)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        vote_mode=None,
        vote_resolved=None,
        podling_thread_id=None,
    )

    await writer.resolve_release(
        _project_key(),
        release,
        None,
        "failed",
        _latest_vote_task(),
        "Chair",
        "The vote has failed.",
    )

    assert release.podling_thread_id is None
    assert release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    assert release.vote_mode is None
    assert release.vote_resolved is None


def test_format_utc_returns_utc_minute_string() -> None:
    assert resolve.format_utc(datetime.datetime(2026, 1, 2, 3, 4)) == "2026-01-02 03:04 UTC"
    offset = datetime.timezone(datetime.timedelta(hours=1))
    assert resolve.format_utc(datetime.datetime(2026, 1, 2, 4, 4, tzinfo=offset)) == "2026-01-02 03:04 UTC"


@pytest.mark.asyncio
async def test_manual_cancelled_returns_to_draft_and_clears_podling_thread_id() -> None:
    """Manual cancelled returns the release to draft and clears podling_thread_id."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _manual_candidate_release(podling_thread_id="thread123")
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        vote_mode=None,
        vote_resolved=None,
        podling_thread_id=None,
    )

    success = await writer.resolve_manually(
        _project_key(),
        _version_key(),
        "cancelled",
    )

    assert release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    assert release.vote_mode is None
    assert release.vote_resolved is None
    assert release.podling_thread_id is None
    assert success == "Vote marked as cancelled"
    write_as.revision.create_revision_with_quarantine.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_failed_returns_to_draft_and_clears_podling_thread_id() -> None:
    """Manual failed returns the release to draft and clears podling_thread_id (bug fix)."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _manual_candidate_release(podling_thread_id="thread123")
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        vote_mode=None,
        vote_resolved=None,
        podling_thread_id=None,
    )

    success = await writer.resolve_manually(
        _project_key(),
        _version_key(),
        "failed",
    )

    assert release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    assert release.vote_mode is None
    assert release.vote_resolved is None
    assert release.podling_thread_id is None
    assert success == "Vote marked as failed"


@pytest.mark.asyncio
async def test_manual_passed_creates_preview_revision() -> None:
    """Manual passed promotes to preview and creates a revision."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _manual_candidate_release()
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_PREVIEW, vote_resolved=datetime.datetime.now(datetime.UTC)
    )

    success = await writer.resolve_manually(
        _project_key(),
        _version_key(),
        "passed",
    )

    assert release.phase == sql.ReleasePhase.RELEASE_PREVIEW
    assert release.vote_resolved is not None
    assert success == "Vote marked as passed"
    write_as.revision.create_revision_with_quarantine.assert_awaited_once()
    revision_call = write_as.revision.create_revision_with_quarantine.await_args
    assert revision_call is not None
    assert revision_call.kwargs["allowed_phases"] == frozenset({sql.ReleasePhase.RELEASE_PREVIEW})


@pytest.mark.asyncio
async def test_manual_resolve_page_explains_cancellation_notice_url(
    monkeypatch: pytest.MonkeyPatch, render_app: quart.Quart
) -> None:
    """Manual resolve page explains which thread URL to provide when cancelling."""

    monkeypatch.setattr(
        manual.util,
        "as_url",
        lambda _endpoint, **_kwargs: "/vote/project/1.0.0",
    )

    async def _no_form_errors(_path: str) -> dict[str, object]:
        return {}

    monkeypatch.setattr(sessions, "form_error_pop", _no_form_errors)

    release = SimpleNamespace(
        project=SimpleNamespace(key="project"),
        version="1.0.0",
        short_display_name="Project 1.0.0",
    )

    async with render_app.test_request_context("/manual/resolve/project/1.0.0"):
        html = str(await manual._render_resolve_page(release))

    assert "manual vote resolution" in html
    assert "where you posted the result" in html
    assert "cancellation notice" in html
    assert "does not store them" in html


@pytest.mark.asyncio
async def test_manual_resolve_rejects_concurrent_modification() -> None:
    """Manual resolve raises AccessError when the release was modified concurrently."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _manual_candidate_release()
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.execute = mock.AsyncMock(return_value=_mock_cursor_result(rowcount=0))

    with pytest.raises(storage.AccessError, match="release state has changed"):
        await writer.resolve_manually(
            _project_key(),
            _version_key(),
            "passed",
        )

    data.rollback.assert_awaited()
    write_as.revision.create_revision_with_quarantine.assert_not_awaited()


def test_manual_vote_resolve_section_links_to_manual_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manual vote releases link to the manual resolution page."""

    def fake_as_url(endpoint, **kwargs) -> str:
        if endpoint is manual.resolve_selected:
            return f"/manual/resolve/{kwargs['project_key']}/{kwargs['version_key']}"
        if endpoint is resolve.selected:
            return f"/resolve/{kwargs['project_key']}/{kwargs['version_key']}"
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    monkeypatch.setattr(atr.get.vote.util, "as_url", fake_as_url)

    page = htm.Block()
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        vote_mode=sql.VoteMode.MANUAL,
        effective_vote_mode=sql.VoteMode.MANUAL,
        release_policy=SimpleNamespace(vote_mode=sql.VoteMode.MANUAL),
        project=SimpleNamespace(key="project"),
        version="1.0.0",
    )

    atr.get.vote._render_section_resolve(page, release, atr.get.vote.UserCategory.COMMITTER_RM)

    html = str(page.collect())
    assert 'href="/manual/resolve/project/1.0.0"' in html
    assert 'href="/resolve/project/1.0.0"' not in html


@pytest.mark.asyncio
async def test_podling_double_pass_raises_error() -> None:
    """A second podling round 1 pass raises AccessError when the first already set podling_thread_id."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    data.merge = mock.AsyncMock(return_value=release)
    data.execute = mock.AsyncMock(return_value=_mock_cursor_result(rowcount=0))
    write_as.cache.get_message_archive_url = mock.AsyncMock(return_value="https://lists.apache.org/thread/abc123")

    with pytest.raises(storage.AccessError, match="release state has changed"):
        await writer.resolve_release(
            _project_key(),
            release,
            1,
            "passed",
            _latest_vote_task(),
            "Chair",
            "The vote has passed.",
        )

    data.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_podling_stale_round_one_cancel_after_pass() -> None:
    """A stale cancel after another user passes podling round 1 raises AccessError."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    data.merge = mock.AsyncMock(return_value=release)
    data.execute = mock.AsyncMock(return_value=_mock_cursor_result(rowcount=0))

    with pytest.raises(storage.AccessError, match="release state has changed"):
        await writer.resolve_release(
            _project_key(),
            release,
            1,
            "cancelled",
            _latest_vote_task(),
            "Chair",
            "The vote has been cancelled.",
        )

    data.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_resolve_allows_cancelled_before_vote_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writer allows Cancelled even before the end of the vote."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.merge = mock.AsyncMock(return_value=release)

    future_task = _latest_vote_task_with_end(24)
    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=future_task))
    monkeypatch.setattr(interaction, "vote_duration_bypass", lambda: False)

    writer.resolve_release = mock.AsyncMock(return_value=(release, None, "Vote marked as cancelled", None))

    _release, _round, success, _error = await writer.resolve(
        _project_key(),
        _version_key(),
        "cancelled",
        "Chair",
        "The vote has been cancelled.",
    )

    assert success == "Vote marked as cancelled"


@pytest.mark.asyncio
async def test_resolve_allows_early_passed_with_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writer allows Passed before the end of the vote when bypass is active."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.merge = mock.AsyncMock(return_value=release)

    future_task = _latest_vote_task_with_end(24)
    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=future_task))
    monkeypatch.setattr(interaction, "vote_duration_bypass", lambda: True)

    writer.resolve_release = mock.AsyncMock(return_value=(release, None, "Vote marked as passed", None))

    _release, _round, success, _error = await writer.resolve(
        _project_key(),
        _version_key(),
        "passed",
        "Chair",
        "The vote has passed.",
    )

    assert success == "Vote marked as passed"


@pytest.mark.asyncio
async def test_resolve_allows_passed_after_vote_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writer allows Passed after the end of the vote has elapsed."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.merge = mock.AsyncMock(return_value=release)

    past_task = _latest_vote_task_with_end(-24)
    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=past_task))
    monkeypatch.setattr(interaction, "vote_duration_bypass", lambda: False)

    writer.resolve_release = mock.AsyncMock(return_value=(release, None, "Vote marked as passed", None))

    _release, _round, success, _error = await writer.resolve(
        _project_key(),
        _version_key(),
        "passed",
        "Chair",
        "The vote has passed.",
    )

    assert success == "Vote marked as passed"


@pytest.mark.asyncio
async def test_resolve_page_allows_manual_continuation_when_archive_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = resolve.util.FetchError(
        "Failed to look up archive URL",
        url="https://lists.apache.org/api/email.json",
    )

    context, form_render, archive_lookup, vote_committee, vote_details = await _render_standard_resolve_page(
        monkeypatch,
        archive_error=error,
    )

    assert context["fetch_error"] == (
        "ATR could not look up the archived vote thread on lists.apache.org. "
        "Please review the vote manually and continue below."
    )
    assert context["resolve_form"] == "FORM"
    assert form_render.call_args.kwargs["defaults"] == {"vote_mode": sql.VoteMode.EMAIL, "vote_seq": None}
    assert form_render.call_args.kwargs["pre_submit"] is not None
    assert archive_lookup.await_args.kwargs["strict"] is True
    vote_committee.assert_not_awaited()
    vote_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_page_allows_manual_continuation_when_tabulation_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, form_render, archive_lookup, vote_committee, vote_details = await _render_standard_resolve_page(
        monkeypatch,
        details_error=ValueError("Thread exceeds maximum of 10000 messages"),
    )

    assert context["fetch_error"] == (
        "ATR could not tabulate the archived vote thread automatically. "
        "Please review the vote manually and continue below."
    )
    assert context["resolve_form"] == "FORM"
    assert form_render.call_args.kwargs["defaults"] == {"vote_mode": sql.VoteMode.EMAIL, "vote_seq": None}
    assert form_render.call_args.kwargs["pre_submit"] is not None
    assert archive_lookup.await_args.kwargs["strict"] is True
    vote_committee.assert_awaited_once()
    vote_details.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_page_allows_manual_continuation_when_thread_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = resolve.util.FetchError(
        "Failed fetching thread metadata",
        url="https://lists.apache.org/api/thread.json",
    )

    context, form_render, archive_lookup, vote_committee, vote_details = await _render_standard_resolve_page(
        monkeypatch,
        details_error=error,
    )

    assert context["fetch_error"] == (
        "ATR could not retrieve the archived vote thread from lists.apache.org, "
        "so automatic vote tabulation is unavailable. Please review the vote manually "
        "and continue below."
    )
    assert context["resolve_form"] == "FORM"
    assert form_render.call_args.kwargs["defaults"] == {"vote_mode": sql.VoteMode.EMAIL, "vote_seq": None}
    assert form_render.call_args.kwargs["pre_submit"] is not None
    assert archive_lookup.await_args.kwargs["strict"] is True
    vote_committee.assert_awaited_once()
    vote_details.assert_awaited_once()


def test_resolve_page_cancel_form_requires_confirmation() -> None:
    with pytest.raises(ValueError):
        shared.resolve.CancelSubmitForm(
            csrf_token="csrf",
            email_body="The vote has been cancelled.",
            confirm_cancel="WRONG",
            vote_result="Cancelled",
            vote_mode=sql.VoteMode.EMAIL,
            vote_seq=None,
        )

    form = shared.resolve.CancelSubmitForm(
        csrf_token="csrf",
        email_body="The vote has been cancelled.",
        confirm_cancel="CONFIRM",
        vote_result="Cancelled",
        vote_mode=sql.VoteMode.EMAIL,
        vote_seq=None,
    )

    assert form.confirm_cancel == "CONFIRM"


@pytest.mark.asyncio
async def test_resolve_page_uses_cancel_form_before_vote_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vote_end = datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)

    context, form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        pass_fail_allowed=False,
        vote_end=vote_end,
    )

    assert context["cancel_only"] is True
    assert form_render.call_args.kwargs["model_cls"] is shared.resolve.CancelSubmitForm
    assert form_render.call_args.kwargs["submit_classes"] == "btn-danger"
    assert form_render.call_args.kwargs["submit_label"] == "Cancel vote"


@pytest.mark.asyncio
async def test_resolve_rejects_early_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writer rejects Failed when the end of the vote has not been reached and no bypass is active."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)
    data.merge = mock.AsyncMock(return_value=release)

    future_task = _latest_vote_task_with_end(24)
    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=future_task))
    monkeypatch.setattr(interaction, "vote_duration_bypass", lambda: False)

    with pytest.raises(storage.AccessError, match="unless it is cancelled"):
        await writer.resolve(
            _project_key(),
            _version_key(),
            "failed",
            "Chair",
            "The vote has failed.",
        )


@pytest.mark.asyncio
async def test_resolve_rejects_early_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Writer rejects Passed when the end of the vote has not been reached and no bypass is active."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)

    future_task = _latest_vote_task_with_end(24)
    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=future_task))
    monkeypatch.setattr(interaction, "vote_duration_bypass", lambda: False)

    with pytest.raises(storage.AccessError, match="voting period"):
        await writer.resolve(
            _project_key(),
            _version_key(),
            "passed",
            "Chair",
            "The vote has passed.",
        )


@pytest.mark.asyncio
async def test_resolve_rejects_stale_email_vote_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release()
    release.current_vote_seq = 2
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)

    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=_latest_vote_task()))

    with pytest.raises(storage.AccessError, match="resolve form is stale"):
        await writer.resolve(
            _project_key(),
            _version_key(),
            "failed",
            "Chair",
            "The vote has failed.",
            expected_vote_seq=1,
            expected_vote_mode=sql.VoteMode.EMAIL,
        )


@pytest.mark.asyncio
async def test_send_resolution_cancelled_builds_cancelled_subject() -> None:
    """send_resolution accepts cancelled and builds a CANCELLED subject."""
    data = _mock_data()
    writer = _writer_with_data(data)
    latest_vote_task = _latest_vote_task()
    release = SimpleNamespace(
        project=SimpleNamespace(
            key="project",
            display_name="Project",
        ),
        version="1.0.0",
    )

    error = await writer.send_resolution(
        release,
        "cancelled",
        "The vote has been cancelled.",
        "Project Chair",
        latest_vote_task,
    )

    assert error is None
    data.add_all.assert_called_once()
    queued_task = data.add_all.call_args.args[0][0]
    assert "CANCELLED" in queued_task.task_args["subject"]
    assert queued_task.task_args["email_to"] == "dev@project.apache.org"
    assert queued_task.task_args["email_cc"] == ["private@project.apache.org"]
    assert queued_task.task_args["email_bcc"] == ["secretary@project.apache.org"]


@pytest.mark.asyncio
async def test_trusted_ballot_rows_use_recomputed_bindingness_and_receipt_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    ballot = sql.BallotPaper(
        release_key="project-1.0.0",
        vote_seq=1,
        vote_round=None,
        voter_asf_uid="voter",
        voter_fullname="Voter",
        choice=sql.VoteChoice.YES,
        comment="",
        is_binding_at_cast=False,
        revision_number_at_cast="00001",
        receipt_message_id="receipt@apache.org",
        created=datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC),
    )
    is_binding_for_release = mock.AsyncMock(return_value=(True, "Project"))
    monkeypatch.setattr(interaction.user, "is_binding_for_release", is_binding_for_release)

    details, summary = await interaction.trusted_ballot_details_from_ballots(
        release,
        [ballot],
        None,
    )
    rows = resolve._trusted_ballot_rows(details, "dev@project.apache.org")

    assert rows == [
        resolve.TrustedBallotRow(
            cast_at="2026-01-02 03:04 UTC",
            choice="+1",
            is_binding=True,
            receipt_message_id="receipt@apache.org",
            receipt_url=(
                "https://lists.apache.org/api/source.lua?"
                "id=%3Creceipt@apache.org%3E&listid=%3Cdev.project.apache.org%3E"
            ),
            status_label="Binding",
            voter_asf_uid="voter",
            voter_fullname="Voter",
        )
    ]
    assert summary.binding_votes_yes == 1
    assert summary.non_binding_votes_yes == 0
    is_binding_for_release.assert_awaited_once_with(release.committee, "voter", None)


def test_trusted_email_context_labels_avoid_authoritative_terms() -> None:
    vote_details = models_tabulate.VoteDetails(
        start_unixtime=None,
        votes={
            "member": models_tabulate.VoteEmail(
                name="Member",
                asf_uid_or_email="member",
                from_email="member@apache.org",
                status=models_tabulate.VoteStatus.BINDING,
                asf_eid="member-eid",
                iso_datetime="2026-01-01T00:00:00Z",
                vote=models_tabulate.Vote.YES,
                quotation="+1",
                updated=False,
            ),
            "committer": models_tabulate.VoteEmail(
                name="Committer",
                asf_uid_or_email="committer",
                from_email="committer@apache.org",
                status=models_tabulate.VoteStatus.COMMITTER,
                asf_eid="committer-eid",
                iso_datetime="2026-01-01T00:00:00Z",
                vote=models_tabulate.Vote.NO,
                quotation="-1",
                updated=False,
            ),
            "contributor": models_tabulate.VoteEmail(
                name="Contributor",
                asf_uid_or_email="contributor",
                from_email="contributor@example.org",
                status=models_tabulate.VoteStatus.CONTRIBUTOR,
                asf_eid="contributor-eid",
                iso_datetime="2026-01-01T00:00:00Z",
                vote=models_tabulate.Vote.ABSTAIN,
                quotation="0",
                updated=False,
            ),
            "unknown": models_tabulate.VoteEmail(
                name="Unknown",
                asf_uid_or_email="unknown@example.org",
                from_email="unknown@example.org",
                status=models_tabulate.VoteStatus.UNKNOWN,
                asf_eid="unknown-eid",
                iso_datetime="2026-01-01T00:00:00Z",
                vote=models_tabulate.Vote.UNKNOWN,
                quotation="?",
                updated=False,
            ),
        },
        summary={},
        passed=True,
        outcome="The vote passed.",
    )

    rows = resolve._email_context_rows(vote_details.votes)
    summary_rows = resolve._email_context_summary_rows(vote_details.votes)

    assert [row.status_label for row in rows] == [
        "Email from PMC member",
        "Email from committer",
        "Email from contributor",
        "Unknown email",
    ]
    assert [row.vote for row in rows] == ["+1", "-1", "0", "?"]
    assert [row.label for row in summary_rows] == [
        "Email from PMC member",
        "Email from committer",
        "Email from contributor",
        "Unknown email",
    ]
    assert "Binding" not in {row.status_label for row in rows}
    assert "Formal" not in {row.status_label for row in rows}


@pytest.mark.asyncio
async def test_trusted_resolve_allows_insufficient_votes_with_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)
    writer.send_resolution = mock.AsyncMock(return_value=None)

    release = _candidate_release()
    release.vote_mode = sql.VoteMode.TRUSTED
    release.effective_vote_mode = sql.VoteMode.TRUSTED
    release.current_vote_seq = 1
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)

    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=_latest_vote_task()))
    monkeypatch.setattr(interaction, "vote_duration_bypass", lambda: True)
    monkeypatch.setattr(interaction, "ballots_for_resolution", mock.AsyncMock(return_value=[]))
    monkeypatch.setattr(
        interaction, "trusted_ballot_summary", mock.AsyncMock(return_value=interaction.TrustedVoteSummary())
    )

    _release, _round, success, _error = await writer.resolve(
        _project_key(),
        _version_key(),
        "passed",
        "Chair",
        "The vote has passed.",
        expected_vote_seq=1,
        expected_vote_mode=sql.VoteMode.TRUSTED,
    )

    assert success == "Vote marked as passed"
    write_as.revision.create_revision_with_quarantine.assert_awaited_once()


@pytest.mark.asyncio
async def test_trusted_resolve_page_passes_authoritative_ballot_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    release.vote_mode = sql.VoteMode.TRUSTED
    release.effective_vote_mode = sql.VoteMode.TRUSTED
    release.release_policy = SimpleNamespace(vote_mode=sql.VoteMode.TRUSTED)
    release.current_vote_seq = 7

    context, _form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
    )

    assert context["trusted_mode"] is True
    assert context["trusted_has_vote_serial"] is True
    assert context["trusted_ballots"] == []
    assert context["trusted_outcome"] == (
        "The ATR ballot record does not satisfy the binding vote threshold for passing."
    )
    assert context["email_context_votes"] == []
    assert context["email_context_summary"] == []


@pytest.mark.asyncio
async def test_trusted_resolve_page_passes_non_empty_authoritative_ballot_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    release.vote_mode = sql.VoteMode.TRUSTED
    release.effective_vote_mode = sql.VoteMode.TRUSTED
    release.release_policy = SimpleNamespace(vote_mode=sql.VoteMode.TRUSTED)
    release.current_vote_seq = 7
    ballot = sql.BallotPaper(
        release_key="project-1.0.0",
        vote_seq=7,
        vote_round=None,
        voter_asf_uid="voter",
        voter_fullname="Voter",
        choice=sql.VoteChoice.YES,
        comment="",
        is_binding_at_cast=False,
        revision_number_at_cast="00001",
        receipt_message_id="receipt@apache.org",
        created=datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC),
    )
    ballots_for_resolution = mock.AsyncMock(return_value=[ballot])

    context, _form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        ballots_for_resolution=ballots_for_resolution,
    )

    assert context["trusted_ballots"] == [
        resolve.TrustedBallotRow(
            cast_at="2026-01-02 03:04 UTC",
            choice="+1",
            is_binding=True,
            receipt_message_id="receipt@apache.org",
            receipt_url=(
                "https://lists.apache.org/api/source.lua?"
                "id=%3Creceipt@apache.org%3E&listid=%3Cdev.project.apache.org%3E"
            ),
            status_label="Binding",
            voter_asf_uid="voter",
            voter_fullname="Voter",
        )
    ]
    ballots_for_resolution.assert_awaited_once_with("project-1.0.0", 7)


@pytest.mark.asyncio
async def test_trusted_resolve_page_passes_receipt_exclusions_to_tabulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    release.vote_mode = sql.VoteMode.TRUSTED
    release.effective_vote_mode = sql.VoteMode.TRUSTED
    release.release_policy = SimpleNamespace(vote_mode=sql.VoteMode.TRUSTED)
    release.current_vote_seq = 7
    ballot_receipt_message_ids = mock.AsyncMock(return_value={"receipt@apache.org"})

    _context, _form_render, _archive_lookup, _vote_committee, vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        ballot_receipt_message_ids=ballot_receipt_message_ids,
    )

    ballot_receipt_message_ids.assert_awaited_once_with("project-1.0.0", 7)
    vote_details.assert_awaited_once()
    assert vote_details.await_args.kwargs["excluded_message_ids"] == {"receipt@apache.org"}


@pytest.mark.asyncio
async def test_trusted_resolve_page_uses_cancel_form_before_vote_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    release.vote_mode = sql.VoteMode.TRUSTED
    release.effective_vote_mode = sql.VoteMode.TRUSTED
    release.release_policy = SimpleNamespace(vote_mode=sql.VoteMode.TRUSTED)
    release.current_vote_seq = 7
    vote_end = datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)

    context, form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        pass_fail_allowed=False,
        vote_end=vote_end,
    )

    assert context["cancel_only"] is True
    assert form_render.call_args.kwargs["model_cls"] is shared.resolve.CancelSubmitForm
    assert form_render.call_args.kwargs["submit_label"] == "Cancel vote"


@pytest.mark.asyncio
async def test_trusted_resolve_passes_round_two_via_carried_ipmc_ballots(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)
    writer.send_resolution = mock.AsyncMock(return_value=None)

    release = _candidate_release(podling_thread_id="thread-abc")
    release.vote_mode = sql.VoteMode.TRUSTED
    release.effective_vote_mode = sql.VoteMode.TRUSTED
    release.current_vote_seq = 2
    release.committee = SimpleNamespace(key="myproject", display_name="MyProject", is_podling=True)
    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    data.release = mock.MagicMock(return_value=query)

    past_task = _latest_vote_task_with_end(-24)
    monkeypatch.setattr(interaction, "release_current_vote_task", mock.AsyncMock(return_value=past_task))
    monkeypatch.setattr(interaction, "vote_duration_bypass", lambda: False)
    effective_ballots = mock.AsyncMock(return_value=[mock.MagicMock(), mock.MagicMock(), mock.MagicMock()])
    monkeypatch.setattr(interaction, "effective_trusted_ballots", effective_ballots)
    monkeypatch.setattr(
        interaction,
        "trusted_ballot_summary",
        mock.AsyncMock(return_value=interaction.TrustedVoteSummary(binding_votes_yes=3)),
    )
    monkeypatch.setattr(
        vote.util,
        "email_mid_from_thread_id",
        mock.AsyncMock(return_value=("general@incubator.apache.org", "msg-id@apache.org")),
    )

    _release, _round, success, _error = await writer.resolve(
        _project_key(),
        _version_key(),
        "passed",
        "Chair",
        "The vote has passed.",
        expected_vote_seq=2,
        expected_vote_mode=sql.VoteMode.TRUSTED,
    )

    assert success == "Vote marked as passed"
    effective_ballots.assert_awaited()
    write_as.revision.create_revision_with_quarantine.assert_awaited_once()


def test_vote_end_get_returns_datetime_for_valid_task() -> None:
    """vote_end_get returns a UTC datetime for a valid VoteInitiate task."""
    task = _latest_vote_task()
    vote_end = interaction.vote_end_get(task)
    assert vote_end is not None
    assert vote_end.tzinfo is datetime.UTC
    assert vote_end == datetime.datetime(2026, 3, 31, 12, 0, 0, tzinfo=datetime.UTC)


def test_vote_end_get_returns_none_for_malformed_date() -> None:
    """vote_end_get returns None when the date string is malformed."""
    task = SimpleNamespace(
        result=results.VoteInitiate(
            kind="vote_initiate",
            message="ok",
            email_to="dev@project.apache.org",
            vote_end="not-a-date",
            subject="[VOTE]",
            mid=None,
            mail_send_warnings=[],
        ),
    )
    assert interaction.vote_end_get(task) is None


def test_vote_end_get_returns_none_for_missing_task() -> None:
    """vote_end_get returns None when given None."""
    assert interaction.vote_end_get(None) is None


def test_vote_end_get_returns_none_for_non_vote_initiate() -> None:
    """vote_end_get returns None for a task with a non-VoteInitiate result."""
    task = SimpleNamespace(result="not a VoteInitiate")
    assert interaction.vote_end_get(task) is None


def test_vote_pass_fail_allowed_returns_false_before_vote_end() -> None:
    """vote_pass_fail_allowed returns False when the end of the vote is in the future."""
    task = _latest_vote_task_with_end(24)
    assert interaction.vote_pass_fail_allowed(task) is False


def test_vote_pass_fail_allowed_returns_false_for_missing_task() -> None:
    """vote_pass_fail_allowed returns False (fail closed) when given None."""
    assert interaction.vote_pass_fail_allowed(None) is False


def test_vote_pass_fail_allowed_returns_true_after_vote_end() -> None:
    """vote_pass_fail_allowed returns True when the end of the vote has elapsed."""
    task = _latest_vote_task_with_end(-24)
    assert interaction.vote_pass_fail_allowed(task) is True


@pytest.mark.asyncio
async def test_vote_tabulate_api_passes_trusted_receipt_exclusions(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _candidate_release()
    release.vote_mode = sql.VoteMode.TRUSTED
    release.effective_vote_mode = sql.VoteMode.TRUSTED
    release.release_policy = SimpleNamespace(vote_mode=sql.VoteMode.TRUSTED)
    release.current_vote_seq = 7
    release.project_key = "project"

    query = mock.MagicMock()
    query.demand = mock.AsyncMock(return_value=release)
    db_data = mock.MagicMock()
    db_data.release = mock.MagicMock(return_value=query)

    @contextlib.asynccontextmanager
    async def _db_session():
        yield db_data

    archive_lookup = mock.AsyncMock(return_value="https://lists.apache.org/thread/0123456789abcdef0123456789abcdef")

    @contextlib.asynccontextmanager
    async def _write_context():
        yield SimpleNamespace(
            as_general_public=lambda: SimpleNamespace(
                cache=SimpleNamespace(get_message_archive_url=archive_lookup),
            ),
        )

    vote_committee = mock.AsyncMock(return_value=release.project.committee)
    vote_details = mock.AsyncMock(
        return_value=models_tabulate.VoteDetails(
            start_unixtime=None,
            votes={},
            summary={},
            passed=False,
            outcome="",
        )
    )
    ballot_receipt_message_ids = mock.AsyncMock(return_value={"receipt@apache.org"})

    monkeypatch.setattr(atr.api.db, "session", _db_session)
    monkeypatch.setattr(atr.api.storage, "write", _write_context)
    monkeypatch.setattr(
        atr.api.interaction,
        "release_current_vote_task",
        mock.AsyncMock(return_value=_latest_vote_task()),
    )
    monkeypatch.setattr(atr.api.interaction, "ballot_receipt_message_ids", ballot_receipt_message_ids)
    monkeypatch.setattr(atr.api.interaction, "ballots_for_resolution", mock.AsyncMock(return_value=[]))
    monkeypatch.setattr(atr.api.tabulate, "vote_committee", vote_committee)
    monkeypatch.setattr(atr.api.tabulate, "vote_details", vote_details)

    response, status_code = await _api_vote_tabulate_handler()(
        "vote/tabulate",
        SimpleNamespace(project="project", version="1.0.0"),
    )

    assert status_code == 200
    assert response["endpoint"] == "/vote/tabulate"
    ballot_receipt_message_ids.assert_awaited_once_with("project-1.0.0", 7)
    vote_details.assert_awaited_once()
    assert vote_details.await_args.kwargs["excluded_message_ids"] == {"receipt@apache.org"}
    assert response["trusted_ballots"] == []
    assert response["trusted_passed"] is False
    assert response["vote_mode"] == sql.VoteMode.TRUSTED.value


def _api_vote_tabulate_handler():
    wrapper = atr.api.vote_tabulate.__wrapped__
    if wrapper.__closure__ is None:
        raise AssertionError("Expected api.vote_tabulate wrapper closure")
    for name, cell in zip(wrapper.__code__.co_freevars, wrapper.__closure__):
        if name == "func":
            handler = cell.cell_contents
            while hasattr(handler, "__wrapped__"):
                handler = handler.__wrapped__
            return handler
    raise AssertionError("Could not find wrapped API handler")


def _candidate_release(podling_thread_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        vote_mode=sql.VoteMode.EMAIL,
        effective_vote_mode=sql.VoteMode.EMAIL,
        release_policy=SimpleNamespace(vote_mode=sql.VoteMode.EMAIL),
        vote_resolved=datetime.datetime.now(datetime.UTC),
        current_vote_seq=None,
        podling_thread_id=podling_thread_id,
        version="1.0.0",
        latest_revision_number="00001",
        committee=SimpleNamespace(
            key="project",
            display_name="Project",
            is_podling=False,
        ),
        project=SimpleNamespace(
            key="project",
            display_name="Project",
            short_display_name="Project",
            release_policy=None,
            committee=SimpleNamespace(
                key="project",
                display_name="Project",
                is_podling=False,
            ),
        ),
        safe_key="project-1.0.0",
        safe_project_key="project",
        safe_version_key="1.0.0",
        safe_latest_revision_number="00001",
        key="project-1.0.0",
    )


def _empty_vote_summary() -> dict[str, int]:
    return {
        "binding_votes": 0,
        "binding_votes_yes": 0,
        "binding_votes_no": 0,
        "binding_votes_abstain": 0,
        "non_binding_votes": 0,
        "non_binding_votes_yes": 0,
        "non_binding_votes_no": 0,
        "non_binding_votes_abstain": 0,
        "unknown_votes": 0,
        "unknown_votes_yes": 0,
        "unknown_votes_no": 0,
        "unknown_votes_abstain": 0,
    }


def _latest_vote_task() -> SimpleNamespace:
    return SimpleNamespace(
        result=results.VoteInitiate(
            kind="vote_initiate",
            message="Vote announcement email sent successfully",
            email_to="dev@project.apache.org",
            vote_end="2026-03-31 12:00:00 UTC",
            subject="[VOTE] Release project 1.0.0",
            mid="vote-thread@apache.org",
            mail_send_warnings=[],
        ),
        task_args={
            "email_to": "dev@project.apache.org",
            "email_cc": ["private@project.apache.org"],
            "email_bcc": ["secretary@project.apache.org"],
        },
    )


def _latest_vote_task_with_end(offset_hours: int) -> SimpleNamespace:
    vote_end = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=offset_hours)
    return SimpleNamespace(
        result=results.VoteInitiate(
            kind="vote_initiate",
            message="Vote announcement email sent successfully",
            email_to="dev@project.apache.org",
            vote_end=vote_end.strftime("%Y-%m-%d %H:%M:%S UTC"),
            subject="[VOTE] Release project 1.0.0",
            mid="vote-thread@apache.org",
            mail_send_warnings=[],
        ),
        task_args={
            "email_to": "dev@project.apache.org",
            "email_cc": ["private@project.apache.org"],
            "email_bcc": ["secretary@project.apache.org"],
        },
    )


def _manual_candidate_release(podling_thread_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        key="project-1.0.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        vote_mode=sql.VoteMode.MANUAL,
        effective_vote_mode=sql.VoteMode.MANUAL,
        release_policy=SimpleNamespace(vote_mode=sql.VoteMode.MANUAL),
        vote_started=datetime.datetime.now(datetime.UTC),
        vote_resolved=datetime.datetime.now(datetime.UTC),
        podling_thread_id=podling_thread_id,
        version="1.0.0",
        safe_version_key="1.0.0",
        project=SimpleNamespace(
            key="project",
            display_name="Project",
            release_policy=None,
            committee=SimpleNamespace(
                key="project",
                is_podling=False,
            ),
        ),
    )


def _mock_cursor_result(rowcount: int = 1) -> mock.MagicMock:
    result = mock.MagicMock(spec=engine.CursorResult)
    result.rowcount = rowcount
    return result


def _mock_data() -> mock.MagicMock:
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.execute = mock.AsyncMock(return_value=_mock_cursor_result())
    data.flush = mock.AsyncMock()
    data.merge = mock.AsyncMock()
    data.refresh = mock.AsyncMock()
    data.rollback = mock.AsyncMock()
    return data


def _mock_write_as() -> mock.MagicMock:
    write_as = mock.MagicMock()
    write_as.append_to_audit_log = mock.MagicMock()
    write_as.revision.create_revision_with_quarantine = mock.AsyncMock()
    return write_as


def _project_key() -> safe.ProjectKey:
    return safe.ProjectKey("project")


def _refresh_as(**attrs: object) -> mock.AsyncMock:
    def _apply(obj: SimpleNamespace) -> None:
        for k, v in attrs.items():
            setattr(obj, k, v)

    return mock.AsyncMock(side_effect=_apply)


async def _render_standard_resolve_page(
    monkeypatch: pytest.MonkeyPatch,
    *,
    archive_error: Exception | None = None,
    details_error: Exception | None = None,
    release: SimpleNamespace | None = None,
    ballot_receipt_message_ids: mock.AsyncMock | None = None,
    ballots_for_resolution: mock.AsyncMock | None = None,
    pass_fail_allowed: bool = True,
    vote_end: datetime.datetime | None = None,
) -> tuple[dict[str, object], mock.AsyncMock, mock.AsyncMock, mock.AsyncMock, mock.AsyncMock]:
    release = release or _candidate_release()
    session = SimpleNamespace(
        uid="chair",
        fullname="Project Chair",
        release=mock.AsyncMock(return_value=release),
    )
    context: dict[str, object] = {}
    archive_lookup = mock.AsyncMock(return_value="https://lists.apache.org/thread/0123456789abcdef0123456789abcdef")
    vote_committee = mock.AsyncMock(return_value=release.project.committee)
    vote_details = mock.AsyncMock()
    form_render = mock.AsyncMock(return_value="FORM")
    ballot_receipt_message_ids = ballot_receipt_message_ids or mock.AsyncMock(return_value=set())
    ballots_for_resolution = ballots_for_resolution or mock.AsyncMock(return_value=[])
    is_binding_for_release = mock.AsyncMock(return_value=(True, "Project"))

    if archive_error is not None:
        archive_lookup.side_effect = archive_error
    if details_error is not None:
        vote_details.side_effect = details_error
    else:
        vote_details.return_value = SimpleNamespace(
            votes={},
            summary=_empty_vote_summary(),
            passed=False,
            outcome="The vote failed.",
        )

    @contextlib.asynccontextmanager
    async def _write_context(_session: object):
        yield SimpleNamespace(
            as_general_public=lambda: SimpleNamespace(
                cache=SimpleNamespace(get_message_archive_url=archive_lookup),
            ),
        )

    async def _template_render(_template_name: str, **kwargs: object) -> str:
        context.update(kwargs)
        return "HTML"

    monkeypatch.setattr(resolve.util, "as_url", lambda _endpoint, **_kwargs: "/resolve/project/1.0.0")
    monkeypatch.setattr(
        resolve.interaction, "release_current_vote_task", mock.AsyncMock(return_value=_latest_vote_task())
    )
    monkeypatch.setattr(resolve.interaction, "vote_duration_bypass", lambda: False)
    monkeypatch.setattr(resolve.interaction, "vote_end_get", lambda _task: vote_end)
    monkeypatch.setattr(resolve.interaction, "vote_pass_fail_allowed", lambda _task: pass_fail_allowed)
    monkeypatch.setattr(resolve.interaction, "ballot_receipt_message_ids", ballot_receipt_message_ids)
    monkeypatch.setattr(resolve.interaction, "ballots_for_resolution", ballots_for_resolution)
    monkeypatch.setattr(resolve.storage, "write", _write_context)
    monkeypatch.setattr(resolve.tabulate, "vote_committee", vote_committee)
    monkeypatch.setattr(resolve.tabulate, "vote_details", vote_details)
    monkeypatch.setattr(resolve.user, "is_binding_for_release", is_binding_for_release)
    monkeypatch.setattr(resolve.atr.form, "render", form_render)
    monkeypatch.setattr(resolve.template, "render", _template_render)

    html = await _resolve_handler()(session, "resolve", _project_key(), _version_key())

    assert html == "HTML"
    return context, form_render, archive_lookup, vote_committee, vote_details


def _resolve_handler():
    wrapper = resolve.selected.__wrapped__
    if wrapper.__closure__ is None:
        raise AssertionError("Expected resolve.selected wrapper closure")
    for name, cell in zip(wrapper.__code__.co_freevars, wrapper.__closure__):
        if name == "func":
            return cell.cell_contents
    raise AssertionError("Could not find wrapped resolve handler")


def _version_key() -> safe.VersionKey:
    return safe.VersionKey("1.0.0")


def _writer_with_data(data: mock.MagicMock) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "chair"
    return writer


def _writer_with_mocks(data: mock.MagicMock, write_as: mock.MagicMock) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__write_as = write_as
    writer._CommitteeMember__asf_uid = "chair"
    writer._CommitteeMember__committee_key = "project"
    return writer
