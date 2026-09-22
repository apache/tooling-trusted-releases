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

import atr.cache as cache
import atr.db as db
import atr.log as log
import atr.models.args as args
import atr.storage as storage
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.validate as validate


@checks.with_model(args.IntegrityCheckArgs)
async def check(task_args: args.IntegrityCheckArgs) -> None:
    await tasks.schedule_next(task_args.asf_uid, task_args.next_schedule_seconds, tasks.integrity_check)
    validation_errors = 0
    try:
        async with db.session() as data:
            async for divergence in validate.everything(data):
                log.error(f"Integrity validation error: {divergence!r}")
                validation_errors += 1
            report = await validate.consistency(data)
    except Exception:
        await _notify_admins("Integrity check could not complete. See worker logs for details.")
        raise

    for path in report.db_only:
        log.error(f"Integrity consistency error: directory missing from filesystem: {path}")
    for path in report.fs_only:
        log.error(f"Integrity consistency error: directory missing from database: {path}")
    consistency_errors = len(report.db_only) + len(report.fs_only)
    message = None
    if validation_errors or consistency_errors:
        message = (
            f"Integrity check found {validation_errors} validation errors and {consistency_errors} consistency errors."
            " See worker logs for details."
        )
    await _notify_admins(message)


async def _notify_admins(message: str | None) -> None:
    for asf_uid in sorted(await cache.admins_get_async()):
        async with storage.write_as_user_service(asf_uid) as waus:
            await waus.notifications_replace(
                message, link="/admin/data?tab=validation", link_text="View checks", is_admin=True
            )
