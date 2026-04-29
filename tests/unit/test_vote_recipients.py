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
import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.db.interaction as interaction
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.writers.vote as vote
import atr.tasks.vote
import atr.util as util


def test_initiate_args_accepts_second_round_email_to() -> None:
    task_args = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="testuser",
        initiator_fullname="Test User",
        subject="[VOTE] Release",
        body="Please vote",
        second_round_email_to="general@incubator.apache.org",
    )

    assert task_args.second_round_email_to == "general@incubator.apache.org"


def test_initiate_args_accepts_vote_seq() -> None:
    task_args = args.Initiate(
        release_key="project-1.0.0",
        email_to="dev@project.apache.org",
        vote_duration=72,
        initiator_id="testuser",
        initiator_fullname="Test User",
        subject="[VOTE] Release",
        body="Please vote",
        vote_seq=3,
    )

    assert task_args.vote_seq == 3


def test_initiate_args_backward_compatible_without_second_round_field() -> None:
    raw = {
        "release_key": "project-1.0.0",
        "email_to": "dev@project.apache.org",
        "vote_duration": 72,
        "initiator_id": "testuser",
        "initiator_fullname": "Test User",
        "subject": "[VOTE] Release",
        "body": "Please vote",
        "email_cc": [],
        "email_bcc": [],
    }

    task_args = args.Initiate.model_validate(raw)

    assert task_args.second_round_email_to is None
    assert task_args.vote_seq is None


def test_permitted_podling_second_round_recipients_includes_incubator_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_config = SimpleNamespace(ATR_STATUS="PRODUCTION")
    monkeypatch.setattr("atr.config.get", lambda: mock_config)

    recipients = util.permitted_podling_second_round_recipients("testuser")

    assert recipients == ["general@incubator.apache.org"]


def test_permitted_podling_second_round_recipients_includes_test_addresses_in_alpha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_config = SimpleNamespace(ATR_STATUS="ALPHA")
    monkeypatch.setattr("atr.config.get", lambda: mock_config)

    recipients = util.permitted_podling_second_round_recipients("testuser")

    assert "general@incubator.apache.org" in recipients
    assert "user-tests@tooling.apache.org" in recipients
    assert "testuser@apache.org" in recipients


@pytest.mark.asyncio
async def test_send_resolution_reuses_original_vote_recipients() -> None:
    data = mock.MagicMock()
    data.flush = mock.AsyncMock()
    data.commit = mock.AsyncMock()

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
        "passed",
        "Resolution body",
        "Project Chair",
        latest_vote_task,
    )

    assert error is None
    data.add_all.assert_called_once()
    queued_task = data.add_all.call_args.args[0][0]
    assert queued_task.task_args["email_to"] == "dev@project.apache.org"
    assert queued_task.task_args["email_cc"] == ["private@project.apache.org"]
    assert queued_task.task_args["email_bcc"] == ["secretary@project.apache.org"]


@pytest.mark.asyncio
async def test_start_email_vote_sets_vote_seq_on_task() -> None:
    data = mock.MagicMock()
    data.add = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    data.commit = mock.AsyncMock()
    data.rollback = mock.AsyncMock()

    release = SimpleNamespace(
        key="project-1.0.0",
        committee=SimpleNamespace(key="project", is_podling=False),
    )
    release_writer = SimpleNamespace(
        _start_vote_no_commit=mock.AsyncMock(return_value=(release, 7, sql.VoteMode.EMAIL)),
    )
    write_as = SimpleNamespace(release=release_writer, append_to_audit_log=mock.MagicMock())
    writer = _participant_writer_with_mocks(data, write_as)

    task = await writer.start(
        "dev@project.apache.org",
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        safe.RevisionNumber("00001"),
        72,
        "[VOTE] Release",
        "Please vote",
        "Project Chair",
        permitted_recipients=["dev@project.apache.org"],
    )

    assert task.task_args["vote_seq"] == 7
    data.add.assert_called_once_with(task)
    data.begin_immediate.assert_awaited_once()
    data.commit.assert_awaited_once()
    data.rollback.assert_not_awaited()
    write_as.append_to_audit_log.assert_called_once()
    assert write_as.append_to_audit_log.call_args.kwargs["vote_seq"] == 7


def test_task_recipient_get_returns_full_vote_recipient() -> None:
    latest_vote_task = _latest_vote_task()

    recipient = interaction.task_recipient_get(latest_vote_task)

    assert recipient == "dev@project.apache.org"


@pytest.mark.asyncio
async def test_worker_accepts_incubator_address_for_podling_round_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _make_release(is_podling=True, podling_thread_id="abc123")
    _patch_db_and_interaction(monkeypatch, release)
    _patch_storage_write(monkeypatch)

    task_args = _make_initiate_args(email_to="general@incubator.apache.org")
    result = await atr.tasks.vote._initiate_core_logic(task_args)

    assert isinstance(result, results.VoteInitiate)
    assert result.email_to == "general@incubator.apache.org"


