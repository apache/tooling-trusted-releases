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
import json
import os
import pathlib
import subprocess
import tempfile
from typing import Final

import atr.cache as cache
import atr.config as config
import atr.constants as constants
import atr.construct as construct
import atr.db as db
import atr.models.sql as sql
import atr.sandbox as sandbox
import atr.storage as storage
import atr.tasks.checks.rat as rat

_KEY: Final = "failures"
_NAMESPACE: Final = "self_test"
_TIMEOUT: Final = 30


async def report(failures: dict[str, str]) -> None:
    state = json.dumps(failures, sort_keys=True) if failures else None
    async with db.session() as data:
        await data.begin_immediate()
        if state != (await data.ns_text_get(_NAMESPACE, _KEY)):
            await _report_email(data, failures, state)
        await data.commit()
    message = f"The self-test is failing: {summary(failures)}" if failures else None
    for asf_uid in sorted(await cache.admins_get_async()):
        async with storage.write_as_user_service(asf_uid) as waus:
            await waus.notifications_replace(
                message, link="/admin/system?tab=configuration", link_text="View configuration", is_admin=True
            )


async def run() -> dict[str, str]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_run), _TIMEOUT)
    except TimeoutError:
        return {"timeout": f"The self-test did not finish within {_TIMEOUT} seconds"}


def summary(failures: dict[str, str]) -> str:
    return "; ".join(f"{name}: {detail}" for name, detail in failures.items())


def _command(argv: list[str]) -> str | None:
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=_TIMEOUT)
    if result.returncode == 0:
        return None
    return f"{os.path.basename(argv[0])} exited {result.returncode}: {result.stderr.strip()}"


def _rat(scratch: pathlib.Path) -> str | None:
    tree = scratch / "rat"
    tree.mkdir()
    (tree / "probe.txt").write_text("ATR self-test\n")
    return rat.self_test(str(tree))


async def _report_email(data: db.Session, failures: dict[str, str], state: str | None) -> None:
    email = construct.self_test_notification(failures)
    data.add(
        sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.MESSAGE_SEND,
            task_args=email.as_task_args(),
            asf_uid=constants.SYSTEM_SERVICE_UID,
        )
    )
    if state is None:
        await data.ns_text_del(_NAMESPACE, _KEY, commit=False)
        return
    await data.ns_text_set(_NAMESPACE, _KEY, state, commit=False)


def _rsync(scratch: pathlib.Path) -> str | None:
    source = scratch / "source"
    destination = scratch / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "probe.txt").write_text("ATR self-test\n")
    argv = ["rsync", "-a", f"{source}/", f"{destination}/"]
    return _command(sandbox.rsync_command(argv, ro_paths=[str(source)], rw_paths=[str(destination)]))


def _run() -> dict[str, str]:
    failures: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="selftest_") as scratch:
        for name, probe in (("sandbox", _sandbox), ("rsync", _rsync), ("RAT", _rat)):
            try:
                failure = probe(pathlib.Path(scratch))
            except (OSError, subprocess.SubprocessError) as e:
                failure = str(e)
            if failure is not None:
                failures[name] = failure
    return failures


def _sandbox(scratch: pathlib.Path) -> str | None:
    if sandbox.landlock_setpriv() is None:
        return "Landlock is not available, so commands run unsandboxed" if config.is_production_mode() else None
    granted = scratch / "granted"
    granted.mkdir()
    (granted / "inside.txt").write_text("ATR self-test\n")
    (scratch / "outside.txt").write_text("ATR self-test\n")
    failure = _command(sandbox.command(["cat", str(granted / "inside.txt")], ro_paths=[str(granted)]))
    if failure is not None:
        return failure
    if _command(sandbox.command(["cat", str(scratch / "outside.txt")], ro_paths=[str(granted)])) is None:
        return "The sandbox allowed a read outside the granted paths"
    return None
