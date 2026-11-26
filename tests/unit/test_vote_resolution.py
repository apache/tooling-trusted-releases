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
import inspect
import pathlib
import unittest.mock as mock
from types import SimpleNamespace
from typing import Literal

import jinja2
import pytest
import quart
import sqlalchemy.engine as engine

import atr.db.interaction as interaction
import atr.form as form
import atr.get.resolve as resolve
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.tabulate as models_tabulate
import atr.post.resolve as post_resolve
import atr.shared as shared
import atr.storage as storage
import atr.storage.writers.vote as vote
import atr.util as util


@pytest.fixture
def render_app() -> quart.Quart:
    app = quart.Quart(__name__)
    app.secret_key = "test-secret"
    app.config["TESTING"] = True
    return app


@pytest.mark.asyncio
async def test_cancel_form_keeps_hidden_result_with_partial_defaults(
    render_app: quart.Quart,
) -> None:
    async with render_app.test_request_context("/resolve/project/1.0.0"):
        rendered = await form.render(
            model_cls=shared.resolve.CancelSubmitForm,
            defaults={
                "vote_seq": None,
            },
            use_error_data=False,
        )

    html = str(rendered)
    assert 'name="vote_result"' in html
    assert 'value="Cancelled"' in html


@pytest.mark.asyncio
async def test_cancel_form_overrides_tabulated_result_before_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    release.project.policy_finish_vote_template = "The vote on {{PROJECT_NAME}} {{VERSION}} {{OUTCOME}}."
    _context, form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        pass_fail_allowed=False,
        vote_end=datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC),
    )

    defaults = form_render.call_args.kwargs["defaults"]
    assert defaults["email_body"] == "The vote on Project 1.0.0 was cancelled."
    assert defaults["vote_result"] == "Cancelled"


def test_counted_ballot_rows_hide_receipt_link_when_expedited() -> None:
    detail = interaction.CountedVoteDetail(
        cast_at=datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC),
        choice=sql.VoteChoice.YES,
        comment="Checked signatures",
        is_binding=True,
        receipt_message_id="receipt@apache.org",
        source="atr",
        status_label="PMC Member (binding)",
        voter_asf_uid="voter",
        voter_fullname="Voter",
        vote_round=None,
    )

    rows = resolve._counted_ballot_rows([detail], "private@project.apache.org", expedited=True)

    assert len(rows) == 1
    assert rows[0].receipt_url is None
    assert rows[0].receipt_message_id == "receipt@apache.org"
    assert rows[0].comment == "Checked signatures"


def test_counted_ballot_rows_include_comment_and_source_links() -> None:
    atr_detail = interaction.CountedVoteDetail(
        cast_at=datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC),
        choice=sql.VoteChoice.YES,
        comment="Verified hashes",
        is_binding=True,
        receipt_message_id="receipt@apache.org",
        source="atr",
        status_label="PMC Member (binding)",
        voter_asf_uid="voter",
        voter_fullname="Voter",
        vote_round=None,
    )
    email_detail = interaction.CountedVoteDetail(
        asf_eid="reply-eid-123",
        cast_at=datetime.datetime(2026, 1, 2, 4, 5, tzinfo=datetime.UTC),
        choice=sql.VoteChoice.NO,
        confirmed_by="chair",
        is_binding=False,
        source="email",
        status_label="Contributor (non-binding)",
        voter_asf_uid=None,
        voter_email="contrib@example.org",
        voter_fullname="Contributor",
        vote_round=None,
    )

    rows = resolve._counted_ballot_rows([atr_detail, email_detail], "dev@project.apache.org")

    assert rows == [
        resolve.CountedBallotRow(
            asf_eid=None,
            cast_at="2026-01-02 03:04 UTC",
            choice="+1",
            comment="Verified hashes",
            confirmed_by=None,
            is_binding=True,
            receipt_message_id="receipt@apache.org",
            receipt_url=(
                "https://lists.apache.org/api/source.lua?"
                "id=%3Creceipt@apache.org%3E&listid=%3Cdev.project.apache.org%3E"
            ),
            source="atr",
            status_label="PMC Member (binding)",
            voter_asf_uid="voter",
            voter_email=None,
            voter_fullname="Voter",
        ),
        resolve.CountedBallotRow(
            asf_eid="reply-eid-123",
            cast_at="2026-01-02 04:05 UTC",
            choice="-1",
            comment="",
            confirmed_by="chair",
            is_binding=False,
            receipt_message_id=None,
            receipt_url="https://lists.apache.org/thread/reply-eid-123",
            source="email",
            status_label="Contributor (non-binding)",
            voter_asf_uid=None,
            voter_email="contrib@example.org",
            voter_fullname="Contributor",
        ),
    ]


