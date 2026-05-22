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

import datetime
import time
from typing import Final

import sqlmodel

import atr.db as db
import atr.log as log
import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.tasks.inactivity as inactivity

_EXPIRED_TOKEN_RETENTION_DAYS: Final[int] = 30
_FORM_ERROR_TTL_SECONDS: Final[int] = 24 * 60 * 60


class MaintenanceError(Exception):
    pass


@checks.with_model(args.MaintenanceArgs)
async def run(task_args: args.MaintenanceArgs) -> results.Results | None:
    """Run maintenance."""
    log.info(f"Starting maintenance (user: {task_args.asf_uid})")

    try:
        # We schedule first to keep the start time approximately consistent
        # (and knowing this task will finish before it's re-scheduled)
        await tasks.schedule_next(task_args.asf_uid, task_args.next_schedule_seconds, tasks.run_maintenance)

        await _expired_pats_maintenance()
        await _session_data_maintenance()
        await _storage_maintenance()
        await _workflow_ssh_keys_maintenance()
        await _inactivity_maintenance()

        log.info(
            "Storage maintenance completed successfully",
        )

        return results.Maintenance(
            kind="maintenance",
        )
    except Exception as e:
        error_msg = f"Unexpected error during maintenance: {e!s}"
        log.exception("Maintenance failed with unexpected error")
        raise MaintenanceError(error_msg) from e


async def _expired_pats_maintenance() -> None:
    via = sql.validate_instrumented_attribute
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=_EXPIRED_TOKEN_RETENTION_DAYS)
    async with db.session() as data:
        statement = sqlmodel.delete(sql.PersonalAccessToken).where(via(sql.PersonalAccessToken.expires) < cutoff)
        result = await data.execute(statement)
        await data.commit()
        deleted = getattr(result, "rowcount", 0) or 0
        if deleted > 0:
            log.info(f"Purged {deleted} expired personal access tokens")


async def _inactivity_maintenance() -> None:
    try:
        plans = await inactivity.run_scan()
    except Exception:
        log.exception("Inactivity scan failed")
        return
    for plan in plans:
        if plan.decision == inactivity.Decision.SKIP:
            continue
        try:
            await inactivity.apply_plan(plan)
        except Exception:
            log.exception(f"Inactivity action failed for release {plan.release_key!r}")


async def _session_data_maintenance() -> None:
    via = sql.validate_instrumented_attribute
    cutoff = time.time() - _FORM_ERROR_TTL_SECONDS
    async with db.session() as data:
        statement = sqlmodel.delete(sql.SessionFormError).where(via(sql.SessionFormError.cts) < cutoff)
        await data.execute(statement)
        await data.commit()


async def _storage_maintenance():
    pass


async def _workflow_ssh_keys_maintenance() -> None:
    via = sql.validate_instrumented_attribute
    now = int(time.time())
    async with db.session() as data:
        statement = sqlmodel.delete(sql.WorkflowSSHKey).where(via(sql.WorkflowSSHKey.expires) < now)
        result = await data.execute(statement)
        await data.commit()
        deleted = getattr(result, "rowcount", 0) or 0
        if deleted > 0:
            log.info(f"Purged {deleted} expired workflow SSH keys")
