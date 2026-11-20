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
from collections.abc import Sequence
from typing import Final

import atr.db as db
import atr.db.interaction as interaction
import atr.log as log
import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.storage as storage
import atr.tasks as tasks
import atr.tasks.checks as checks

_BATCH_SIZE: Final[int] = 60
# A distribution that hasn't been found is checked every 2 minutes for its first 15 checks, then hourly
_CHECK_INTERVAL_MIN: Final[datetime.timedelta] = datetime.timedelta(minutes=2)
_CHECK_INTERVAL_MAX: Final[datetime.timedelta] = datetime.timedelta(hours=1)
_FAST_CHECKS: Final[int] = 15
# A lookup can take a minute to time out, and a run is stopped at the worker's task limit
_RUN_SECONDS: Final[int] = 90


@checks.with_model(args.DistributionStatusCheckArgs)
async def status_check(
    task_args: args.DistributionStatusCheckArgs, *, task_id: int | None = None
) -> results.Results | None:
    log.info("Checking pending recorded distributions")
    # Scheduled first, as a run that fails or is stopped for taking too long would otherwise be the last
    await tasks.schedule_next(task_args.asf_uid, task_args.next_schedule_seconds, tasks.distribution_status_check)
    started = time.monotonic()
    async with db.session() as data:
        checking = await data.distribution(status=sql.DistributionStatus.CHECKING).all()
        held = await _held_release_keys(data, checking)
    checking = [dist for dist in checking if dist.release_key not in held]
    for dist in _due(checking, datetime.datetime.now(datetime.UTC)):
        if (time.monotonic() - started) > _RUN_SECONDS:
            log.warning("Distribution check ran out of time, the rest are left for the next run")
            break
        try:
            async with storage.write_as_system(storage.WriteAsDistributionCheckService) as system:
                await system.distributions_check(
                    dist.release_key, dist.platform, dist.owner_namespace, dist.package, dist.version
                )
        except Exception:
            log.exception(f"Failed to check distribution {dist.title}")
    return results.DistributionStatusCheck(
        kind="distribution_status",
    )


def _due(checking: Sequence[sql.Distribution], now: datetime.datetime) -> list[sql.Distribution]:
    # The longest overdue go first, so a full batch doesn't leave the same rows waiting each run
    waiting: list[tuple[datetime.datetime, sql.Distribution]] = []
    for dist in checking:
        if dist.last_checked is None:
            waiting.append((datetime.datetime.min.replace(tzinfo=datetime.UTC), dist))
            continue
        due = _next_check(dist.retries, dist.last_checked)
        if due <= now:
            waiting.append((due, dist))
    waiting.sort(key=lambda pair: pair[0])
    return [dist for _due_at, dist in waiting[:_BATCH_SIZE]]


async def _held_release_keys(data: db.Session, checking: Sequence[sql.Distribution]) -> set[str]:
    # Rows of an embargoed release are left out, as they'd otherwise fill each batch without being looked for
    held: set[str] = set()
    for release_key in {dist.release_key for dist in checking}:
        release = await data.release(key=release_key).get()
        if (release is not None) and await interaction.release_distribution_checks_held(release, data):
            held.add(release_key)
    return held


def _next_check(retries: int, last_checked: datetime.datetime) -> datetime.datetime:
    interval = _CHECK_INTERVAL_MIN if (retries < _FAST_CHECKS) else _CHECK_INTERVAL_MAX
    return last_checked + interval