@pytest.mark.asyncio
async def test_early_completion_form_has_no_preselected_result(render_app: quart.Quart) -> None:
    async with render_app.test_request_context("/resolve/project/1.0.0"):
        rendered = await form.render(
            model_cls=shared.resolve.SubmitForm,
            defaults={"vote_result": [("Passed", "Passed"), ("Cancelled", "Cancelled")]},
            use_error_data=False,
        )

    html = str(rendered)
    assert 'value="Passed"' in html
    assert 'value="Cancelled"' in html
    assert 'value="Failed"' not in html
    assert "checked" not in html
    assert 'name="early_reason"' in html


def test_email_context_rows_preserve_confirmation_and_labels() -> None:
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
                confirmed=True,
                confirmed_by="chair",
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
                asf_uid_or_email="contributor@example.org",
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
                status=models_tabulate.VoteStatus.CONTRIBUTOR,
                asf_eid="unknown-eid",
                iso_datetime="2026-01-01T00:00:00Z",
                vote=models_tabulate.Vote.UNKNOWN,
                quotation="?",
                updated=False,
            ),
        },
        summary={},
    )

    rows = resolve._email_context_rows(vote_details.votes)
    summary_rows = resolve._email_context_summary_rows(vote_details.votes)

    assert [row.status_label for row in rows] == [
        "Email from PMC member",
        "Email from committer",
        "Email from contributor",
        "Email from contributor",
    ]
    assert [row.vote for row in rows] == ["+1", "-1", "0", "?"]
    assert [(row.confirmed, row.confirmed_by) for row in rows] == [
        (True, "chair"),
        (False, None),
        (False, None),
        (False, None),
    ]
    assert [row.label for row in summary_rows] == [
        "Email from PMC member",
        "Email from committer",
        "Email from contributor",
    ]
    assert "Binding" not in {row.status_label for row in rows}
    assert "Formal" not in {row.status_label for row in rows}


def test_format_utc_returns_utc_minute_string() -> None:
    assert resolve.format_utc(datetime.datetime(2026, 1, 2, 3, 4)) == "2026-01-02 03:04 UTC"
    offset = datetime.timezone(datetime.timedelta(hours=1))
    assert resolve.format_utc(datetime.datetime(2026, 1, 2, 4, 4, tzinfo=offset)) == "2026-01-02 03:04 UTC"


def test_interpret_form_accepts_choice_reset_and_vote_seq() -> None:
    confirmed = shared.resolve.InterpretForm(
        csrf_token="csrf",
        asf_eid="eid-1",
        choice="+1",
        vote_seq="7",
    )
    reset = shared.resolve.InterpretForm(
        csrf_token="csrf",
        asf_eid="eid-1",
        choice="",
        vote_seq="",
    )

    assert confirmed.choice == "+1"
    assert confirmed.vote_seq == 7
    assert reset.choice == ""
    assert reset.vote_seq is None


@pytest.mark.parametrize(
    ("choice_input", "expected_choice", "expected_flash"),
    [
        ("+1", sql.VoteChoice.YES, "Email vote confirmed as +1"),
        ("", None, "Email vote confirmation reset"),
    ],
)
@pytest.mark.asyncio
async def test_post_interpret_calls_confirm_email_vote(
    monkeypatch: pytest.MonkeyPatch,
    choice_input: Literal["+1", ""],
    expected_choice: sql.VoteChoice | None,
    expected_flash: str,
) -> None:
    wacm = SimpleNamespace(vote=SimpleNamespace(confirm_email_vote=mock.AsyncMock()))
    write = SimpleNamespace(as_vote_committee_member=mock.AsyncMock(return_value=wacm))
    monkeypatch.setattr(post_resolve.storage, "write", _mock_write_context(write))
    session = SimpleNamespace(redirect=mock.AsyncMock(return_value="REDIRECT"))
    interpret_form = shared.resolve.InterpretForm(
        csrf_token="csrf",
        asf_eid="eid-123",
        choice=choice_input,
        vote_seq=4,
    )

    response = await _unwrap_handler(post_resolve.interpret)(
        session, "resolve/interpret", _project_key(), _version_key(), interpret_form
    )

    assert response == "REDIRECT"
    write.as_vote_committee_member.assert_awaited_once_with(_project_key(), _version_key())
    wacm.vote.confirm_email_vote.assert_awaited_once_with(
        _project_key(),
        _version_key(),
        "eid-123",
        expected_choice,
        expected_vote_seq=4,
    )
    assert session.redirect.await_args.kwargs["success"] == expected_flash


