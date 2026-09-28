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

import collections.abc as abc
import datetime
import json
import pathlib
from typing import Any, Final

import rfc8785

import atr.sealing as sealing

FIELDS: Final = frozenset({"event", "level", "logger", "timestamp"})


def backfill(source_dir: pathlib.Path, target_dir: pathlib.Path, through: datetime.date) -> None:
    records = []
    for name in ("auth-audit.log", "keys-submitted.log", "storage-audit.log"):
        records.extend(_read(source_dir / name))
    if not records:
        raise ValueError("No historical audit entries establish a starting day")
    first = datetime.date.fromisoformat(min(record["timestamp"] for record in records)[:10])
    days = build(records, first, through)
    for day in days:
        target = target_dir / f"{day}.jsonl"
        if target.exists(follow_symlinks=False):
            raise FileExistsError(target)
    for day, content in days.items():
        sealing.publish(target_dir / f"{day}.jsonl", content)


def build(records: abc.Iterable[Any], first: datetime.date, last: datetime.date) -> dict[str, bytes]:
    if first > last:
        raise ValueError("The first day must not follow the last day")
    entries = sorted((_entry(record) for record in records), key=lambda entry: entry[0])
    days: dict[str, list[bytes]] = {
        (first + datetime.timedelta(days=offset)).isoformat(): [] for offset in range((last - first).days + 1)
    }
    for (timestamp, _, _, _), line in entries:
        day = timestamp[:10]
        if day not in days:
            raise ValueError(f"Audit entry outside the requested day range: {timestamp}")
        days[day].append(line)
    return {day: b"".join(lines) for day, lines in days.items()}


def _entry(record: Any) -> tuple[tuple[str, str, str, bytes], bytes]:
    if (not isinstance(record, dict)) or (frozenset(record) != FIELDS):
        raise ValueError("Expected event, level, logger and timestamp fields")
    if not all(isinstance(record[key], str) for key in ("timestamp", "logger", "level")):
        raise ValueError("Timestamp, logger and level must be strings")
    timestamp = record["timestamp"]
    if _timestamp(timestamp) != timestamp:
        raise ValueError("Expected a UTC timestamp with six fractional digits and Z")
    key = (timestamp, record["logger"], record["level"], rfc8785.dumps(record["event"]))
    return key, rfc8785.dumps(record) + b"\n"


def _historical(record: Any, storage: bool) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("Expected a JSON object")
    if storage and (not FIELDS.intersection(record)):
        if ("datetime" not in record) or (not isinstance(record.get("action"), str)):
            raise ValueError("Expected a legacy storage event")
        timestamp = _timestamp(record["datetime"])
        if record["datetime"] != timestamp[:-4] + "Z":
            raise ValueError("Expected a legacy UTC datetime with three fractional digits and Z")
        return {"event": record, "level": "info", "logger": "atr.storage.audit", "timestamp": timestamp}
    timestamp = _timestamp(record.get("timestamp"))
    if record["timestamp"] not in (timestamp, timestamp[:19] + "Z"):
        raise ValueError("Expected a historical UTC timestamp with zero or six fractional digits and Z")
    return record | {"timestamp": timestamp}


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON member: {key}")
        result[key] = value
    return result


def _read(source: pathlib.Path) -> list[dict[str, Any]]:
    records = []
    with source.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            try:
                if not line.endswith(b"\n"):
                    raise ValueError("Unterminated JSONL line")
                record = json.loads(line.decode("utf-8"), object_pairs_hook=_object)
                record = _historical(record, source.name == "storage-audit.log")
            except ValueError as error:
                raise ValueError(f"{source}:{number}: {error}") from error
            records.append(record)
    return records


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a UTC timestamp")
    instant = datetime.datetime.fromisoformat(value)
    if instant.tzinfo != datetime.UTC:
        raise ValueError("Expected a UTC timestamp")
    return instant.isoformat(timespec="microseconds").replace("+00:00", "Z")
