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
import quart
import sqlalchemy.engine as engine

import atr.db.interaction as interaction
import atr.get.manual as manual
import atr.get.resolve as resolve
import atr.get.vote
import atr.htm as htm
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
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
        vote_manual=False,
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
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, vote_resolved=None, podling_thread_id=None
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
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, vote_resolved=None, podling_thread_id=None
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
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, vote_resolved=None, podling_thread_id=None
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
    assert release.vote_resolved is None
    assert success == "Vote marked as cancelled"
    write_as.revision.create_revision_with_quarantine.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_resolve_release_clears_podling_thread_id() -> None:
    """Candidate failed also clears podling_thread_id (bug fix)."""
    data = _mock_data()
    write_as = _mock_write_as()
    writer = _writer_with_mocks(data, write_as)

    release = _candidate_release(podling_thread_id="abc123")
    data.merge = mock.AsyncMock(return_value=release)
    data.refresh = _refresh_as(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, vote_resolved=None, podling_thread_id=None
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
    assert release.vote_resolved is None


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
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, vote_resolved=None, podling_thread_id=None
    )

    success = await writer.resolve_manually(
        _project_key(),
        _version_key(),
        "cancelled",
    )

    assert release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
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
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, vote_resolved=None, podling_thread_id=None
    )

    success = await writer.resolve_manually(
        _project_key(),
        _version_key(),
        "failed",
    )

    assert release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
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

    release = SimpleNamespace(
        project=SimpleNamespace(key="project"),
        version="1.0.0",
        short_display_name="Project 1.0.0",
    )

    async with render_app.test_request_context("/manual/resolve/project/1.0.0"):
        html = str(manual._render_resolve_page(release))

    assert "manual vote resolution" in html
    assert "where you posted the result" in html
    assert "cancellation notice" in html


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
        vote_manual=True,
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
    monkeypatch.setattr(interaction, "release_latest_vote_task", mock.AsyncMock(return_value=future_task))
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
    monkeypatch.setattr(interaction, "release_latest_vote_task", mock.AsyncMock(return_value=future_task))
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
    monkeypatch.setattr(interaction, "release_latest_vote_task", mock.AsyncMock(return_value=past_task))
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
    monkeypatch.setattr(interaction, "release_latest_vote_task", mock.AsyncMock(return_value=future_task))
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
    monkeypatch.setattr(interaction, "release_latest_vote_task", mock.AsyncMock(return_value=future_task))
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
        "chair",
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


def _candidate_release(podling_thread_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        vote_resolved=datetime.datetime.now(datetime.UTC),
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
        vote_manual=True,
        vote_started=datetime.datetime.now(datetime.UTC),
        vote_resolved=datetime.datetime.now(datetime.UTC),
        podling_thread_id=podling_thread_id,
        version="1.0.0",
        safe_version_key="1.0.0",
        project=SimpleNamespace(
            key="project",
            display_name="Project",
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


def _version_key() -> safe.VersionKey:
    return safe.VersionKey("1.0.0")


def _writer_with_data(data: mock.MagicMock) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeMember__data = data
    return writer


def _writer_with_mocks(data: mock.MagicMock, write_as: mock.MagicMock) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__write_as = write_as
    writer._CommitteeMember__asf_uid = "chair"
    writer._CommitteeMember__committee_key = "project"
    return writer
