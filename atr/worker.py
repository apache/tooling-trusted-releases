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

import asyncio
import contextlib
import datetime
import inspect
import json
import logging
import logging.handlers
import os
import signal
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, Final

import psutil
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
import atr.util as util

_CPU_LIMIT_SECONDS: Final = 300
_DEFER_SECONDS: Final = 120
_MEMORY_WATCHDOG_POLL_SECONDS: Final = 0.5
_SHUTDOWN_GRACE_SECONDS: Final = 15.0
_TASK_LOG_LOGGER: Final = "atr.tasks.log"
_TASK_ARG_HIDDEN: Final = "<hidden>"
_MESSAGE_SEND_SENSITIVE_ARGS: Final = frozenset({"body", "email_to", "email_cc", "email_bcc"})


def main() -> None:
    """Main entry point."""
    import atr.config as config

    conf = config.get()
    if os.path.isdir(conf.STATE_DIR):
        os.chdir(conf.STATE_DIR)

    listeners = _setup_logging()
    log.add_context(worker_pid=os.getpid())
    log.info(f"Starting worker process with pid {os.getpid()}")

    tasks: list[asyncio.Task] = []
    shutdown_requested = False

    def _handle_signal(signum: int) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            os._exit(1)
        shutdown_requested = True
        log.info(f"Received signal {signum}, shutting down...")
        # Work in a thread cannot be cancelled, so exit even if the loop cannot finish
        timer = threading.Timer(_SHUTDOWN_GRACE_SECONDS, os._exit, args=(1,))
        timer.daemon = True
        timer.start()
        for t in tasks:
            t.cancel()

    util.cpu_limit_arm(_CPU_LIMIT_SECONDS)
    threading.Thread(target=_memory_watchdog_run, daemon=True).start()

    async def _start() -> None:
        loop = asyncio.get_running_loop()
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, _handle_signal, s)
        await asyncio.create_task(db.init_database_for_worker())
        tasks.append(asyncio.create_task(_worker_loop_run()))
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.debug("Cancelled all running tasks")

    try:
        asyncio.run(_start())
    finally:
        for listener in listeners:
            listener.stop()

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


def _memory_watchdog_run() -> None:
    process = psutil.Process()
    while True:
        try:
            rss = util.process_tree_rss(process)
        except psutil.Error:
            rss = 0
        if rss > constants.WORKER_MEMORY_LIMIT_BYTES:
            log.error(
                f"Worker process tree uses {rss} bytes of resident memory,"
                f" over the limit of {constants.WORKER_MEMORY_LIMIT_BYTES} bytes, terminating"
            )
            os.killpg(os.getpgrp(), signal.SIGKILL)
        time.sleep(_MEMORY_WATCHDOG_POLL_SECONDS)


def _setup_logging() -> list[logging.handlers.QueueListener]:
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
    storage_audit_listener = loggers.setup_dedicated_file_logger(
        "atr.storage.audit",
        conf.STORAGE_AUDIT_LOG_FILE,
        shared_processors,
    )

    # Completed recurring tasks
    task_log_listener = loggers.setup_dedicated_file_logger(
        _TASK_LOG_LOGGER,
        conf.TASK_LOG_FILE,
        shared_processors,
    )
    return [storage_audit_listener, task_log_listener]


