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
    result_process.assert_awaited_once_with(1, None, task.FAILED, "down")