@pytest.mark.parametrize(
    "error",
    [
        storage.AccessError("Not a member of project", status=403),
        interaction.InteractionError("Cannot confirm receipt email"),
        util.FetchError("lists.apache.org unavailable", "https://lists.apache.org/api/stats.lua"),
        ValueError("Invalid thread ID"),
    ],
)
@pytest.mark.asyncio
async def test_post_interpret_redirects_on_confirmation_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    wacm = SimpleNamespace(vote=SimpleNamespace(confirm_email_vote=mock.AsyncMock(side_effect=error)))
    write = SimpleNamespace(as_vote_committee_member=mock.AsyncMock(return_value=wacm))
    monkeypatch.setattr(post_resolve.storage, "write", _mock_write_context(write))
    session = SimpleNamespace(redirect=mock.AsyncMock(return_value="REDIRECT"))
    interpret_form = shared.resolve.InterpretForm(
        csrf_token="csrf",
        asf_eid="eid-err",
        choice="+1",
        vote_seq=4,
    )

    response = await _unwrap_handler(post_resolve.interpret)(
        session, "resolve/interpret", _project_key(), _version_key(), interpret_form
    )

    assert response == "REDIRECT"
    assert session.redirect.await_args.kwargs["error"] == str(error)


@pytest.mark.asyncio
async def test_post_resolve_uses_vote_resolver_or_security_team(monkeypatch: pytest.MonkeyPatch) -> None:
    wavr = SimpleNamespace(resolve=mock.AsyncMock(return_value=(None, 2, "Vote marked as passed", None)))
    wast = SimpleNamespace(vote_resolve=mock.AsyncMock(return_value=(None, None, "Vote marked as passed", None)))
    write = SimpleNamespace(
        as_vote_resolver=mock.AsyncMock(return_value=wavr),
        as_project_security_team=mock.AsyncMock(return_value=wast),
    )
    monkeypatch.setattr(post_resolve.storage, "write", _mock_write_context(write))
    session = SimpleNamespace(fullname="Project Chair", redirect=mock.AsyncMock(return_value="REDIRECT"))

    normal_form = shared.resolve.SubmitForm(
        csrf_token="csrf",
        email_cc=[],
        email_bcc=[],
        vote_seq=5,
        email_body="Result {{OUTCOME}}",
        vote_result="Passed",
        notify_when_finished=True,
    )
    await _unwrap_handler(post_resolve.selected)(session, "resolve", _project_key(), _version_key(), normal_form)

    write.as_vote_resolver.assert_awaited_once_with(_project_key(), _version_key())
    wavr.resolve.assert_awaited_once_with(
        _project_key(),
        _version_key(),
        "passed",
        "Project Chair",
        "Result passed",
        expected_vote_seq=5,
        notify_when_finished=True,
        additional_cc=[],
        additional_bcc=[],
        early_reason="",
    )

    override_form = shared.resolve.SubmitForm(
        csrf_token="csrf",
        email_cc=[],
        email_bcc=[],
        vote_seq=5,
        email_body="Result {{OUTCOME}}",
        vote_result="Passed",
        security_override=True,
    )
    await _unwrap_handler(post_resolve.selected)(session, "resolve", _project_key(), _version_key(), override_form)

    write.as_project_security_team.assert_awaited_once_with(_project_key())
    wast.vote_resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_second_round_uses_vote_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    task = SimpleNamespace(task_args={"email_to": "general@incubator.apache.org"})
    wavr = SimpleNamespace(start_second_round=mock.AsyncMock(return_value=task))
    write = SimpleNamespace(as_vote_resolver=mock.AsyncMock(return_value=wavr))
    monkeypatch.setattr(post_resolve.storage, "write", _mock_write_context(write))
    session = SimpleNamespace(fullname="Project Chair", redirect=mock.AsyncMock(return_value="REDIRECT"))
    second_round_form = shared.resolve.SecondRoundForm(csrf_token="csrf", vote_seq="3")

    response = await _unwrap_handler(post_resolve.second_round)(
        session, "resolve/second-round", _project_key(), _version_key(), second_round_form
    )

    assert response == "REDIRECT"
    write.as_vote_resolver.assert_awaited_once_with(_project_key(), _version_key())
    wavr.start_second_round.assert_awaited_once_with(_project_key(), _version_key(), "Project Chair", 3)
    assert "general@incubator.apache.org" in session.redirect.await_args.kwargs["success"]


