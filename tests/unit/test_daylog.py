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
import json
import pathlib
from typing import Any

import pytest
import rfc8785

import atr.daylog as daylog


def record(
    event: Any,
    timestamp: str = "2026-09-28T00:00:00.000001Z",
    logger: str = "atr.auth",
    level: str = "info",
) -> dict[str, Any]:
    return {"event": event, "level": level, "logger": logger, "timestamp": timestamp}


@pytest.fixture
def sources(tmp_path: pathlib.Path) -> pathlib.Path:
    source = tmp_path / "sources"
    source.mkdir()
    for name in ("auth-audit.log", "keys-submitted.log", "storage-audit.log"):
        (source / name).write_bytes(b"")
    return source


def test_backfill(sources: pathlib.Path, tmp_path: pathlib.Path) -> None:
    flat = {"datetime": "2025-08-05T12:34:56.789Z", "action": "old", "project_name": "example"}
    auth = record("historical string", timestamp="2025-08-08T00:00:00Z")
    storage = record({"datetime": "2025-08-05T12:34:56.789Z"}, "2025-08-08T00:00:00.000001Z", "atr.storage.audit")
    (sources / "auth-audit.log").write_bytes(json.dumps(auth).encode() + b"\n")
    (sources / "storage-audit.log").write_bytes(
        json.dumps(flat).encode() + b"\n" + json.dumps(storage).encode() + b"\n"
    )
    originals = {path: path.read_bytes() for path in sources.iterdir()}
    target = tmp_path / "days"
    target.mkdir()
    through = datetime.date(2025, 8, 10)
    conflict = target / "2025-08-07.jsonl"
    conflict.write_bytes(b"existing\n")
    with pytest.raises(ValueError, match="outside the requested day range"):
        daylog.backfill(sources, target, datetime.date(2025, 8, 5))
    with pytest.raises(FileExistsError):
        daylog.backfill(sources, target, through)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == {conflict.name: b"existing\n"}
    conflict.unlink()

    daylog.backfill(sources, target, through)

    expected = {
        "2025-08-05.jsonl": rfc8785.dumps(record(flat, "2025-08-05T12:34:56.789000Z", "atr.storage.audit")) + b"\n",
        "2025-08-06.jsonl": b"",
        "2025-08-07.jsonl": b"",
        "2025-08-08.jsonl": rfc8785.dumps(auth | {"timestamp": "2025-08-08T00:00:00.000000Z"})
        + b"\n"
        + rfc8785.dumps(storage)
        + b"\n",
        "2025-08-09.jsonl": b"",
        "2025-08-10.jsonl": b"",
    }
    assert {path.name: path.read_bytes() for path in target.iterdir()} == expected
    assert {path: path.read_bytes() for path in sources.iterdir()} == originals
    with pytest.raises(FileExistsError):
        daylog.backfill(sources, target, through)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == expected


def test_backfill_rejects_empty_history(sources: pathlib.Path, tmp_path: pathlib.Path) -> None:
    target = tmp_path / "days"
    target.mkdir()
    with pytest.raises(ValueError, match="No historical audit entries"):
        daylog.backfill(sources, target, datetime.date(2026, 9, 28))
    assert not list(target.iterdir())


@pytest.mark.parametrize(
    ("name", "line"),
    [
        ("auth-audit.log", json.dumps(record({})).encode()),
        ("auth-audit.log", b"\n"),
        ("auth-audit.log", b"\xff\n"),
        ("auth-audit.log", json.dumps(record({})).replace('"event": {}', '"event": {"x": 1, "x": 2}').encode() + b"\n"),
        ("auth-audit.log", json.dumps(record({}, timestamp="2026-09-28T00:00:00.000Z")).encode() + b"\n"),
        ("auth-audit.log", b'{"datetime":"2025-08-05T12:34:56.789Z","action":"old"}\n'),
        (
            "storage-audit.log",
            b'{"event":{"datetime":"2026-09-28T00:00:00.000Z"},"logger":"atr.storage.audit","level":"info"}\n',
        ),
        ("storage-audit.log", b'{"datetime":"2025-08-05T12:34:56.789Z"}\n'),
        ("storage-audit.log", b'{"datetime":"2025-08-05T12:34:56.789000Z","action":"old"}\n'),
    ],
)
def test_backfill_rejects_invalid_lines(sources: pathlib.Path, tmp_path: pathlib.Path, name: str, line: bytes) -> None:
    (sources / name).write_bytes(json.dumps(record({})).encode() + b"\n" + line)
    target = tmp_path / "days"
    target.mkdir()
    with pytest.raises(ValueError, match=rf"{name}:2:"):
        daylog.backfill(sources, target, datetime.date(2026, 9, 28))
    assert not list(target.iterdir())


def test_build() -> None:
    earliest = record({}, timestamp="2026-09-28T00:00:00.000000Z", logger="atr.storage.audit")
    debug = record({}, level="debug")
    string = record('{"keep":"as a string"}')
    ten = record({"z": 0, "n": 10})
    two = record({"n": 2, "z": 0})
    keys = record({}, logger="atr.keys.submitted", level="debug")
    storage = record({}, logger="atr.storage.audit")
    next_day = record(None, timestamp="2026-09-29T00:00:00.000000Z")
    ordered = [earliest, debug, string, ten, two, two, keys, storage]
    inputs = [storage, next_day, two, debug, earliest, keys, two, string, ten]
    expected = {
        "2026-09-27": b"",
        "2026-09-28": b"".join(rfc8785.dumps(item) + b"\n" for item in ordered),
        "2026-09-29": rfc8785.dumps(next_day) + b"\n",
        "2026-09-30": b"",
        "2026-10-01": b"",
    }
    first, last = datetime.date(2026, 9, 27), datetime.date(2026, 10, 1)
    assert daylog.build(inputs, first, last) == expected
    assert daylog.build(reversed(inputs), first, last) == expected
    assert daylog.build([], first, last) == dict.fromkeys(expected, b"")
    assert daylog.build([], first, first) == {"2026-09-27": b""}


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"event": {}},
        record({}) | {"extra": True},
        record({}) | {"level": 1},
        record({}) | {"logger": None},
        record({}, timestamp="2026-09-28T00:00:00Z"),
        record({}, timestamp="2026-09-28T00:00:00.000000+00:00"),
        record({}, timestamp="2026-09-28T00:00:00.000000"),
        record({}, timestamp="2026-09-28T00:00:00.0000001Z"),
        record({}, timestamp="2026-09-27T00:00:00.000000Z"),
        record(float("nan")),
        record(float("inf")),
        record(2**53),
        record("\ud800"),
    ],
)
def test_build_rejects_invalid_records(value: Any) -> None:
    with pytest.raises(ValueError):
        daylog.build([value], datetime.date(2026, 9, 28), datetime.date(2026, 9, 29))


def test_build_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="first day must not follow"):
        daylog.build([], datetime.date(2026, 9, 29), datetime.date(2026, 9, 28))