@pytest.mark.asyncio
async def test_worker_rejects_stale_vote_seq(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _make_release(is_podling=False, podling_thread_id=None, current_vote_seq=2)
    _patch_db_and_interaction(monkeypatch, release)
    _patch_storage_write(monkeypatch)

    task_args = _make_initiate_args(email_to="dev@project.apache.org")
    task_args.vote_seq = 1

    with pytest.raises(atr.tasks.vote.VoteInitiationError, match="stale"):
        await atr.tasks.vote._initiate_core_logic(task_args)


@pytest.mark.asyncio
async def test_worker_rejects_vote_task_outside_candidate_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    release = _make_release(is_podling=False, podling_thread_id=None, phase=sql.ReleasePhase.RELEASE)
    _patch_db_and_interaction(monkeypatch, release)
    _patch_storage_write(monkeypatch)

    task_args = _make_initiate_args(email_to="dev@project.apache.org")

    with pytest.raises(atr.tasks.vote.VoteInitiationError, match="stale"):
        await atr.tasks.vote._initiate_core_logic(task_args)


@pytest.mark.asyncio
async def test_worker_skips_recipient_check_for_podling_round_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _make_release(is_podling=True, podling_thread_id="abc123")
    _patch_db_and_interaction(monkeypatch, release)
    _patch_storage_write(monkeypatch)

    task_args = _make_initiate_args(email_to="chooser@apache.org", initiator_id="resolver")
    result = await atr.tasks.vote._initiate_core_logic(task_args)

    assert isinstance(result, results.VoteInitiate)
    assert result.email_to == "chooser@apache.org"


@pytest.mark.asyncio
async def test_worker_uses_first_round_validation_for_non_podling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _make_release(is_podling=False, podling_thread_id=None)
    _patch_db_and_interaction(monkeypatch, release)
    _patch_storage_write(monkeypatch)
    monkeypatch.setattr("atr.config.get", lambda: SimpleNamespace(ATR_STATUS="ALPHA"))
    monkeypatch.setattr(
        "atr.util.permitted_voting_recipients",
        lambda uid, committee_key: ["dev@project.apache.org"],
    )

    task_args = _make_initiate_args(email_to="dev@project.apache.org")
    result = await atr.tasks.vote._initiate_core_logic(task_args)

    assert isinstance(result, results.VoteInitiate)
    assert result.email_to == "dev@project.apache.org"


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


def _make_initiate_args(email_to: str, initiator_id: str = "testuser") -> args.Initiate:
    return args.Initiate(
        release_key="testproject-1.0.0",
        email_to=email_to,
        vote_duration=72,
        initiator_id=initiator_id,
        initiator_fullname="Test User",
        subject="[VOTE] Release",
        body="Please vote",
    )


def _make_release(
    *,
    is_podling: bool,
    podling_thread_id: str | None,
    current_vote_seq: int | None = None,
    phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE_CANDIDATE,
) -> SimpleNamespace:
    return SimpleNamespace(
        phase=phase,
        current_vote_seq=current_vote_seq,
        latest_revision_number="00001",
        safe_project_key=safe.ProjectKey("testproject"),
        safe_version_key=safe.VersionKey("1.0.0"),
        safe_latest_revision_number=safe.RevisionNumber("00001"),
        committee=SimpleNamespace(
            is_podling=is_podling,
            key="testproject",
        ),
        podling_thread_id=podling_thread_id,
    )


def _participant_writer_with_mocks(data: mock.MagicMock, write_as: SimpleNamespace) -> vote.CommitteeParticipant:
    writer = object.__new__(vote.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__write_as = write_as
    writer._CommitteeParticipant__asf_uid = "chair"
    writer._CommitteeParticipant__committee_key = "project"
    return writer


def _patch_db_and_interaction(monkeypatch: pytest.MonkeyPatch, release: SimpleNamespace) -> None:
    query = mock.AsyncMock()
    query.demand = mock.AsyncMock(return_value=release)
    mock_data = mock.MagicMock()
    mock_data.release = mock.MagicMock(return_value=query)

    @contextlib.asynccontextmanager
    async def mock_session(**kwargs):
        yield mock_data

    monkeypatch.setattr("atr.db.session", mock_session)
    monkeypatch.setattr("atr.db.interaction.tasks_ongoing", mock.AsyncMock(return_value=0))


def _patch_storage_write(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_send = mock.AsyncMock(return_value=("test-mid@apache.org", []))
    mock_mail_writer = SimpleNamespace(send=mock_send)
    mock_wafc = SimpleNamespace(mail=mock_mail_writer)
    mock_write = SimpleNamespace(as_foundation_committer=lambda: mock_wafc)

    @contextlib.asynccontextmanager
    async def mock_storage_write(asf_uid=None):
        yield mock_write

    monkeypatch.setattr("atr.storage.write", mock_storage_write)


def _writer_with_data(data: mock.MagicMock) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeMember__data = data
    writer._CommitteeMember__asf_uid = "chair"
    return writer
