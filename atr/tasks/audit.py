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

import asyncio
import datetime

import atr.config as config
import atr.daylog as daylog
import atr.models.args as args
import atr.paths as paths
import atr.sealing as sealing
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.tasks.task as task


@checks.with_model(args.AuditSealArgs)
async def seal(task_args: args.AuditSealArgs) -> None:
    day = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).date()
    schedule = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(minute=5), datetime.UTC)
    await tasks.audit_seal(task_args.asf_uid, schedule=schedule)
    if await asyncio.to_thread(_seal_day, day - datetime.timedelta(days=1)):
        raise task.DeferredError(seconds=0)


def _seal_day(through: datetime.date) -> bool:
    daily = paths.get_audit_log_dir().path
    daylog.close(daily, through)
    directory = paths.get_sealing_dir()
    if (not directory.exists()) and (not config.is_production_mode()):
        return False
    with daylog.lock(directory):
        days = sorted(path for path in daily.glob("????-??-??.jsonl") if path.name[:10] <= through.isoformat())
        if days:
            first = datetime.date.fromisoformat(days[0].stem)
            expected = [(first + datetime.timedelta(days=i)).isoformat() for i in range((through - first).days + 1)]
            if [path.stem for path in days] != expected:
                raise ValueError("Missing closed audit day")
        seals = sorted(daily.glob("????-??-??.seal.json"))
        if seals != [path.with_suffix(".seal.json") for path in days[: len(seals)]]:
            raise ValueError("Audit seals must form a consecutive prefix of the daily logs")
        keys = directory / "keys"
        sealing.prepare(keys, daily / "root.der", seals[-1] if seals else None)
        if len(seals) == len(days):
            return False
        sealing.seal(keys, days[len(seals)])
        return len(seals) + 1 < len(days)