def _task_args_for_log(
    task_type: str | sql.TaskType, task_args: list[str] | dict[str, Any]
) -> list[str] | dict[str, Any]:
    if (task_type != sql.TaskType.MESSAGE_SEND) or (not isinstance(task_args, dict)):
        return task_args
    return {
        key: _TASK_ARG_HIDDEN if (key in _MESSAGE_SEND_SENSITIVE_ARGS) else value for key, value in task_args.items()
    }


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
        "task_args": _task_args_for_log(task_obj.task_type, task_obj.task_args),
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
                .values(status=task.QUEUED, started=None, pid=None, pid_created=None, scheduled=scheduled)
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
                .scalar_subquery()
            )

            # Use an UPDATE with a WHERE clause to atomically claim the task
            # This ensures that only one worker can claim a specific task
            now = datetime.datetime.now(datetime.UTC)
            created = psutil.Process().create_time()
            update_stmt = (
                sqlmodel.update(sql.Task)
                .where(sqlmodel.and_(sql.Task.id == oldest_queued_task, sql.Task.status == task.QUEUED))
                .values(status=task.ACTIVE, started=now, pid=os.getpid(), pid_created=created)
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
                log.info(f"Claimed task {task_id} ({task_type}) with args {_task_args_for_log(task_type, task_args)}")
                return task_id, task_type, task_args, asf_uid

            return None


async def _task_process(task_id: int, task_type: str, task_args: list[str] | dict[str, Any], asf_uid: str) -> None:
    """Process a claimed task."""
    import atr.config as config

    log.info(f"Processing task {task_id} ({task_type}) with raw args {_task_args_for_log(task_type, task_args)}")
    try:
        task_type_member = sql.TaskType(task_type)
    except ValueError as e:
        log.error(f"Invalid task type: {task_type}")
        await _task_result_process(task_id, None, task.FAILED, str(e))
        return

    task_results: results.Results | None
    error_data: Any = None
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
    except task.CheckRetryableError as e:
        task_results = None
        status = task.BROKEN
        error_data = e.data
        log.error(f"Task {task_id} failed with a retryable error: {e}")
        error = str(e)
    except Exception as e:
        task_results = None
        status = task.FAILED
        error_details = traceback.format_exc()
        log.error(f"Task {task_id} failed processing: {error_details}")
        error = str(e)
    await _task_result_process(task_id, task_results, status, error, error_data=error_data)


async def _task_result_process(
    task_id: int,
    task_results: results.Results | None,
    status: sql.TaskStatus,
    error: str | None = None,
    error_data: Any = None,
) -> None:
    """Process and store task results in the database."""
    if status in (task.FAILED, task.BROKEN):
        await task.finalise_failure(task_id, os.getpid(), error or "", status, error_data=error_data)
        return
    pid = os.getpid()
    via = sql.validate_instrumented_attribute
    log_record: dict[str, Any] | None = None
    completed = datetime.datetime.now(datetime.UTC)
    async with db.session() as data:
        async with data.begin():
            update_stmt = (
                sqlmodel.update(sql.Task)
                .where(
                    via(sql.Task.id) == task_id,
                    via(sql.Task.status) == task.ACTIVE,
                    via(sql.Task.pid) == pid,
                )
                .values(status=task.COMPLETED, completed=completed, result=task_results)
                .returning(
                    via(sql.Task.task_type),
                    via(sql.Task.task_args),
                    via(sql.Task.asf_uid),
                    via(sql.Task.added),
                    via(sql.Task.started),
                )
            )
            row = (await data.execute(update_stmt)).first()
            if row is None:
                log.warning(f"Task {task_id} was not completed because it is no longer active")
                return
            task_type, task_args, asf_uid, added, started = row
            with contextlib.suppress(ValueError):
                task_type = sql.TaskType(task_type)
            if task_type in task.RECURRING_TASK_TYPES:
                # A successful recurring run leaves only a log line behind
                task_obj = sql.Task(
                    id=task_id,
                    status=task.COMPLETED,
                    task_type=task_type,
                    task_args=task_args,
                    asf_uid=asf_uid,
                    added=added,
                    started=started,
                    pid=pid,
                    completed=completed,
                )
                log_record = _task_completed_record(task_obj, task_results, completed)
                await data.execute(
                    sqlmodel.delete(sql.Task).where(via(sql.Task.id) == task_id, via(sql.Task.pid) == pid)
                )

    if log_record is not None:
        _task_completed_log(log_record)


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
                util.cpu_limit_arm(_CPU_LIMIT_SECONDS)
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


if __name__ == "__main__":
    log.info("Starting ATR worker...")
    try:
        main()
    except Exception as e:
        os.makedirs("logs", exist_ok=True)
        with open("logs/atr-worker-error.log", "a") as f:
            f.write(f"{datetime.datetime.now(datetime.UTC)}: {e}\n")
            f.flush()
