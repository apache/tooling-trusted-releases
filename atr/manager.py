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
import datetime
import io
import os
import signal
import sys

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


class WorkerManager:
    """Manager for a pool of worker processes."""

    def __init__(
        self,
        min_workers: int = 4,
        check_interval_seconds: float = 2.0,
        max_task_seconds: float = 300.0,
    ):
        self.min_workers = min_workers
        self.max_workers = 2 * min_workers
        self.check_interval_seconds = check_interval_seconds
        self.max_task_seconds = max_task_seconds
        self.workers: dict[int, WorkerProcess] = {}
        self.budget_index = 0
        self.running = False
        self.check_task: asyncio.Task | None = None

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
        for worker in list(self.workers.values()):
            if worker.pid:
                try:
                    os.kill(worker.pid, signal.SIGTERM)
                except ProcessLookupError:
                    # The process may have already exited
                    ...
                except Exception as e:
                    log.error(f"Error stopping worker {worker.pid}: {e}")

        # Wait for processes to exit
        for worker in list(self.workers.values()):
            try:
                await asyncio.wait_for(worker.process.wait(), timeout=5.0)
            except TimeoutError:
                if worker.pid:
                    try:
                        os.kill(worker.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        # The process may have already exited
                        ...
                    except Exception as e:
                        log.error(f"Error force killing worker {worker.pid}: {e}")

        self.workers.clear()

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

                # Check if worker has been processing its task for too long
                # This also stops tasks if they have indeed been running for too long
                if await self.check_task_duration(data, pid, worker):
                    exited_workers.append(pid)
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
        self, active_task: sql.Task, worker: WorkerProcess, task_id: int, pid: int
    ) -> None:
        """
        Terminate a task that has been running for too long.
        Updates the task status and terminates the worker process.
        """
        try:
            # Mark the task as failed
            active_task.status = sql.TaskStatus.FAILED
            active_task.completed = datetime.datetime.now(datetime.UTC)
            active_task.error = f"Task terminated after exceeding time limit of {self.max_task_seconds} seconds"

            if worker.pid:
                os.killpg(worker.pid, signal.SIGTERM)
                log.info(f"Worker {pid} terminated after processing task {task_id} for > {self.max_task_seconds}s")
        except ProcessLookupError:
            return
        except Exception as e:
            log.error(f"Error stopping long-running worker {pid}: {e}")

    async def check_task_duration(self, data: db.Session, pid: int, worker: WorkerProcess) -> bool:
        """
        Check whether a worker has been processing its task for too long.
        Returns True if the worker has been terminated.
        """
        notify_args = None
        try:
            async with data.begin():
                active_task = await data.task(pid=pid, status=sql.TaskStatus.ACTIVE).get()
                if (not active_task) or (not active_task.started):
                    return False

                task_duration = (datetime.datetime.now(datetime.UTC) - active_task.started).total_seconds()
                if task_duration <= self.max_task_seconds:
                    return False

                await self.terminate_long_running_task(active_task, worker, active_task.id, pid)
                notify_args = (
                    active_task.asf_uid,
                    active_task.task_type,
                    active_task.project_key,
                    active_task.version_key,
                    active_task.revision_number,
                    active_task.primary_rel_path,
                    active_task.error or "task terminated",
                )
        except Exception as e:
            log.error(f"Error checking task duration for worker {pid}: {e}")
            # TODO: Return False here to avoid over-reporting errors
            return False
        await task.notify_failure(*notify_args)
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
                        via(sql.Task.status).in_([sql.TaskStatus.COMPLETED, sql.TaskStatus.FAILED]),
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
        current_count = len(self.workers)
        if current_count < self.min_workers:
            log.info(f"Worker pool below minimum ({current_count} < {self.min_workers}), spawning new workers")
            while len(self.workers) < self.min_workers:
                await self.spawn_worker()
            log.info(f"Worker pool restored to {len(self.workers)} workers")

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


class WorkerProcess:
    """Interface to control a worker process."""

    def __init__(self, process: asyncio.subprocess.Process, started: datetime.datetime, budget: int):
        self.process = process
        self.started = started
        self.last_checked = started
        self.budget = budget
        self.pre_spawned = False

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
