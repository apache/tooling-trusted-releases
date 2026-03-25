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

import atr.get.manual as manual
import atr.get.resolve as resolve
import atr.get.vote
import atr.htm as htm
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
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


def _manual_candidate_release(podling_thread_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
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


def _mock_data() -> mock.MagicMock:
    data = mock.MagicMock()
    data.commit = mock.AsyncMock()
    data.flush = mock.AsyncMock()
    data.merge = mock.AsyncMock()
    data.refresh = mock.AsyncMock()
    return data


def _mock_write_as() -> mock.MagicMock:
    write_as = mock.MagicMock()
    write_as.append_to_audit_log = mock.MagicMock()
    write_as.revision.create_revision_with_quarantine = mock.AsyncMock()
    return write_as


def _project_key() -> safe.ProjectKey:
    return safe.ProjectKey("project")


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