def test_presentations_bypass_set_audits_only_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    conf = interaction.config.get()
    assert conf.PRESENTATIONS_VOTE_RESOLUTION_BYPASS is False
    monkeypatch.setattr(conf, "PRESENTATIONS_VOTE_RESOLUTION_BYPASS", False)
    monkeypatch.setattr(storage.user, "is_admin", lambda _asf_uid: True)
    audit = mock.Mock()
    monkeypatch.setattr(storage, "audit", audit)
    writer = storage.Write(SimpleNamespace(asf_uid="admin"), _mock_data()).as_foundation_admin().vote

    writer.presentations_bypass_set(True)
    assert conf.PRESENTATIONS_VOTE_RESOLUTION_BYPASS is True
    writer.presentations_bypass_set(True)
    writer.presentations_bypass_set(False)
    assert conf.PRESENTATIONS_VOTE_RESOLUTION_BYPASS is False
    assert audit.call_args_list == [
        mock.call(asf_uid="admin", previous=False, enabled=True),
        mock.call(asf_uid="admin", previous=True, enabled=False),
    ]


@pytest.mark.asyncio
async def test_resolve_page_allows_early_result_when_bypass_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vote_end = datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC)

    context, form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        pass_fail_allowed=False,
        resolution_bypass=True,
        vote_end=vote_end,
    )

    assert context["cancel_only"] is False
    assert form_render.call_args.kwargs["model_cls"] is shared.resolve.SubmitForm
    assert form_render.call_args.kwargs["submit_classes"] == "btn-primary"
    assert form_render.call_args.kwargs["submit_label"] == "Resolve vote"
    assert "security_override" in form_render.call_args.kwargs["skip"]


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
    assert form_render.call_args.kwargs["defaults"] == {
        "email_body": "",
        "vote_result": [("Failed", "Failed"), ("Cancelled", "Cancelled")],
        "result_email_to": "dev@project.apache.org",
        "result_subject": "[RESULT] [VOTE] Release Project 1.0.0 {{OUTCOME}}",
        "vote_seq": None,
    }
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
    assert form_render.call_args.kwargs["defaults"] == {
        "email_body": "",
        "vote_result": [("Failed", "Failed"), ("Cancelled", "Cancelled")],
        "result_email_to": "dev@project.apache.org",
        "result_subject": "[RESULT] [VOTE] Release Project 1.0.0 {{OUTCOME}}",
        "vote_seq": None,
    }
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
    assert form_render.call_args.kwargs["defaults"] == {
        "email_body": "",
        "vote_result": [("Failed", "Failed"), ("Cancelled", "Cancelled")],
        "result_email_to": "dev@project.apache.org",
        "result_subject": "[RESULT] [VOTE] Release Project 1.0.0 {{OUTCOME}}",
        "vote_seq": None,
    }
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
            vote_seq=None,
        )

    cancel_form = shared.resolve.CancelSubmitForm(
        csrf_token="csrf",
        email_body="The vote has been cancelled.",
        confirm_cancel="CONFIRM",
        vote_result="Cancelled",
        vote_seq=None,
    )

    assert cancel_form.confirm_cancel == "CONFIRM"


@pytest.mark.asyncio
async def test_resolve_page_defers_selectable_outcome_to_post_and_renders_tally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    release.current_vote_seq = 1
    release.project.policy_finish_vote_template = (
        "The vote on {{PROJECT_NAME}} {{VERSION}} {{OUTCOME}}.\n\n{{ATR_TALLY}}"
    )
    ballot = interaction.CountedVoteDetail(
        cast_at=datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.UTC),
        choice=sql.VoteChoice.YES,
        comment="LGTM",
        is_binding=True,
        receipt_message_id="receipt@apache.org",
        source="atr",
        status_label="PMC Member (binding)",
        voter_asf_uid="voter",
        voter_fullname="Voter",
        vote_round=None,
    )
    summary = interaction.CountedVoteSummary(binding_votes_yes=3)

    context, form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        counted_details=[ballot],
        counted_summary=summary,
    )

    defaults = form_render.call_args.kwargs["defaults"]
    assert "The vote on Project 1.0.0 {{OUTCOME}}." in defaults["email_body"]
    assert "+1 voter (binding)" in defaults["email_body"]
    assert "Binding votes: +1: 3, 0: 0, -1: 0." in defaults["email_body"]
    assert context["counted_ballots"][0].comment == "LGTM"


