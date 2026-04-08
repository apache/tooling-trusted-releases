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
import atr.db as db
import atr.log as log
import atr.models.args as args
import atr.models.results as results
import atr.shared.distribution as distribution
import atr.storage as storage
import atr.tasks as tasks
import atr.tasks.checks as checks

_RETRY_LIMIT = 5
_BATCH_SIZE = 20


@checks.with_model(args.DistributionStatusCheckArgs)
async def status_check(
    task_args: args.DistributionStatusCheckArgs, *, task_id: int | None = None
) -> results.Results | None:
    log.info("Checking pending recorded distributions")
    dists = []
    async with db.session() as data:
        dists = await data.distribution(
            pending=True, _with_release=True, _with_release_project=True, _limit=_BATCH_SIZE
        ).all()
    for dist in dists:
        name = f"{dist.platform} {dist.owner_namespace} {dist.package} {dist.version}"
        dd = dist.distribution_data()
        if not dist.created_by:
            log.warning(f"Distribution {name} has no creator, skipping")
            continue
        if not dist.release.project.committee_key:
            log.warning(f"Distribution {name} has no committee, skipping")
            continue
        try:
            async with storage.write_as_committee_member(dist.release.project.committee_key, dist.created_by) as w:
                if dist.retries >= _RETRY_LIMIT:
                    await w.distributions.delete_distribution(
                        dist.safe_release_key, dist.platform, dist.owner_namespace, dist.package, dist.version
                    )
                    log.error(f"Distribution {name} failed {_RETRY_LIMIT} times, skipping")
                    continue
                log.warning(f"Retrying distribution {name}")
                await w.distributions.record_from_data(
                    dist.safe_release_key,
                    dist.staging,
                    dd,
                    allow_retries=True,
                )
        except (distribution.DistributionError, storage.AccessError) as e:
            msg = f"Failed to record distribution: {e}"
            log.error(msg)
    await tasks.schedule_next(task_args.asf_uid, task_args.next_schedule_seconds, tasks.distribution_status_check)
    return results.DistributionStatusCheck(
        kind="distribution_status",
    )
