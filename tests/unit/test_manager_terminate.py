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

import types
import unittest.mock as mock

import pytest

import atr.manager as manager
import atr.models.sql as sql


async def test_terminate_kills_only_after_finalisation(monkeypatch: pytest.MonkeyPatch) -> None:
    finalise = mock.AsyncMock(return_value=True)
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = types.SimpleNamespace(pid=999)

    terminated = await manager.WorkerManager().terminate_long_running_task(
        _active_task(), worker_process, 7, 999, 300.0
    )

    assert terminated is True
    finalise.assert_awaited_once()
    killpg.assert_called_once()


async def test_terminate_reports_failure_when_finalisation_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    finalise = mock.AsyncMock(side_effect=RuntimeError("database is busy"))
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = types.SimpleNamespace(pid=999)

    terminated = await manager.WorkerManager().terminate_long_running_task(
        _active_task(), worker_process, 7, 999, 300.0
    )

    assert terminated is False
    killpg.assert_not_called()


async def test_terminate_spares_the_worker_when_the_task_was_already_finalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalise = mock.AsyncMock(return_value=False)
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = types.SimpleNamespace(pid=999)

    terminated = await manager.WorkerManager().terminate_long_running_task(
        _active_task(), worker_process, 7, 999, 300.0
    )

    assert terminated is False
    killpg.assert_not_called()


def _active_task() -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.ACTIVE, task_type=sql.TaskType.COMPARE_SOURCE_TREES, task_args={}, asf_uid="alice"
    )
