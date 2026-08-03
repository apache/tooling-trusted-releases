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

"""Worker process manager."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import io
import os
import signal
import sys
from typing import Final

import psutil
import sqlalchemy
import sqlalchemy.engine as engine
import sqlmodel

import atr.constants as constants
import atr.db as db
import atr.log as log
import atr.models.sql as sql
import atr.tasks.task as task
import atr.util as util

# Global debug flag to control worker process output capturing
global_worker_debug: bool = False

# Global worker manager instance
# Can't use "StringClass" | None, must use Optional["StringClass"] for forward references
global_worker_manager: WorkerManager | None = None


_KILL_POLL_SECONDS: Final = 0.1
_KILL_WAIT_SECONDS: Final = 5.0
_MEMORY_TERMINATE_LIMIT_BYTES: Final[int] = (constants.WORKER_MEMORY_LIMIT_BYTES * 6) // 5


class WorkerManager:
    """Manager for a pool of worker processes."""

    def __init__(
        self,
        min_workers: int = 4,
        check_interval_seconds: float = 2.0,
        max_task_seconds: float = 300.0,
        terminate_grace_seconds: float = 10.0,
    ):
        self.min_workers = min_workers
        self.max_workers = 2 * min_workers
        self.check_interval_seconds = check_interval_seconds
        self.max_task_seconds = max_task_seconds
        self.terminate_grace_seconds = terminate_grace_seconds
        self.workers: dict[int, WorkerProcess] = {}
        self.stop_tasks: set[asyncio.Task] = set()
        self.budget_index = 0
        self.running = False
        self.check_task: asyncio.Task | None = None

    def active_worker_count(self) -> int:
        return sum(1 for worker in self.workers.values() if not worker.stopping)

    async def start(self) -> None:
        """Start the worker manager."""
        if self.running:
            return

        self.running = True
        log.info(f"Starting worker manager in {os.getcwd()}")

        # Start initial workers
        for _ in range(self.min_workers):
            await self.spawn_worker()

        # Start monitoring task
        self.check_task = asyncio.create_task(self.monitor_workers())

    async def stop(self) -> None:
        """Stop all workers and the manager."""
        if not self.running:
            return

        self.running = False
        log.info("Stopping worker manager")

        # Cancel monitoring task
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                ...

        # Stop all workers
        await self.stop_all_workers()

    async def stop_all_workers(self) -> None:
        """Stop all worker processes."""
        # Stopping concurrently bounds shutdown by the slowest worker rather than by their sum
        stopping = [self.stop_worker(worker) for worker in self.workers.values() if not worker.stopping]
        awaitables = stopping + list(self.stop_tasks)
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)
        self.workers.clear()

    async def stop_worker(self, worker: WorkerProcess) -> None:
        pid = worker.pid
        if not pid:
            return
        worker.stopping = True
        # Assume the group survives, so that an error before any check leaves the worker stoppable again
        live_members = [pid]
        try:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGTERM)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(worker.process.wait(), timeout=self.terminate_grace_seconds)
            # The worker may have exited while a child of it ignored the signal, so check the whole group
            live_members = await asyncio.to_thread(_live_process_group_members, pid)
            if not live_members:
                return
            log.warning(f"Process group of worker {pid} survived SIGTERM, sending SIGKILL")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            live_members = await self.wait_for_process_group(pid)
            if live_members:
                log.error(f"Process group of worker {pid} has live members after SIGKILL: {live_members}")
        finally:
            # A group which is still alive can be stopped again, so do not skip the worker forever
            worker.stopping = not live_members

    def stop_worker_in_background(self, worker: WorkerProcess) -> asyncio.Task:
        worker.stopping = True
        stop_task = asyncio.create_task(self.stop_worker(worker))
        self.stop_tasks.add(stop_task)
        stop_task.add_done_callback(self.stop_task_done)
        return stop_task

    def stop_task_done(self, stop_task: asyncio.Task) -> None:
        self.stop_tasks.discard(stop_task)
        if stop_task.cancelled():
            return
        error = stop_task.exception()
        if error is not None:
            log.error(f"Error stopping a worker: {error}")

    async def wait_for_process_group(self, pgid: int) -> list[int]:
        # Signal delivery is not instant, so poll rather than declaring survivors on the first look
        deadline = asyncio.get_running_loop().time() + _KILL_WAIT_SECONDS
        while True:
            live_members = await asyncio.to_thread(_live_process_group_members, pgid)
            if (not live_members) or (asyncio.get_running_loop().time() >= deadline):
                return live_members
            await asyncio.sleep(_KILL_POLL_SECONDS)

    async def spawn_worker(self) -> None:
        """Spawn a new worker process."""
        if len(self.workers) >= self.max_workers:
            return

        try:
            # Get the absolute path to the project root (i.e. atr/..)
            abs_path = await asyncio.to_thread(os.path.abspath, __file__)
            project_root = os.path.dirname(os.path.dirname(abs_path))

            # Ensure PYTHONPATH includes our project root
            env = os.environ.copy()
            python_path = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{project_root}:{python_path}" if python_path else project_root

            budget = constants.WORKER_TASK_BUDGETS[self.budget_index % len(constants.WORKER_TASK_BUDGETS)]
            self.budget_index += 1
            env[constants.WORKER_MAX_TASKS_ENV] = str(budget)

            # Get absolute path to worker script
            worker_script = os.path.join(project_root, "atr", "worker.py")

            # Handle stdout and stderr based on debug setting
            stdout_target: int | io.TextIOWrapper = asyncio.subprocess.DEVNULL
            stderr_target: int | io.TextIOWrapper = asyncio.subprocess.DEVNULL

            # Generate a unique log file name for this worker if debugging is enabled
            log_file_path = None
            if global_worker_debug:
                timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
                log_file_name = f"worker_{timestamp}_{os.getpid()}.log"
                log_file_path = os.path.join(project_root, "state", log_file_name)

                # Open log file for writing
                log_file = await asyncio.to_thread(open, log_file_path, "w")
                stdout_target = log_file
                stderr_target = log_file
                log.info(f"Worker output will be logged to {log_file_path}")

            # Start worker process with the updated environment
            # Use preexec_fn to create new process group
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                worker_script,
                stdout=stdout_target,
                stderr=stderr_target,
                env=env,
                preexec_fn=os.setsid,
            )

            worker = WorkerProcess(process, datetime.datetime.now(datetime.UTC), budget)
            if worker.pid:
                self.workers[worker.pid] = worker
                log.info(f"Started worker process {worker.pid} with task budget {budget}")
                if global_worker_debug and log_file_path:
                    log.info(f"Worker {worker.pid} logs: {log_file_path}")
            else:
                log.error("Failed to start worker process: No PID assigned")
                if global_worker_debug and isinstance(stdout_target, io.TextIOWrapper):
                    await asyncio.to_thread(stdout_target.close)
        except Exception as e:
            log.error(f"Error spawning worker: {e}")

    async def monitor_workers(self) -> None:
        """Monitor worker processes and restart them if needed."""
        while self.running:
            try:
                await self.check_workers()
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.exception(f"Error in worker monitor: {e}")
                # TODO: How long should we wait before trying again?
                await asyncio.sleep(1.0)

    async def check_workers(self) -> None:
        """Check worker processes and restart if needed."""
        exited_workers = []

        async with db.session() as data:
            # Check each worker first
            for pid, worker in list(self.workers.items()):
                # Check if process is running
                if not await worker.is_running():
                    exited_workers.append(pid)
                    log.info(f"Worker {pid} has exited")
                    continue

                # A worker being stopped in the background stays tracked until it is seen to exit
                if worker.stopping:
                    continue

                if await self.check_worker_memory(data, pid, worker):
                    exited_workers.append(pid)
                    continue

                # Check if worker has been processing its task for too long
                # This also stops tasks if they have indeed been running for too long
                if await self.check_task_duration(data, pid, worker):
                    continue

                await self.pre_spawn_replacement(data, pid, worker)

        # Remove exited workers
        for pid in exited_workers:
            self.workers.pop(pid, None)

        # Check for active tasks
        # try:
        #     async with get_session() as session:
        #         result = await session.execute(
        #             text("""
        #                 SELECT COUNT(*)
        #                 FROM task
        #                 WHERE status = 'QUEUED'
        #             """)
        #         )
        #         queued_count = result.scalar()
        #         log.info(f"Found {queued_count} queued tasks waiting for workers")
        # except Exception as e:
        #     log.error(f"Error checking queued tasks: {e}")

        # Spawn new workers if needed
        await self.maintain_worker_pool()

        # Reset any tasks that were being processed by now inactive workers
        await self.reset_broken_tasks()

    async def terminate_long_running_task(
        self, active_task: sql.Task, worker: WorkerProcess, task_id: int, pid: int, limit: float
    ) -> bool:
        """
        Terminate a task that has been running for too long.
        Updates the task status and starts terminating the worker process.
        """
        try:
            # Mark the task as failed
            status = task.BROKEN if (active_task.task_type in task.CHECK_TASK_TYPES) else task.FAILED
            error = f"Task terminated after exceeding time limit of {limit} seconds"
            if not await task.finalise_failure(task_id, pid, error, status):
                log.info(f"Task {task_id} was already finalised, not terminating worker {pid}")
                return False

            if worker.pid:
                # Stopping in the background, because waiting here would delay every later worker
                self.stop_worker_in_background(worker)
                log.info(f"Worker {pid} is being stopped after processing task {task_id} for > {limit}s")
            return True
        except ProcessLookupError:
            return True
        except Exception as e:
            log.error(f"Error stopping long-running worker {pid}: {e}")
            return False

    async def check_task_duration(self, data: db.Session, pid: int, worker: WorkerProcess) -> bool:
        """
        Check whether a worker has been processing its task for too long.
        Returns True if the worker has been terminated.
        """
        active_task = None
        limit = self.max_task_seconds
        try:
            async with data.begin():
                candidate = await data.task(pid=pid, status=sql.TaskStatus.ACTIVE).get()
                if (not candidate) or (not candidate.started):
                    return False

                limit = task.TASK_TYPE_TIMEOUT_SECONDS.get(candidate.task_type, self.max_task_seconds)
                task_duration = (datetime.datetime.now(datetime.UTC) - candidate.started).total_seconds()
                if task_duration <= limit:
                    return False

                active_task = candidate
        except Exception as e:
            log.error(f"Error checking task duration for worker {pid}: {e}")
            # TODO: Return False here to avoid over-reporting errors
            return False
        return await self.terminate_long_running_task(active_task, worker, active_task.id, pid, limit)

    async def check_worker_memory(self, data: db.Session, pid: int, worker: WorkerProcess) -> bool:
        active_task = None
        try:
            async with data.begin():
                active_task = await data.task(pid=pid, status=sql.TaskStatus.ACTIVE).get()
        except Exception as e:
            log.error(f"Error checking active task for worker {pid}: {e}")
        try:
            rss = await asyncio.to_thread(_worker_tree_rss, pid)
        except Exception as e:
            log.error(f"Error sampling memory of worker {pid}: {e}")
            return False
        if rss <= _MEMORY_TERMINATE_LIMIT_BYTES:
            return False
        log.error(f"Worker {pid} process tree uses {rss} bytes of resident memory, terminating")
        if active_task is not None:
            status = task.BROKEN if (active_task.task_type in task.CHECK_TASK_TYPES) else task.FAILED
            error = f"Task terminated because its worker used {rss} bytes of resident memory"
            if not await task.finalise_failure(active_task.id, pid, error, status):
                log.info(f"Task {active_task.id} was already finalised, killing worker {pid} without a verdict")
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        try:
            await asyncio.wait_for(worker.process.wait(), timeout=_KILL_WAIT_SECONDS)
        except TimeoutError:
            log.error(f"Worker {pid} did not exit after SIGKILL, keeping it tracked to retry")
            return False
        live_members = await asyncio.to_thread(_live_process_group_members, pid)
        if live_members:
            log.error(f"Worker {pid} exited after SIGKILL but its process group has live members: {live_members}")
        return True

    async def pre_spawn_replacement(self, data: db.Session, pid: int, worker: WorkerProcess) -> None:
        if worker.pre_spawned:
            return
        via = sql.validate_instrumented_attribute
        try:
            async with data.begin():
                active_query = (
                    sqlmodel.select(sqlalchemy.func.count())
                    .select_from(sql.Task)
                    .where(
                        sql.Task.pid == pid,
                        sql.Task.status == sql.TaskStatus.ACTIVE,
                        via(sql.Task.started) >= worker.started,
                    )
                )
                active = (await data.execute(active_query)).scalar_one()
                if not active:
                    return
                finished_query = (
                    sqlmodel.select(sqlalchemy.func.count())
                    .select_from(sql.Task)
                    .where(
                        sql.Task.pid == pid,
                        via(sql.Task.status).in_(
                            [sql.TaskStatus.COMPLETED, sql.TaskStatus.FAILED, sql.TaskStatus.BROKEN]
                        ),
                        via(sql.Task.started) >= worker.started,
                    )
                )
                finished = (await data.execute(finished_query)).scalar_one()
        except Exception as e:
            log.error(f"Error counting processed tasks for worker {pid}: {e}")
            return
        if finished < (worker.budget - 1):
            return
        worker.pre_spawned = True
        log.info(f"Worker {pid} started the last of its {worker.budget} tasks, spawning a replacement")
        await self.spawn_worker()

    async def maintain_worker_pool(self) -> None:
        """Ensure we maintain the minimum number of workers."""
        # Workers being stopped do not count, but they still occupy a slot below max_workers
        if self.active_worker_count() >= self.min_workers:
            return
        log.info(f"Worker pool below minimum ({self.active_worker_count()} < {self.min_workers}), spawning new workers")
        while self.active_worker_count() < self.min_workers:
            tracked = len(self.workers)
            await self.spawn_worker()
            if len(self.workers) == tracked:
                log.warning("Could not spawn a replacement worker")
                return
        log.info(f"Worker pool restored to {self.active_worker_count()} workers")

    async def _log_tasks_held_by_unmanaged_pids(self, data: db.Session, active_worker_pids: list[int]) -> None:
        """Log tasks that are active and held by PIDs not managed by this worker manager."""
        foreign_tasks_stmt = sqlmodel.select(sql.Task.pid, sql.Task.id).where(
            sqlmodel.and_(
                sql.validate_instrumented_attribute(sql.Task.pid).notin_(active_worker_pids),
                sql.Task.status == sql.TaskStatus.ACTIVE,
                sql.validate_instrumented_attribute(sql.Task.pid).isnot(None),
            )
        )
        foreign_tasks_result = await data.execute(foreign_tasks_stmt)
        foreign_pids_with_tasks: dict[int, int] = {
            row.pid: row.id for row in foreign_tasks_result if row.pid is not None
        }

        if not foreign_pids_with_tasks:
            return

        log.debug(f"Found tasks potentially claimed by non-managed PIDs: {foreign_pids_with_tasks}")
        for foreign_pid, task_id_held in foreign_pids_with_tasks.items():
            try:
                os.kill(foreign_pid, 0)
                log.warning(f"Task {task_id_held} is held by an active, unmanaged process (PID: {foreign_pid})")
            except ProcessLookupError:
                log.info(f"Task {task_id_held} was held by PID {foreign_pid}, which is no longer running")
            except Exception as e:
                log.error(f"Unexpected error: {foreign_pid} holding task {task_id_held}: {e}")

    async def reset_broken_tasks(self) -> None:
        """Reset any tasks that were being processed by exited or unmanaged workers."""
        try:
            async with db.session() as data:
                async with data.begin():
                    active_worker_pids = list(self.workers)
                    try:
                        await self._log_tasks_held_by_unmanaged_pids(data, active_worker_pids)
                    except Exception:
                        ...

                    update_stmt = (
                        sqlmodel.update(sql.Task)
                        .where(
                            sqlmodel.and_(
                                sql.validate_instrumented_attribute(sql.Task.pid).notin_(active_worker_pids),
                                sql.Task.status == sql.TaskStatus.ACTIVE,
                            )
                        )
                        .values(status=sql.TaskStatus.QUEUED, started=None, pid=None)
                    )

                    result = await data.execute(update_stmt)
                    if not isinstance(result, engine.CursorResult):
                        log.error(f"Expected cursor result, got {type(result)}")
                        return
                    if result.rowcount > 0:
                        log.info(f"Reset {util.plural(result.rowcount, 'task')} to state 'QUEUED' due to worker issues")

        except Exception as e:
            log.error(f"Error resetting broken tasks: {e}")


