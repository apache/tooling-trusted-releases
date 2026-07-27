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

"""worker.py - Task worker process for ATR"""

# TODO: If started is older than some threshold and status
# is active but the pid is no longer running, we can revert
# the task to status='QUEUED'. For this to work, ideally we
# need to check wall clock time as well as CPU time.

import asyncio
import datetime
import inspect
import json
import logging
import os
import resource
import signal
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, Final

import sqlmodel

import atr.constants as constants
import atr.db as db
import atr.ldap as ldap
import atr.log as log
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.tasks.task as task

# Resource limits, 5 minutes and 3GB
_CPU_LIMIT_SECONDS: Final = 300
_MEMORY_LIMIT_BYTES: Final = 3 * 1024 * 1024 * 1024

_DEFER_SECONDS: Final = 120

# Successful recurring tasks are appended here as JSON lines instead of kept in the database
_TASK_LOG_LOGGER: Final = "atr.tasks.log"

# # Create tables if they don't exist
# SQLModel.metadata.create_all(engine)


def main() -> None:
    """Main entry point."""
    import atr.config as config

    conf = config.get()
    if os.path.isdir(conf.STATE_DIR):
        os.chdir(conf.STATE_DIR)

    _setup_logging()
    log.add_context(worker_pid=os.getpid())
    log.info(f"Starting worker process with pid {os.getpid()}")

    tasks: list[asyncio.Task] = []

    async def _handle_signal(signum: int) -> None:
        log.info(f"Received signal {signum}, shutting down...")

        await db.shutdown_database()

        for t in tasks:
            t.cancel()

        log.debug("Cancelled all running tasks")
        asyncio.get_event_loop().stop()
        log.debug("Stopped event loop")

    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda signum, frame: asyncio.create_task(_handle_signal(signum)))

    _worker_resources_limit_set()

    async def _start() -> None:
        await asyncio.create_task(db.init_database_for_worker())
        tasks.append(asyncio.create_task(_worker_loop_run()))
        await asyncio.gather(*tasks)

    asyncio.run(_start())

    # If the worker decides to stop running (see #230 in _worker_loop_run()), shutdown the database gracefully
    asyncio.run(db.shutdown_database())
    log.info("Exiting worker process")


async def _execute_check_task(
    handler: Callable[..., Awaitable[results.Results | None]],
    task_args: list[str] | dict[str, Any],
    task_id: int,
    task_type: str,
) -> results.Results | None:
    log.debug(f"Handler {handler.__name__} expects checks.FunctionArguments, fetching full task details")
    async with db.session() as data:
        task_obj = await data.task(id=task_id).demand(ValueError(f"Task {task_id} disappeared during processing"))

    # Validate required fields from the Task object itself
    if task_obj.project_key is None:
        raise ValueError(f"Task {task_id} is missing required project_key")
    if task_obj.version_key is None:
        raise ValueError(f"Task {task_id} is missing required version_key")
    if task_obj.revision_number is None:
        raise ValueError(f"Task {task_id} is missing required revision_number")

    if not isinstance(task_args, dict):
        raise TypeError(
            f"Task {task_id} ({task_type}) has non-dict raw args {task_args} which should represent keyword_args"
        )

    project_key = safe.ProjectKey(task_obj.project_key)
    version_key = safe.VersionKey(task_obj.version_key)
    revision_number = safe.RevisionNumber(task_obj.revision_number)

    async def recorder_factory(checker_version: str | None = None) -> checks.Recorder:
        return await checks.Recorder.create(
            checker=handler,
            checker_version=checker_version,
            inputs_hash=task_obj.inputs_hash or "",
            project_key=project_key,
            version_key=version_key,
            revision_number=revision_number,
            primary_rel_path=task_obj.safe_primary_rel_path,
        )

    function_arguments = checks.FunctionArguments(
        recorder=recorder_factory,
        asf_uid=task_obj.asf_uid,
        project_key=project_key,
        version_key=version_key,
        revision_number=revision_number,
        primary_rel_path=task_obj.safe_primary_rel_path,
        extra_args=task_args,
    )
    log.debug(f"Calling {handler.__name__} with structured arguments: {function_arguments}")
    handler_result = await handler(function_arguments)
    return handler_result


