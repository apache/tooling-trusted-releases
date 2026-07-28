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

from typing import Any, Final

import atr.constants as constants
import atr.log as log
import atr.models.results as results
import atr.models.sql as sql
import atr.storage as storage

QUEUED: Final = sql.TaskStatus.QUEUED
ACTIVE: Final = sql.TaskStatus.ACTIVE
COMPLETED: Final = sql.TaskStatus.COMPLETED
FAILED: Final = sql.TaskStatus.FAILED
BROKEN: Final = sql.TaskStatus.BROKEN

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
    task_type: sql.TaskType,
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
    parts = [f"{task_type.label} failed"]
    if location_parts:
        parts.append(f"for {' '.join(location_parts)}")
    if path_text:
        parts.append(f"({path_text})")
    return f"{' '.join(parts)}: {_truncate_single_line(error, 500)}"


async def notify_failure(
    asf_uid: str | None,
    task_type: sql.TaskType,
    project_key: str | None,
    version_key: str | None,
    revision_number: str | None,
    primary_rel_path: str | None,
    error: str,
) -> None:
    if (not asf_uid) or (asf_uid == constants.SYSTEM_SERVICE_UID):
        return
    message = failure_message(task_type, project_key, version_key, revision_number, primary_rel_path, error)
    try:
        async with storage.write_as_user_service(asf_uid) as waus:
            await waus.notifications_create(message, sql.NotificationLevel.ERROR)
    except Exception:
        log.exception("Failed to record failure notification for task")


def _truncate_single_line(text: str | None, max_length: int) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0]
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text