def _live_process_group_members(pgid: int) -> list[int]:
    members = []
    for process in psutil.process_iter():
        try:
            if (os.getpgid(process.pid) == pgid) and (process.status() != psutil.STATUS_ZOMBIE):
                members.append(process.pid)
        except (OSError, psutil.Error):
            continue
    return members


def _worker_tree_rss(pid: int) -> int:
    try:
        return util.process_tree_rss(psutil.Process(pid))
    except psutil.NoSuchProcess:
        return 0


class WorkerProcess:
    """Interface to control a worker process."""

    def __init__(self, process: asyncio.subprocess.Process, started: datetime.datetime, budget: int):
        self.process = process
        self.started = started
        self.last_checked = started
        self.budget = budget
        self.pre_spawned = False
        self.stopping = False

    @property
    def pid(self) -> int | None:
        return self.process.pid

    async def is_running(self) -> bool:
        """Check if the process is still running."""
        if self.process.returncode is not None:
            # Process has already exited
            return False

        if not self.pid:
            # Process did not start
            return False

        try:
            os.kill(self.pid, 0)
            self.last_checked = datetime.datetime.now(datetime.UTC)
            return True
        except ProcessLookupError:
            # Process no longer exists
            return False
        except PermissionError:
            # Process exists, but we don't have permission to signal it
            # This shouldn't happen in our case since we own the process
            log.warning(f"Permission error checking process {self.pid}")
            return False


def get_worker_manager() -> WorkerManager:
    """Get the global worker manager instance."""
    global global_worker_manager
    if global_worker_manager is None:
        global_worker_manager = WorkerManager()
    return global_worker_manager