def _setup_logging() -> None:
    import atr.config as config
    import atr.loggers as loggers

    conf = config.get()

    os.makedirs("logs", exist_ok=True)
    os.makedirs(os.path.dirname(conf.STORAGE_AUDIT_LOG_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(conf.TASK_LOG_FILE), exist_ok=True)

    shared_processors = loggers.shared_processors()
    output_handler = logging.FileHandler("logs/atr-worker.log")
    output_handler.setFormatter(loggers.create_json_formatter(shared_processors))

    logging.basicConfig(level=logging.INFO, handlers=[output_handler], force=True)

    loggers.configure_structlog(shared_processors)

    # Audit logger
    loggers.setup_dedicated_file_logger(
        "atr.storage.audit",
        conf.STORAGE_AUDIT_LOG_FILE,
        shared_processors,
    )

    # Completed recurring tasks
    loggers.setup_dedicated_file_logger(
        _TASK_LOG_LOGGER,
        conf.TASK_LOG_FILE,
        shared_processors,
    )


def _task_completed_log(record: dict[str, Any]) -> None:
    logging.getLogger(_TASK_LOG_LOGGER).info(json.dumps(record, allow_nan=False))


def _task_completed_record(
    task_obj: sql.Task, result: results.Results | None, completed: datetime.datetime
) -> dict[str, Any]:
    return {
        "id": task_obj.id,
        "task_type": task_obj.task_type.value,
        "status": task.COMPLETED.value,
        "asf_uid": task_obj.asf_uid,
        "added": task_obj.added.isoformat() if task_obj.added else None,
        "started": task_obj.started.isoformat() if task_obj.started else None,
        "completed": completed.isoformat(),
        "task_args": task_obj.task_args,
        "result": result.model_dump(mode="json") if (result is not None) else None,
        "pid": task_obj.pid,
    }


async def _task_defer(task_id: int) -> None:
    via = sql.validate_instrumented_attribute
    scheduled = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=_DEFER_SECONDS)
    async with db.session() as data:
        async with data.begin():
            update_stmt = (
                sqlmodel.update(sql.Task)
                .where(
                    sqlmodel.and_(
                        via(sql.Task.id) == task_id,
                        sql.Task.status == task.ACTIVE,
                        via(sql.Task.pid) == os.getpid(),
                    )
                )
                .values(status=task.QUEUED, started=None, pid=None, scheduled=scheduled)
                .returning(via(sql.Task.id))
            )
            result = await data.execute(update_stmt)
            if result.first() is None:
                log.warning(f"Task {task_id} was not deferred because it is no longer active")


async def _task_next_claim() -> tuple[int, str, list[str] | dict[str, Any], str] | None:
    """
    Attempt to claim the oldest unclaimed task.
    Returns (task_id, task_type, task_args) if successful.
    Returns None if no tasks are available.
    """
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        async with data.begin():
            # Get the ID of the oldest queued task
            oldest_queued_task = (
                sqlmodel.select(sql.Task.id)
                .where(
                    sqlmodel.and_(
                        sql.Task.status == task.QUEUED,
                        sqlmodel.or_(
                            via(sql.Task.scheduled).is_(None),
                            via(sql.Task.scheduled) <= datetime.datetime.now(datetime.UTC),
                        ),
                    )
                )
                .order_by(via(sql.Task.added).asc())
                .limit(1)
            )

            # Use an UPDATE with a WHERE clause to atomically claim the task
            # This ensures that only one worker can claim a specific task
            now = datetime.datetime.now(datetime.UTC)
            update_stmt = (
                sqlmodel.update(sql.Task)
                .where(sqlmodel.and_(sql.Task.id == oldest_queued_task, sql.Task.status == task.QUEUED))
                .values(status=task.ACTIVE, started=now, pid=os.getpid())
                .returning(
                    sql.validate_instrumented_attribute(sql.Task.id),
                    sql.validate_instrumented_attribute(sql.Task.task_type),
                    sql.validate_instrumented_attribute(sql.Task.task_args),
                    sql.validate_instrumented_attribute(sql.Task.asf_uid),
                )
            )

            result = await data.execute(update_stmt)
            claimed_task = result.first()

            if claimed_task:
                task_id, task_type, task_args, asf_uid = claimed_task
                log.info(f"Claimed task {task_id} ({task_type}) with args {task_args}")
                return task_id, task_type, task_args, asf_uid

            return None


