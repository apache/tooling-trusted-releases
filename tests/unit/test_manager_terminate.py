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

import asyncio
import signal
import types
import unittest.mock as mock

import pytest

import atr.manager as manager
import atr.models.sql as sql


async def test_a_failed_background_stop_is_logged_and_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.killpg", mock.Mock(side_effect=PermissionError("not permitted")))
    errors = []
    monkeypatch.setattr(manager.log, "error", lambda message: errors.append(message))
    worker_process = _worker_process()
    worker_manager = manager.WorkerManager()

    stop_task = worker_manager.stop_worker_in_background(worker_process)
    await asyncio.gather(stop_task, return_exceptions=True)

    assert worker_process.stopping is False
    assert worker_manager.stop_tasks == set()
    assert any("Error stopping a worker" in message for message in errors)


async def test_stop_worker_escalates_when_sigterm_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [999])
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    hanging = True

    async def wait() -> int:
        nonlocal hanging
        if hanging:
            hanging = False
            await asyncio.sleep(60)
        return -9

    worker_process = types.SimpleNamespace(pid=999, stopping=False, process=types.SimpleNamespace(wait=wait))
    worker_manager = manager.WorkerManager(terminate_grace_seconds=0.01)
    monkeypatch.setattr(manager, "_KILL_WAIT_SECONDS", 0.05)

    await worker_manager.stop_worker(worker_process)

    assert killpg.call_args_list == [mock.call(999, signal.SIGTERM), mock.call(999, signal.SIGKILL)]
    assert worker_process.stopping is False


async def test_stop_worker_kills_the_group_when_a_child_survives(monkeypatch: pytest.MonkeyPatch) -> None:
    members = [[123], []]
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: members.pop(0))
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = _worker_process()

    await manager.WorkerManager().stop_worker(worker_process)

    assert killpg.call_args_list == [mock.call(999, signal.SIGTERM), mock.call(999, signal.SIGKILL)]


async def test_stop_worker_reports_a_child_which_outlives_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [123])
    monkeypatch.setattr(manager, "_KILL_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(manager, "_KILL_POLL_SECONDS", 0.01)
    errors = []
    monkeypatch.setattr(manager.log, "error", lambda message: errors.append(message))
    worker_process = _worker_process()

    await manager.WorkerManager().stop_worker(worker_process)

    assert len(errors) == 1
    assert "live members after SIGKILL" in errors[0]
    assert worker_process.stopping is False


async def test_stop_worker_stays_stoppable_when_stopping_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.killpg", mock.Mock(side_effect=PermissionError("not permitted")))
    worker_process = _worker_process()

    with pytest.raises(PermissionError):
        await manager.WorkerManager().stop_worker(worker_process)

    assert worker_process.stopping is False


async def test_stop_worker_waits_for_a_child_which_is_slow_to_die(monkeypatch: pytest.MonkeyPatch) -> None:
    # The child is still listed for the first few looks after SIGKILL, as it is in reality
    looks = [[123], [123], [123], []]
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: looks.pop(0) if looks else [])
    monkeypatch.setattr(manager, "_KILL_POLL_SECONDS", 0.01)
    errors = []
    monkeypatch.setattr(manager.log, "error", lambda message: errors.append(message))
    worker_process = _worker_process()

    await manager.WorkerManager().stop_worker(worker_process)

    assert errors == []
    assert worker_process.stopping is True


async def test_terminate_does_not_wait_for_the_worker_to_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    finalise = mock.AsyncMock(return_value=True)
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [])
    monkeypatch.setattr("os.killpg", mock.Mock())
    worker_process = types.SimpleNamespace(pid=999, stopping=False, process=types.SimpleNamespace(wait=_never))
    worker_manager = manager.WorkerManager(terminate_grace_seconds=30.0)

    async with asyncio.timeout(5):
        terminated = await worker_manager.terminate_long_running_task(_active_task(), worker_process, 7, 999, 300.0)

    assert terminated is True
    for stop_task in worker_manager.stop_tasks:
        stop_task.cancel()


async def test_terminate_kills_only_after_finalisation(monkeypatch: pytest.MonkeyPatch) -> None:
    finalise = mock.AsyncMock(return_value=True)
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [])
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = _worker_process()
    worker_manager = manager.WorkerManager()

    terminated = await worker_manager.terminate_long_running_task(_active_task(), worker_process, 7, 999, 300.0)
    await asyncio.gather(*worker_manager.stop_tasks)

    assert terminated is True
    assert worker_process.stopping is True
    finalise.assert_awaited_once()
    killpg.assert_called_once_with(999, signal.SIGTERM)


async def test_terminate_reports_failure_when_finalisation_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    finalise = mock.AsyncMock(side_effect=RuntimeError("database is busy"))
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = _worker_process()

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
    worker_process = _worker_process()

    terminated = await manager.WorkerManager().terminate_long_running_task(
        _active_task(), worker_process, 7, 999, 300.0
    )

    assert terminated is False
    assert worker_process.stopping is False
    killpg.assert_not_called()


def _active_task() -> sql.Task:
    return sql.Task(
        status=sql.TaskStatus.ACTIVE, task_type=sql.TaskType.COMPARE_SOURCE_TREES, task_args={}, asf_uid="alice"
    )


async def _never() -> int:
    await asyncio.sleep(3600)
    return 0


def _worker_process() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        pid=999, stopping=False, process=types.SimpleNamespace(wait=mock.AsyncMock(return_value=0))
    )
