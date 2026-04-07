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

import pydantic

import atr.log as log
import atr.models.results as results
import atr.models.schema as schema
import atr.tasks as tasks
import atr.tasks.checks as checks


class MaintenanceArgs(schema.Strict):
    """Arguments for the task to perform scheduled maintenance."""

    asf_uid: str = schema.description("The ASF UID of the user triggering the maintenance")
    next_schedule_seconds: int = pydantic.Field(default=0, description="The next scheduled time")


class MaintenanceError(Exception):
    pass


@checks.with_model(MaintenanceArgs)
async def run(args: MaintenanceArgs) -> results.Results | None:
    """Run maintenance."""
    log.info(f"Starting maintenance (user: {args.asf_uid})")

    try:
        await _storage_maintenance()

        log.info(
            "Storage maintenance completed successfully",
        )

        await tasks.schedule_next(args.asf_uid, args.next_schedule_seconds, tasks.run_maintenance)

        return results.Maintenance(
            kind="maintenance",
        )
    except Exception as e:
        error_msg = f"Unexpected error during maintenance: {e!s}"
        log.exception("Maintenance failed with unexpected error")
        raise MaintenanceError(error_msg) from e


async def _storage_maintenance():
    pass
