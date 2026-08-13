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

from __future__ import annotations

import contextlib
import datetime
from typing import Any, Final

import sqlmodel

import atr.constants as constants
import atr.db as db
import atr.log as log
import atr.models.results as results
import atr.models.sql as sql
import atr.storage as storage

QUEUED: Final = sql.TaskStatus.QUEUED
ACTIVE: Final = sql.TaskStatus.ACTIVE
COMPLETED: Final = sql.TaskStatus.COMPLETED
FAILED: Final = sql.TaskStatus.FAILED
BROKEN: Final = sql.TaskStatus.BROKEN

CHECK_TASK_TYPES: Final[frozenset[sql.TaskType]] = frozenset(
    {
        sql.TaskType.ARCHIVE_COMPARISON,
        sql.TaskType.COMPARE_SOURCE_TREES,
        sql.TaskType.HAS_SBOM,
        sql.TaskType.HASHING_CHECK,
        sql.TaskType.LICENSE_FILES,
        sql.TaskType.LICENSE_HEADERS,
        sql.TaskType.PATHS_CHECK,
        sql.TaskType.RAT_CHECK,
        sql.TaskType.SIGNATURE_CHECK,
        sql.TaskType.TARGZ_STRUCTURE,
        sql.TaskType.ZIPFORMAT_STRUCTURE,
    }
)

# The recurring tasks reschedule themselves and carry no lasting per-run value, so on
# success they are logged to a file and dropped rather than kept in the table.
RECURRING_TASK_TYPES: Final[frozenset[sql.TaskType]] = frozenset(
    {
        sql.TaskType.CATALOG_SITE_GENERATE,
        sql.TaskType.DISTRIBUTION_STATUS,
        sql.TaskType.MAINTENANCE,
        sql.TaskType.METADATA_UPDATE,
        sql.TaskType.WORKFLOW_STATUS,
    }
)

# Per-type overrides for the worker's default per-task duration limit, in seconds. A type
# not listed uses the worker's default. A full catalog-site rebuild writes tens of thousands
# of files, so it needs far longer than the usual limit without being unbounded.
TASK_TYPE_TIMEOUT_SECONDS: Final[dict[sql.TaskType, int]] = {
    sql.TaskType.CATALOG_SITE_GENERATE: 1800,
    sql.TaskType.RELEASE_FINALISE: 600,
}


class CheckRetryableError(Exception):
    def __init__(self, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data


class DeferredError(Exception):
    """Raised by a handler to send its own task back to the queue for a later attempt."""


class Error(Exception):
    """Error during task execution."""

    def __init__(self, message: str, *result: results.Results | None) -> None:
        self.message = message
        self.result = result


def failure_message(
    task_type: sql.TaskType | str,
    project_key: str | None,
    version_key: str | None,
    revision_number: str | None,
    primary_rel_path: str | None,
    error: str,
) -> str:
    location_parts = []
    if project_key:
        location_parts.append(project_key)
    if version_key:
        location_parts.append(version_key)
    if revision_number:
        location_parts.append(f"r{revision_number}")
    path_text = _truncate_single_line(primary_rel_path, 180)
    label = task_type.label if isinstance(task_type, sql.TaskType) else str(task_type)
    parts = [f"{label} failed"]
    if location_parts:
        parts.append(f"for {' '.join(location_parts)}")
    if path_text:
        parts.append(f"({path_text})")
    return f"{' '.join(parts)}: {_truncate_single_line(error, 500)}"


async def finalise_failure(
    task_id: int,
    pid: int,
    error: str,
    status: sql.TaskStatus,
    error_data: Any = None,
    caller_data: db.Session | None = None,
) -> bool:
    via = sql.validate_instrumented_attribute
    error = ((error or "").strip()) or "unknown error"
    completed = datetime.datetime.now(datetime.UTC)
    notification: sql.Notification | None = None
    async with db.ensure_session(caller_data) as data:
        async with data.begin():
            update_stmt = (
                sqlmodel.update(sql.Task)
                .where(
                    via(sql.Task.id) == task_id,
                    via(sql.Task.status) == ACTIVE,
                    via(sql.Task.pid) == pid,
                )
                .values(status=status, completed=completed, error=error, result=None)
                .returning(
                    via(sql.Task.task_type),
                    via(sql.Task.asf_uid),
                    via(sql.Task.project_key),
                    via(sql.Task.version_key),
                    via(sql.Task.revision_number),
                    via(sql.Task.primary_rel_path),
                    via(sql.Task.inputs_hash),
                )
            )
            row = (await data.execute(update_stmt)).first()
            if row is None:
                log.warning(f"Task {task_id} was not finalised because it is no longer active")
                return False
            task_type, asf_uid, project_key, version_key, revision_number, primary_rel_path, inputs_hash = row
            with contextlib.suppress(ValueError):
                task_type = sql.TaskType(task_type)
            checker = _checker_name(task_type)
            if (checker is not None) and project_key and version_key:
                data.add(
                    sql.CheckResult(
                        release_key=sql.release_key(project_key, version_key),
                        revision_number=revision_number,
                        checker=checker,
                        checker_version=None,
                        primary_rel_path=primary_rel_path,
                        member_rel_path=None,
                        created=completed,
                        status=sql.CheckResultStatus.EXCEPTION,
                        message=error,
                        data=error_data,
                        cached=False,
                        inputs_hash=inputs_hash,
                    )
                )
            if (checker is None) and asf_uid and (asf_uid != constants.SYSTEM_SERVICE_UID):
                message = failure_message(task_type, project_key, version_key, revision_number, primary_rel_path, error)
                notification = sql.Notification(asf_uid=asf_uid, message=message, level=sql.NotificationLevel.ERROR)
                data.add(notification)
    if notification is not None:
        storage.audit(asf_uid=notification.asf_uid, notification_id=notification.id, level=notification.level.value)
    return True


def _checker_name(task_type: Any) -> str | None:
    import atr.tasks as tasks
    import atr.tasks.checks as checks

    if not isinstance(task_type, sql.TaskType):
        return None
    if task_type not in CHECK_TASK_TYPES:
        return None
    return checks.function_key(tasks.resolve(task_type))


def _truncate_single_line(text: str | None, max_length: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0]
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text