@pytest.mark.parametrize(
    ("vote_end", "model_cls", "vote_result"),
    [
        (
            datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC),
            shared.resolve.SubmitForm,
            [("Passed", "Passed"), ("Cancelled", "Cancelled")],
        ),
        (None, shared.resolve.CancelSubmitForm, "Cancelled"),
    ],
)
@pytest.mark.asyncio
async def test_resolve_page_offers_early_completion_only_with_known_end(
    monkeypatch: pytest.MonkeyPatch,
    vote_end: datetime.datetime | None,
    model_cls: type[form.Form],
    vote_result: object,
) -> None:
    monkeypatch.setattr(resolve.tabulate, "binding_vote_passes", lambda _yes, _no: True)

    release = _candidate_release()
    release.current_vote_seq = 1

    context, form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        counted_summary=interaction.CountedVoteSummary(binding_votes_yes=3),
        pass_fail_allowed=False,
        vote_end=vote_end,
    )

    assert context["early_completion"] is (vote_end is not None)
    assert form_render.call_args.kwargs["model_cls"] is model_cls
    assert form_render.call_args.kwargs["defaults"]["vote_result"] == vote_result
    if model_cls is shared.resolve.SubmitForm:
        assert "early_reason" not in form_render.call_args.kwargs["skip"]


@pytest.mark.asyncio
async def test_resolve_page_offers_security_override_to_security_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
    release.current_vote_seq = 7

    _context, form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        pass_fail_allowed=False,
        security_team=True,
        vote_end=datetime.datetime(2026, 4, 1, 12, 0, 0, tzinfo=datetime.UTC),
    )

    assert form_render.call_args.kwargs["model_cls"] is shared.resolve.SubmitForm
    assert "security_override" not in form_render.call_args.kwargs["skip"]
    assert ("Passed", "Passed") in form_render.call_args.kwargs["defaults"]["vote_result"]


@pytest.mark.asyncio
async def test_resolve_page_passes_receipt_exclusions_to_tabulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _candidate_release()
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


@pytest.mark.parametrize(
    ("role", "podling_round", "expedited", "expected_confirm", "expected_resolve", "expected_second_round"),
    [
        ("pmc", None, False, True, True, False),
        ("rm", None, False, False, True, False),
        ("observer", None, False, False, False, False),
        ("pmc", 1, False, True, True, True),
        ("rm", 1, False, False, True, True),
        ("ipmc", 1, False, False, False, False),
        ("pmc", 2, False, False, True, False),
        ("ipmc", 2, False, True, True, False),
        ("pmc", None, True, False, True, False),
        ("rm", None, True, False, False, False),
        ("security", None, True, False, True, False),
    ],
)
@pytest.mark.asyncio
async def test_resolve_page_permissions_matrix(
    monkeypatch: pytest.MonkeyPatch,
    role: Literal["pmc", "rm", "ipmc", "observer", "security"],
    podling_round: int | None,
    expedited: bool,
    expected_confirm: bool,
    expected_resolve: bool,
    expected_second_round: bool,
) -> None:
    release = _candidate_release(podling_thread_id="thread-r1" if (podling_round == 2) else None)
    release.current_vote_seq = 4
    release.expedited = expedited
    release.is_embargoed = expedited
    release.committee.is_podling = podling_round is not None
    release.committee.committee_members = ["pmc_user"]
    release.committee.committers = ["rm_user"]
    release.committee.release_managers = ["rm_user"]

    uid_by_role = {
        "pmc": "pmc_user",
        "rm": "rm_user",
        "ipmc": "ipmc_user",
        "observer": "other_user",
        "security": "sec_user",
    }
    committees_by_role = {
        "pmc": ["project"],
        "rm": [],
        "ipmc": ["incubator"],
        "observer": [],
        "security": ["security"],
    }
    is_binding = (role == "pmc" and podling_round != 2) or (role == "ipmc" and podling_round == 2)

    context, _form_render, _archive_lookup, _vote_committee, _vote_details = await _render_standard_resolve_page(
        monkeypatch,
        release=release,
        uid=uid_by_role[role],
        member_committees=committees_by_role[role],
        is_binding=is_binding,
    )

    assert context["can_confirm"] is expected_confirm
    assert context["can_resolve"] is expected_resolve
    assert context["second_round_startable"] is expected_second_round
    assert (context["resolve_form"] is not None) is expected_resolve


