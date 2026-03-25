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

import unittest.mock as mock
from types import SimpleNamespace

import pytest

import atr.db.interaction as interaction
import atr.models.results as results
import atr.storage.writers.vote as vote


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
        "chair",
        "Project Chair",
        latest_vote_task,
    )

    assert error is None
    data.add_all.assert_called_once()
    queued_task = data.add_all.call_args.args[0][0]
    assert queued_task.task_args["email_to"] == "dev@project.apache.org"
    assert queued_task.task_args["email_cc"] == ["private@project.apache.org"]
    assert queued_task.task_args["email_bcc"] == ["secretary@project.apache.org"]


def test_task_recipient_get_returns_full_vote_recipient() -> None:
    latest_vote_task = _latest_vote_task()

    recipient = interaction.task_recipient_get(latest_vote_task)

    assert recipient == "dev@project.apache.org"


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


def _writer_with_data(data: mock.MagicMock) -> vote.CommitteeMember:
    writer = object.__new__(vote.CommitteeMember)
    writer._CommitteeMember__data = data
    return writer
