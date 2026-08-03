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
import types
import unittest.mock as mock

import pytest

import atr.manager as manager
import atr.models.sql as sql


class Begin:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> bool:
        return False


def test_live_process_group_members_excludes_zombies(monkeypatch: pytest.MonkeyPatch) -> None:
    live = types.SimpleNamespace(pid=1000, status=mock.Mock(return_value=manager.psutil.STATUS_RUNNING))
    zombie = types.SimpleNamespace(pid=1001, status=mock.Mock(return_value=manager.psutil.STATUS_ZOMBIE))
    outside = types.SimpleNamespace(pid=1002, status=mock.Mock(return_value=manager.psutil.STATUS_RUNNING))
    monkeypatch.setattr(manager.psutil, "process_iter", lambda: [live, zombie, outside])
    monkeypatch.setattr(manager.os, "getpgid", lambda pid: 999 if (pid != 1002) else 1002)

    assert manager._live_process_group_members(999) == [1000]


async def test_memory_check_ignores_worker_under_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_worker_tree_rss", lambda pid: 1024)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = _worker_process()

    terminated = await manager.WorkerManager().check_worker_memory(_data(None), 999, worker_process)

    assert terminated is False
    killpg.assert_not_called()


async def test_memory_check_keeps_watching_unkillable_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_worker_tree_rss", lambda pid: manager._MEMORY_TERMINATE_LIMIT_BYTES + 1)
    monkeypatch.setattr(manager, "_KILL_WAIT_SECONDS", 0.01)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)

    async def hanging_wait() -> int:
        await asyncio.sleep(60)
        return 0

    worker_process = types.SimpleNamespace(pid=999, process=types.SimpleNamespace(wait=hanging_wait))

    terminated = await manager.WorkerManager().check_worker_memory(_data(None), 999, worker_process)

    assert terminated is False
    killpg.assert_called_once()


async def test_memory_check_kills_without_verdict_when_task_already_finalised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager, "_worker_tree_rss", lambda pid: manager._MEMORY_TERMINATE_LIMIT_BYTES + 1)
    finalise = mock.AsyncMock(return_value=False)
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = _worker_process()

    terminated = await manager.WorkerManager().check_worker_memory(_data(_active_task()), 999, worker_process)

    assert terminated is True
    finalise.assert_awaited_once()
    killpg.assert_called_once()


async def test_memory_check_logs_live_group_members_after_worker_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_worker_tree_rss", lambda pid: manager._MEMORY_TERMINATE_LIMIT_BYTES + 1)
    monkeypatch.setattr(manager, "_live_process_group_members", lambda pgid: [1000, 1001])
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    log_error = mock.Mock()
    monkeypatch.setattr(manager.log, "error", log_error)
    worker_process = _worker_process()

    terminated = await manager.WorkerManager().check_worker_memory(_data(None), 999, worker_process)

    assert terminated is True
    log_error.assert_any_call("Worker 999 exited after SIGKILL but its process group has live members: [1000, 1001]")


async def test_memory_check_terminates_idle_worker_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_worker_tree_rss", lambda pid: manager._MEMORY_TERMINATE_LIMIT_BYTES + 1)
    finalise = mock.AsyncMock(return_value=True)
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = _worker_process()

    terminated = await manager.WorkerManager().check_worker_memory(_data(None), 999, worker_process)

    assert terminated is True
    finalise.assert_not_awaited()
    killpg.assert_called_once()


async def test_memory_check_terminates_worker_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager, "_worker_tree_rss", lambda pid: manager._MEMORY_TERMINATE_LIMIT_BYTES + 1)
    finalise = mock.AsyncMock(return_value=True)
    monkeypatch.setattr("atr.tasks.task.finalise_failure", finalise)
    killpg = mock.Mock()
    monkeypatch.setattr("os.killpg", killpg)
    worker_process = _worker_process()

    terminated = await manager.WorkerManager().check_worker_memory(_data(_active_task()), 999, worker_process)

    assert terminated is True
    finalise.assert_awaited_once()
    killpg.assert_called_once()
    worker_process.process.wait.assert_awaited_once()


def _active_task() -> sql.Task:
    return sql.Task(
        id=7, status=sql.TaskStatus.ACTIVE, task_type=sql.TaskType.COMPARE_SOURCE_TREES, task_args={}, asf_uid="alice"
    )


def _data(active_task: sql.Task | None) -> types.SimpleNamespace:
    query = types.SimpleNamespace(get=mock.AsyncMock(return_value=active_task))
    return types.SimpleNamespace(begin=lambda: Begin(), task=lambda **kwargs: query)


def _worker_process() -> types.SimpleNamespace:
    return types.SimpleNamespace(pid=999, process=types.SimpleNamespace(wait=mock.AsyncMock(return_value=-9)))
