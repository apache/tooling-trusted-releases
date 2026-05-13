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

import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.shared.resolve as resolve
import atr.tasks.vote as vote


@pytest.mark.asyncio
async def test_auto_resolve_skips_when_vote_failed_and_flag_off(monkeypatch) -> None:
    monkeypatch.setattr(vote, "AUTOMATICALLY_RESOLVE_ON_FAILURE", False)

    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        vote_resolved=None,
        effective_vote_mode=sql.VoteMode.TRUSTED,
        committee=SimpleNamespace(is_podling=False),
        podling_thread_id=None,
        current_vote_seq=1,
        safe_project_key="project",
        safe_version_key="1.0.0",
    )
    query = SimpleNamespace(get=mock.AsyncMock(return_value=release))
    mock_data = SimpleNamespace(release=mock.MagicMock(return_value=query))

    @contextlib.asynccontextmanager
    async def _session():
        yield mock_data

    monkeypatch.setattr(vote.db, "session", _session)

    latest_vote_task = SimpleNamespace(
        result=results.VoteInitiate(
            kind="vote_initiate",
            message="ok",
            email_to="dev@project.apache.org",
            vote_end="2026-01-04 00:00:00 UTC",
            subject="[VOTE] Release",
            mid="mid@apache.org",
            mail_send_warnings=[],
        ),
        task_args={"notify_when_finished": False},
    )
    monkeypatch.setattr(
        vote.interaction,
        "release_current_vote_task",
        mock.AsyncMock(return_value=latest_vote_task),
    )
    monkeypatch.setattr(
        vote.interaction,
        "vote_end_get",
        lambda _task: datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    monkeypatch.setattr(
        vote.interaction,
        "effective_trusted_ballots",
        mock.AsyncMock(return_value=[]),
    )
    summary = SimpleNamespace(
        binding_votes_yes=0,
        binding_votes_no=0,
        binding_votes_abstain=0,
        non_binding_votes_yes=0,
        non_binding_votes_no=0,
        non_binding_votes_abstain=0,
    )
    monkeypatch.setattr(
        vote.interaction,
        "trusted_ballot_summary",
        mock.AsyncMock(return_value=summary),
    )
    monkeypatch.setattr(vote.interaction, "trusted_vote_round", lambda _release: 1)
    monkeypatch.setattr(vote.interaction, "task_mid_get", lambda _task: None)
    monkeypatch.setattr(vote.interaction, "task_recipient_get", lambda _task: None)
    monkeypatch.setattr(vote.tabulate, "binding_vote_passes", lambda yes, no: False)
    monkeypatch.setattr(vote.user, "binding_terminology", lambda _round: ("Binding", "Non-binding"))

    resolve_mock = mock.AsyncMock()

    @contextlib.asynccontextmanager
    async def _write_as_project_committee_member(*_args, **_kwargs):
        yield SimpleNamespace(vote=SimpleNamespace(resolve=resolve_mock))

    monkeypatch.setattr(
        vote.storage,
        "write_as_project_committee_member",
        _write_as_project_committee_member,
    )

    result = await vote.auto_resolve(
        args.VoteAutoResolve(
            release_key="project-1.0.0",
            vote_seq=1,
            resolver_id="chair",
            resolver_fullname="Chair",
        ).model_dump()
    )

    assert isinstance(result, results.VoteAutoResolve)
    assert result.resolved is False
    assert result.vote_result == "failed"
    assert result.skip_reason == "vote_failed_manual_resolution_required"
    resolve_mock.assert_not_awaited()


def test_auto_resolve_supported_handles_podling_rounds() -> None:
    non_podling = sql.Committee(key="project", name="Project", is_podling=False)
    podling = sql.Committee(key="podling", name="Podling", is_podling=True)

    assert vote._auto_resolve_supported(non_podling, None) is True
    assert vote._auto_resolve_supported(non_podling, "thread-abc") is True
    assert vote._auto_resolve_supported(podling, None) is False
    assert vote._auto_resolve_supported(podling, "thread-abc") is True
    assert vote._auto_resolve_supported(None, None) is False


def test_resolve_submit_form_carries_round_two_flags() -> None:
    parsed = resolve.SubmitForm.model_validate(
        {
            "csrf_token": "token",
            "variant": "submit",
            "email_body": "body",
            "vote_result": "Passed",
            "automatic_resolve_when_finished": "on",
            "notify_when_finished": "on",
            "vote_mode": sql.VoteMode.TRUSTED,
            "vote_seq": 1,
        }
    )
    assert parsed.automatic_resolve_when_finished is True
    assert parsed.notify_when_finished is True

    default = resolve.SubmitForm.model_validate(
        {
            "csrf_token": "token",
            "variant": "submit",
            "email_body": "body",
            "vote_result": "Passed",
            "vote_mode": sql.VoteMode.TRUSTED,
            "vote_seq": 1,
        }
    )
    assert default.automatic_resolve_when_finished is False
    assert default.notify_when_finished is False