def test_resolve_page_template_renders_official_tally_and_confirmation_table() -> None:
    env = jinja2.Environment(
        loader=jinja2.ChoiceLoader(
            [
                jinja2.DictLoader(
                    {"layouts/base.html": "{% block content %}{% endblock %}{% block javascripts %}{% endblock %}"}
                ),
                jinja2.FileSystemLoader(str(pathlib.Path("atr/templates"))),
            ]
        ),
        autoescape=True,
    )
    tpl = env.get_template("resolve-tabulated.html")
    release = _candidate_release()
    release.current_vote_seq = 7

    counted_ballots = [
        resolve.CountedBallotRow(
            cast_at="2026-01-02 03:04 UTC",
            choice="+1",
            comment="Checked signatures",
            is_binding=True,
            receipt_message_id="receipt@apache.org",
            receipt_url="https://lists.apache.org/api/source.lua?id=%3Creceipt@apache.org%3E",
            source="atr",
            status_label="PMC Member (binding)",
            voter_asf_uid="alice",
            voter_fullname="Alice",
        ),
        resolve.CountedBallotRow(
            asf_eid="reply-eid-1",
            cast_at="2026-01-02 04:05 UTC",
            choice="+1",
            comment="",
            confirmed_by="chair",
            is_binding=False,
            receipt_url="https://lists.apache.org/thread/reply-eid-1",
            source="email",
            status_label="Contributor (non-binding)",
            voter_asf_uid=None,
            voter_email="ext@example.org",
            voter_fullname="External Voter",
        ),
    ]
    email_context_votes = [
        resolve.EmailContextRow(
            asf_eid="reply-eid-1",
            asf_uid_or_email="ext@example.org",
            confirmed=True,
            confirmed_by="chair",
            link_url="https://lists.apache.org/thread/reply-eid-1",
            name="External Voter",
            quotation="+1 from me",
            status_label="Email from contributor",
            vote="+1",
        ),
        resolve.EmailContextRow(
            asf_eid="reply-eid-2",
            asf_uid_or_email="bob",
            confirmed=False,
            confirmed_by=None,
            link_url="https://lists.apache.org/thread/reply-eid-2",
            name="Bob",
            quotation="+1",
            status_label="Email from PMC member",
            vote="+1",
        ),
    ]
    base_context = {
        "as_url": lambda _route, **kwargs: f"/action/{kwargs.get('project_key', '')}/{kwargs.get('version_key', '')}",
        "csrf_input_fn": lambda: '<input type="hidden" name="csrf_token" value="tok" />',
        "static_url": lambda path: f"/static/{path}",
        "get": SimpleNamespace(vote=SimpleNamespace(selected="vote")),
        "post": SimpleNamespace(
            resolve=SimpleNamespace(interpret="interpret", second_round="second_round"),
        ),
        "release": release,
        "commit_hash_html": "",
        "vote_task_error": None,
        "mail_warnings": [],
        "template_warning": None,
        "has_vote_serial": True,
        "vote_seq": 7,
        "counted_ballots": counted_ballots,
        "counted_summary": interaction.CountedVoteSummary(binding_votes_yes=1, non_binding_votes_yes=1),
        "counted_outcome": "Outcome text",
        "binding_label": "Binding",
        "binding_label_inline": "binding",
        "non_binding_label": "Non-binding",
        "archive_url": "https://lists.apache.org/thread/abc",
        "fetch_error": None,
        "email_context_votes": email_context_votes,
        "email_context_summary": [resolve.VoteCountRow(abstain=0, label="Email from PMC member", no=0, total=1, yes=1)],
        "can_confirm": True,
        "can_resolve": True,
        "second_round_startable": False,
        "resolve_form": "RESOLVE_FORM_HTML",
        "cancel_only": False,
        "early_completion": False,
        "pass_fail_allowed": True,
        "bypass_active": False,
        "vote_end": None,
    }

    html = tpl.render(**base_context)
    assert "Checked signatures" in html
    assert "Email (confirmed by chair)" in html
    assert "Carried" not in html
    assert "Confirmed (+1) by chair" in html
    assert "Unconfirmed (not counted)" in html
    assert "Unmapped email: remains non-binding" in html
    assert '<input type="hidden" name="vote_seq" value="7" />' in html
    assert "Reset</button>" in html
    assert "RESOLVE_FORM_HTML" in html

    readonly_html = tpl.render(**{**base_context, "can_confirm": False, "can_resolve": False, "resolve_form": None})
    assert "<th>Confirm</th>" not in readonly_html
    assert "RESOLVE_FORM_HTML" not in readonly_html
    assert "can resolve this vote." in readonly_html


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
async def test_send_resolution_appends_early_reason() -> None:
    data = _mock_data()
    writer = _writer_with_data(data)

    await writer._ReleaseManager__send_resolution(
        _candidate_release(),
        "passed",
        "The vote passed.\n",
        _latest_vote_task(),
        early_reason="Security fix",
    )

    queued_task = data.add_all.call_args.args[0][0]
    assert queued_task.task_args["body"] == (
        "The vote passed.\n\nThis vote was completed before the end of its voting period,"
        " for the following reason:\n\nSecurity fix\n"
    )


