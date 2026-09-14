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

import dataclasses
import datetime
import math
import urllib.parse
from typing import Any

import packageurl


@dataclasses.dataclass(frozen=True)
class Score:
    criticality: float | None
    health: float | None
    health_inputs: int
    risk: float | None


def gitbox_mirror(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "gitbox.apache.org":
        return None
    if parsed.path.startswith("/repos/asf/"):
        name = parsed.path.removeprefix("/repos/asf/")
    elif parsed.path in ("/repos/asf", "/repos/asf.git"):
        names = urllib.parse.parse_qs(parsed.query).get("p", [])
        name = names[0] if (len(names) == 1) else ""
    else:
        return None
    name = name.removesuffix(".git")
    if (not name) or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in name):
        return None
    return f"github.com/apache/{name.lower()}"


def issues(data: Any) -> dict[str, Any]:
    if (not isinstance(data, dict)) or (not data):
        return {}
    maintainers = data.get("active_maintainers")
    return {"active_maintainers": len(maintainers) if isinstance(maintainers, list) else None}


def number(value: Any) -> float | None:
    if isinstance(value, bool) or (not isinstance(value, int | float | str)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def package_key(text: str) -> str | None:
    purl = package_url(text)
    return str(packageurl.PackageURL(purl.type, purl.namespace, purl.name)) if purl else None


def package_url(text: Any) -> packageurl.PackageURL | None:
    if not isinstance(text, str):
        return None
    try:
        return packageurl.PackageURL.from_string(text) if text else None
    except ValueError:
        return None


def repository(data: Any) -> dict[str, Any]:
    if (not isinstance(data, dict)) or (not data):
        return {}
    archived = data.get("archived")
    files = (data.get("metadata") or {}).get("files")
    names = ("security", "code_of_conduct", "contributing")
    governance = None
    if isinstance(files, dict) and all(name in files for name in names):
        governance = sum(bool(files[name]) for name in names)
    return {
        "archived": archived if isinstance(archived, bool) else None,
        "dds": number((data.get("commit_stats") or {}).get("dds")),
        "governance_files": governance,
    }


def score(
    *,
    active_maintainers: int | None,
    archived: bool | None,
    dds: float | None,
    governance_files: int | None,
    latest_release_at: str | None,
    rankings_average: float | None,
    now: datetime.datetime,
) -> Score:
    released = _timestamp(latest_release_at)
    recency = None if (released is None) else 1 - ((now - released).total_seconds() / (365 * 86400 * 3))
    parts = [
        recency,
        dds,
        None if (active_maintainers is None) else active_maintainers / 3,
        None if (governance_files is None) else governance_files / 3,
    ]
    observed = [max(0, min(1, part)) for part in parts if (part is not None)]
    health = sum(observed) / 4 if (len(observed) == 4) else None
    if archived is True:
        health = 0.05
    criticality = None
    if (rankings_average is not None) and (0 <= rankings_average <= 100):
        criticality = 1 - (math.log10(rankings_average + 1) / math.log10(101))
    risk = criticality * (1 - health) if (criticality is not None) and (health is not None) else None
    return Score(criticality, health, len(observed), risk)


def _timestamp(value: str | None) -> datetime.datetime | None:
    if value is None:
        return None
    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo else None
