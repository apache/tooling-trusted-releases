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

import pytest

import atr.ldap as ldap
import atr.models.sql as sql
import atr.tasks.task as task
import atr.worker as worker


def test_task_args_for_log_leaves_other_tasks_unchanged() -> None:
    task_args = {"body": "Not a mail body"}

    assert worker._task_args_for_log(sql.TaskType.MAINTENANCE, task_args) is task_args


def test_task_args_for_log_redacts_message_body_and_recipients() -> None:
    task_args = {
        "email_sender": "sender@apache.org",
        "email_to": "to@apache.org",
        "subject": "Subject",
        "body": "Secret body",
        "in_reply_to": "previous@apache.org",
        "email_cc": ["cc@apache.org"],
        "email_bcc": ["bcc@apache.org"],
        "message_id": "message@apache.org",
        "footer_category": "vote",
    }

    logged = worker._task_args_for_log(sql.TaskType.MESSAGE_SEND, task_args)

    assert logged == {
        "email_sender": "sender@apache.org",
        "email_to": "<hidden>",
        "subject": "Subject",
        "body": "<hidden>",
        "in_reply_to": "previous@apache.org",
        "email_cc": "<hidden>",
        "email_bcc": "<hidden>",
        "message_id": "message@apache.org",
        "footer_category": "vote",
    }
    assert task_args["body"] == "Secret body"


async def test_task_process_defers_when_ldap_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("atr.config.is_production_mode", lambda: True)
    monkeypatch.setattr("atr.ldap.account_lookup", mock.AsyncMock(side_effect=ldap.UnavailableError("down")))
    defer = mock.AsyncMock()
    monkeypatch.setattr("atr.worker._task_defer", defer)
    result_process = mock.AsyncMock()
    monkeypatch.setattr("atr.worker._task_result_process", result_process)

    await worker._task_process(1, sql.TaskType.MESSAGE_SEND.value, {}, "alice")

    defer.assert_awaited_once_with(1)
    result_process.assert_not_awaited()


async def test_task_process_fails_when_handler_ldap_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(args: object) -> None:
        raise ldap.UnavailableError("down")

    monkeypatch.setattr("atr.config.is_production_mode", lambda: False)
    monkeypatch.setattr("atr.config.is_ldap_configured", lambda: False)
    monkeypatch.setattr("atr.tasks.resolve", lambda task_type: handler)
    defer = mock.AsyncMock()
    monkeypatch.setattr("atr.worker._task_defer", defer)
    result_process = mock.AsyncMock()
    monkeypatch.setattr("atr.worker._task_result_process", result_process)

    await worker._task_process(1, sql.TaskType.MESSAGE_SEND.value, {}, "alice")

    defer.assert_not_awaited()
    result_process.assert_awaited_once_with(1, None, task.FAILED, "down", error_data=None)


async def test_task_process_marks_retryable_check_failures_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(args: object) -> None:
        raise task.CheckRetryableError("clone failed", {"repo_url": "https://example.invalid/repo.git"})

    monkeypatch.setattr("atr.config.is_production_mode", lambda: False)
    monkeypatch.setattr("atr.config.is_ldap_configured", lambda: False)
    monkeypatch.setattr("atr.tasks.resolve", lambda task_type: handler)
    result_process = mock.AsyncMock()
    monkeypatch.setattr("atr.worker._task_result_process", result_process)

    await worker._task_process(1, sql.TaskType.COMPARE_SOURCE_TREES.value, {}, "alice")

    result_process.assert_awaited_once_with(
        1,
        None,
        task.BROKEN,
        "clone failed",
        error_data={"repo_url": "https://example.invalid/repo.git"},
    )
