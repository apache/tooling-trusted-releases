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
import pathlib
from typing import Any

import atr.config as config
import atr.log as log
import atr.models.safe as safe
import atr.paths as paths
import atr.util as util


async def write_release_log(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    *,
    until: str | None = None,
    required_action: str | None = None,
) -> int:
    source = pathlib.Path(config.get().STORAGE_AUDIT_LOG_FILE)
    events = await asyncio.to_thread(_matching_events, source, str(project_key), str(version_key), until)
    if required_action is not None:
        if not any(event.get("action") == required_action for event in events):
            raise ValueError(f"No {required_action} event was found for {project_key}-{version_key}")
    target = paths.audit_release_log_file(project_key, version_key)
    content = "".join(json.dumps(event, allow_nan=False) + "\n" for event in events)
    await util.atomic_write_file(target.path, content, mode=0o444)
    return len(events)


def _matches(event: dict[str, Any], project_key: str, version_key: str, release_key: str) -> bool:
    if event.get("release_key") == release_key:
        return True
    if event.get("project_key") != project_key:
        return False
    return version_key in (event.get("version"), event.get("version_key"))


def _matching_events(
    source: pathlib.Path, project_key: str, version_key: str, until: str | None
) -> list[dict[str, Any]]:
    release_key = f"{project_key}-{version_key}"
    events: list[dict[str, Any]] = []
    unparseable = 0
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                unparseable += 1
                continue
            event = record.get("event") if isinstance(record, dict) else None
            if not isinstance(event, dict):
                unparseable += 1
                continue
            if not _matches(event, project_key, version_key, release_key):
                continue
            if (until is not None) and (str(event.get("datetime", "")) > until):
                continue
            events.append(_normalise(event, project_key, version_key))
    if unparseable:
        log.warning(f"Skipped {unparseable} unparseable audit log lines while compiling {release_key}")
    events.sort(key=lambda entry: str(entry.get("datetime", "")))
    return events


def _normalise(event: dict[str, Any], project_key: str, version_key: str) -> dict[str, Any]:
    normalised = dict(event)
    normalised.pop("version", None)
    normalised["project_key"] = project_key
    normalised["version_key"] = version_key
    return normalised