@pytest.mark.asyncio
async def test_send_resolution_cancelled_builds_cancelled_subject() -> None:
    """__send_resolution accepts cancelled and builds a CANCELLED subject."""
    data = _mock_data()
    writer = _writer_with_data(data)
    latest_vote_task = _latest_vote_task()
    release = SimpleNamespace(
        project=SimpleNamespace(
            key="project",
            status=sql.ProjectStatus.ACTIVE,
            is_active=True,
            display_name="Project",
            policy_recipients=lambda action: ("", [], []),
        ),
        version="1.0.0",
    )

    error = await writer._ReleaseManager__send_resolution(
        release,
        "cancelled",
        "The vote has been cancelled.",
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


@pytest.mark.parametrize(
    ("app_host", "production_mode", "is_admin", "committee_key", "project_key", "enabled", "expected"),
    [
        ("tooling-vm-ec2-de.apache.org", True, True, "tooling", "tooling", False, True),
        ("tooling-vm-ec2-de.apache.org:443", True, True, "tooling", "tooling-demo", False, True),
        ("tooling-vm-ec2-de.apache.org", True, False, "tooling", "tooling", False, False),
        ("tooling-vm-ec2-de.apache.org", True, True, "other", "tooling", False, False),
        ("tooling-vm-ec2-de.apache.org", False, False, "tooling", "tooling", False, False),
        ("tooling-vm-ec2-de.apache.org", True, False, "tooling", "tooling-presentations", True, False),
        ("release-test.apache.org", True, True, "tooling", "tooling", False, False),
        ("release-test.apache.org", True, False, "tooling", "tooling-presentations", False, False),
        ("release-test.apache.org", True, False, "tooling", "tooling-presentations", True, True),
        ("release-test.apache.org", True, True, "tooling", "tooling-presentations-other", True, False),
        ("release-test.apache.org", True, True, "tooling", "tooling", True, False),
        ("127.0.0.1", False, False, "other", "project", False, True),
        ("127.0.0.1", True, True, "tooling", "tooling", False, False),
    ],
)
def test_vote_resolution_bypass(
    monkeypatch: pytest.MonkeyPatch,
    app_host: str,
    production_mode: bool,
    is_admin: bool,
    committee_key: str,
    project_key: str,
    enabled: bool,
    expected: bool,
) -> None:
    release = _candidate_release()
    release.project_key = project_key
    release.project.committee_key = committee_key
    monkeypatch.setattr(
        interaction.config,
        "get",
        lambda: SimpleNamespace(APP_HOST=app_host, PRESENTATIONS_VOTE_RESOLUTION_BYPASS=enabled),
    )
    monkeypatch.setattr(interaction.config, "is_production_mode", lambda: production_mode)
    monkeypatch.setattr(interaction.user, "is_admin", lambda _asf_uid: is_admin)

    assert interaction.vote_resolution_bypass(release, "admin") is expected


def _candidate_release(podling_thread_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        is_embargoed=False,
        expedited=False,
        vote_resolved=None,
        current_vote_seq=None,
        podling_thread_id=podling_thread_id,
        commit_hash=None,
        version="1.0.0",
        latest_revision_number="00001",
        committee=SimpleNamespace(
            key="project",
            display_name="Project",
            is_podling=False,
            committee_members=["chair"],
            committers=["chair"],
            release_managers=[],
        ),
        project=SimpleNamespace(
            key="project",
            committee_key="project",
            status=sql.ProjectStatus.ACTIVE,
            is_active=True,
            display_name="Project",
            short_display_name="Project",
            release_policy=None,
            policy_finish_vote_template="{{ATR_TALLY}}",
            policy_finish_vote_template_url="",
            policy_recipients=lambda action: ("", [], []),
            committee=SimpleNamespace(
                key="project",
                display_name="Project",
                is_podling=False,
                committee_members=["chair"],
                committers=["chair"],
                release_managers=[],
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
        status=sql.TaskStatus.COMPLETED,
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
        status=sql.TaskStatus.COMPLETED,
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


def _mock_write_context(write_obj: object):
    @contextlib.asynccontextmanager
    async def _ctx(_session: object):
        yield write_obj

    return _ctx


def _project_key() -> safe.ProjectKey:
    return safe.ProjectKey("project")


async def _render_standard_resolve_page(
    monkeypatch: pytest.MonkeyPatch,
    *,
    archive_error: Exception | None = None,
    details_error: Exception | None = None,
    release: SimpleNamespace | None = None,
    ballot_receipt_message_ids: mock.AsyncMock | None = None,
    counted_details: list[interaction.CountedVoteDetail] | None = None,
    counted_summary: interaction.CountedVoteSummary | None = None,
    pass_fail_allowed: bool = True,
    resolution_bypass: bool = False,
    security_team: bool = False,
    vote_end: datetime.datetime | None = None,
    uid: str = "chair",
    member_committees: list[str] | None = None,
    is_binding: bool = True,
) -> tuple[dict[str, object], mock.AsyncMock, mock.AsyncMock, mock.AsyncMock, mock.AsyncMock]:
    release = release or _candidate_release()
    committees = (
        member_committees if (member_committees is not None) else (["security"] if security_team else ["project"])
    )
    session = SimpleNamespace(
        uid=uid,
        fullname="Project Chair",
        member_committees=committees,
        release=mock.AsyncMock(return_value=release),
    )
    context: dict[str, object] = {}
    archive_lookup = mock.AsyncMock(return_value="https://lists.apache.org/thread/0123456789abcdef0123456789abcdef")
    vote_committee = mock.AsyncMock(return_value=release.project.committee)
    vote_details = mock.AsyncMock()
    form_render = mock.AsyncMock(return_value="FORM")
    resolution_bypass_check = mock.Mock(return_value=resolution_bypass)
    ballot_receipt_message_ids = ballot_receipt_message_ids or mock.AsyncMock(return_value=set())
    counted_vote_details_and_summary = mock.AsyncMock(
        return_value=(
            counted_details if (counted_details is not None) else [],
            counted_summary if (counted_summary is not None) else interaction.CountedVoteSummary(),
        )
    )
    is_binding_for_release = mock.AsyncMock(return_value=(is_binding, "Project"))

    if archive_error is not None:
        archive_lookup.side_effect = archive_error
    if details_error is not None:
        vote_details.side_effect = details_error
    else:
        vote_details.return_value = models_tabulate.VoteDetails(
            start_unixtime=None,
            votes={},
            summary={},
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
    monkeypatch.setattr(resolve.interaction, "release_early_second_round_task", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(resolve.interaction, "vote_resolution_bypass", resolution_bypass_check)
    monkeypatch.setattr(resolve.interaction, "vote_end_get", lambda _task: vote_end)
    monkeypatch.setattr(resolve.interaction, "vote_pass_fail_allowed", lambda _task: pass_fail_allowed)
    monkeypatch.setattr(resolve.interaction, "ballot_receipt_message_ids", ballot_receipt_message_ids)
    monkeypatch.setattr(resolve.interaction, "counted_vote_details_and_summary", counted_vote_details_and_summary)
    monkeypatch.setattr(resolve.storage, "write", _write_context)
    monkeypatch.setattr(resolve.tabulate, "vote_committee", vote_committee)
    monkeypatch.setattr(resolve.tabulate, "vote_details", vote_details)
    monkeypatch.setattr(resolve.user, "is_binding_for_release", is_binding_for_release)
    monkeypatch.setattr(resolve.form, "render", form_render)
    monkeypatch.setattr(resolve.template, "render", _template_render)

    html = await _unwrap_handler(resolve.selected)(session, "resolve", _project_key(), _version_key())

    assert html == "HTML"
    resolution_bypass_check.assert_called_once_with(release, session.uid)
    return context, form_render, archive_lookup, vote_committee, vote_details


def _unwrap_handler(endpoint: object):
    wrapper = inspect.unwrap(endpoint)
    if wrapper.__closure__ is None:
        raise AssertionError("Expected route wrapper closure")
    for name, cell in zip(wrapper.__code__.co_freevars, wrapper.__closure__):
        if name == "func":
            return cell.cell_contents
    raise AssertionError("Could not find wrapped route handler")


def _version_key() -> safe.VersionKey:
    return safe.VersionKey("1.0.0")


def _writer_with_data(data: mock.MagicMock) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__asf_uid = "chair"
    return writer