async def _task_process(task_id: int, task_type: str, task_args: list[str] | dict[str, Any], asf_uid: str) -> None:
    """Process a claimed task."""
    import atr.config as config

    log.info(f"Processing task {task_id} ({task_type}) with raw args {task_args}")
    try:
        task_type_member = sql.TaskType(task_type)
    except ValueError as e:
        log.error(f"Invalid task type: {task_type}")
        await _task_result_process(task_id, None, task.FAILED, str(e))
        return

    task_results: results.Results | None
    try:
        if (
            asf_uid != constants.SYSTEM_SERVICE_UID
            and not (config.is_test_mode() and asf_uid == "test")
            and (config.is_production_mode() or config.is_ldap_configured())
        ):
            try:
                user_account = await ldap.account_lookup(asf_uid)
            except ldap.UnavailableError as e:
                log.warning(f"Deferring task {task_id} ({task_type}) because LDAP is unavailable: {e}")
                await _task_defer(task_id)
                return
            # We check here to see if the account is banned - in the case of running tasks,
            # we don't really need to worry about admin/membership status as that wouldn't
            # materially affect outstanding worker tasks and is rare anyway.
            if (user_account is None) or ldap.is_banned(user_account):
                raise RuntimeError(f"Account '{asf_uid}' is banned or does not exist")

        handler = tasks.resolve(task_type_member)
        sig = inspect.signature(handler)
        params = list(sig.parameters.values())

        # Check whether the handler is a check handler
        if (len(params) == 1) and (params[0].annotation == checks.FunctionArguments):
            handler_result = await _execute_check_task(handler, task_args, task_id, task_type)
        else:
            # Otherwise, it's not a check handler
            additional_kwargs = {}
            if sig.parameters.get("task_id") is not None:
                additional_kwargs["task_id"] = task_id
            handler_result = await handler(task_args, **additional_kwargs)

        task_results = handler_result
        status = task.COMPLETED
        error = None
    except task.DeferredError:
        log.info(f"Task {task_id} ({task_type}) deferred, re-queued for a later attempt")
        await _task_defer(task_id)
        return
    except Exception as e:
        task_results = None
        status = task.FAILED
        error_details = traceback.format_exc()
        log.error(f"Task {task_id} failed processing: {error_details}")
        error = str(e)
    await _task_result_process(task_id, task_results, status, error)


async def _task_result_process(
    task_id: int, task_results: results.Results | None, status: sql.TaskStatus, error: str | None = None
) -> None:
    """Process and store task results in the database."""
    notify_args: tuple[str | None, sql.TaskType, str | None, str | None, str | None, str | None, str] | None = None
    log_record: dict[str, Any] | None = None
    completed = datetime.datetime.now(datetime.UTC)
    async with db.session() as data:
        async with data.begin():
            # Find the task by ID
            task_obj = await data.task(id=task_id).get()
            if task_obj:
                if (status == task.COMPLETED) and (task_obj.task_type in sql.RECURRING_TASK_TYPES):
                    # A successful recurring run leaves only a log line behind
                    log_record = _task_completed_record(task_obj, task_results, completed)
                    await data.delete(task_obj)
                else:
                    # Update task properties
                    task_obj.status = status
                    task_obj.completed = completed
                    task_obj.result = task_results

                    if status == task.FAILED:
                        normalised_error = ((error or "").strip()) or "unknown error"
                        task_obj.error = normalised_error
                        notify_args = (
                            task_obj.asf_uid,
                            task_obj.task_type,
                            task_obj.project_key,
                            task_obj.version_key,
                            task_obj.revision_number,
                            task_obj.primary_rel_path,
                            normalised_error,
                        )

    if log_record is not None:
        _task_completed_log(log_record)
    if notify_args is not None:
        await task.notify_failure(*notify_args)


async def _worker_loop_run() -> None:
    """Main worker loop."""
    processed = 0
    max_to_process = int(os.environ.get(constants.WORKER_MAX_TASKS_ENV, "10"))
    while True:
        log.clear_context()
        try:
            log.add_context(worker_pid=os.getpid())
            task = await _task_next_claim()
            if task:
                task_id, task_type, task_args, asf_uid = task
                log.add_context(task_id=task_id, task_type=task_type, asf_uid=asf_uid)
                await _task_process(task_id, task_type, task_args, asf_uid)
                processed += 1
                # Only process max_to_process tasks and then exit
                # This prevents memory leaks from accumulating
                # Another worker will be started automatically when one exits
                if processed >= max_to_process:
                    break
            else:
                # No tasks available, wait 100ms before checking again
                await asyncio.sleep(0.1)
        except Exception:
            # TODO: Should probably be more robust about this
            log.exception("Worker loop error")
            await asyncio.sleep(1)


def _worker_resources_limit_set() -> None:
    """Set CPU and memory limits for this process."""
    # TODO: https://github.com/apache/tooling-trusted-releases/issues/411
    # # Set CPU time limit
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (_CPU_LIMIT_SECONDS, _CPU_LIMIT_SECONDS))
        log.info(f"Set CPU time limit to {_CPU_LIMIT_SECONDS} seconds")
    except ValueError as e:
        log.warning(f"Could not set CPU time limit: {e}")

    # Set memory limit
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))
        log.info(f"Set memory limit to {_MEMORY_LIMIT_BYTES} bytes")
    except ValueError as e:
        log.warning(f"Could not set memory limit: {e}")
    return


if __name__ == "__main__":
    log.info("Starting ATR worker...")
    try:
        main()
    except Exception as e:
        os.makedirs("logs", exist_ok=True)
        with open("logs/atr-worker-error.log", "a") as f:
            f.write(f"{datetime.datetime.now(datetime.UTC)}: {e}\n")
            f.flush()
