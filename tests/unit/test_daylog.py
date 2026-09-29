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
import fcntl
import functools
import json
import logging
import os
import pathlib
import stat
import types
import unittest.mock as mock
from typing import Any

import pytest
import rfc8785

import atr.daylog as daylog
import atr.log as log
import atr.loggers as loggers


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    value = mock.Mock(wraps=datetime.datetime)
    monkeypatch.setattr(daylog, "datetime", types.SimpleNamespace(**(vars(datetime) | {"datetime": value})))
    return value.now


def locked_now(directory: pathlib.Path, instant: datetime.datetime, zone: datetime.tzinfo) -> datetime.datetime:
    assert zone == datetime.UTC
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
    return instant


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


def test_append(clock: mock.Mock, tmp_path: pathlib.Path) -> None:
    instant = datetime.datetime(2026, 9, 29, 0, 0, 0, 1, tzinfo=datetime.UTC)
    clock.side_effect = functools.partial(locked_now, tmp_path, instant)
    event = {"datetime": "1999-01-01T00:00:00.000Z", "timestamp": "caller supplied"}
    daylog.append(tmp_path, "atr.auth", "info", event)
    daylog.append(tmp_path, "atr.keys.submitted", "warning", "string event")
    expected = [
        record(event, "2026-09-29T00:00:00.000001Z"),
        record("string event", "2026-09-29T00:00:00.000001Z", "atr.keys.submitted", "warning"),
    ]
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == {
        "2026-09-29.jsonl": b"".join(rfc8785.dumps(entry) + b"\n" for entry in expected)
    }


@pytest.mark.parametrize("failure", ["closed", "sealed", "partial"])
def test_append_refuses(failure: str, clock: mock.Mock, tmp_path: pathlib.Path) -> None:
    clock.return_value = datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)
    match failure:
        case "closed":
            (tmp_path / "closed").write_bytes(b"2026-09-29\n")
        case "sealed":
            (tmp_path / "2026-09-29.seal.json").write_bytes(b"sealed")
        case "partial":
            (tmp_path / "2026-09-29.jsonl").write_bytes(b'{"unfinished":')
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises((ValueError, FileExistsError)):
        daylog.append(tmp_path, "atr.auth", "info", {})
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_audit_logger(clock: mock.Mock, sources: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = sources / "daily"
    names = ("atr.auth", "atr.keys.submitted", "atr.storage.audit")
    for name in names:
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", [])
        monkeypatch.setattr(logger, "level", logger.level)
        monkeypatch.setattr(logger, "propagate", logger.propagate)
    clock.return_value = datetime.datetime(2026, 9, 28, 23, 59, 59, 999999, tzinfo=datetime.UTC)
    listener = loggers.setup_audit_logger(directory)
    errors = mock.Mock()
    monkeypatch.setattr(listener.handlers[0], "handleError", errors)
    try:
        clock.reset_mock()
        with daylog.lock(directory):
            for name in names:
                logging.getLogger(name).warning('{"datetime":"1999-01-01T00:00:00.000Z"}', extra={"timestamp": "old"})
            assert not clock.called
            clock.return_value = datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)
        log.audit_flush()
        content = (directory / "2026-09-29.jsonl").read_bytes()
        entries = [json.loads(line) for line in content.splitlines()]
        assert [entry["logger"] for entry in entries] == list(names)
        assert all(entry["timestamp"] == "2026-09-29T00:00:00.000000Z" for entry in entries)
        assert all((frozenset(entry) == daylog.FIELDS) and (entry["level"] == "warning") for entry in entries)
        assert (directory / "2026-09-28.jsonl").read_bytes() == b""
        logging.getLogger("atr.auth").info(json.dumps({"unsafe": 2**53}))
        log.audit_flush()
        errors.assert_called_once()
        assert (directory / "2026-09-29.jsonl").read_bytes() == content
        logging.getLogger("atr.auth").info("{not json")
        log.audit_flush()
        assert json.loads((directory / "2026-09-29.jsonl").read_bytes().splitlines()[-1])["event"] == "{not json"
    finally:
        listener.stop()


def test_audit_logger_rejects_invalid_history(tmp_path: pathlib.Path) -> None:
    directory = tmp_path / "daily"
    for name in ("auth-audit.log", "keys-submitted.log", "storage-audit.log"):
        source = tmp_path / name
        source.write_bytes(b"historical\n")
        with pytest.raises(ValueError, match=rf"{name}:1:"):
            loggers.setup_audit_logger(directory)
        assert not directory.exists()
        assert source.read_bytes() == b"historical\n"
        assert all(path.is_file() for path in tmp_path.iterdir())
        source.write_bytes(b"")


@pytest.mark.parametrize("storage_event", [{"datetime": "2025-08-05T12:34:56.789Z"}, "01234567Z x"])
def test_backfill(sources: pathlib.Path, tmp_path: pathlib.Path, storage_event: Any) -> None:
    flat = {"datetime": "2025-08-05T12:34:56.789Z", "action": "old", "project_name": "example"}
    auth = record("historical string", timestamp="2025-08-08T00:00:00Z")
    storage = record(storage_event, "2025-08-08T00:00:00.000001Z", "atr.storage.audit")
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


@pytest.mark.parametrize("missing", [False, True])
def test_backfill_empty_history(sources: pathlib.Path, tmp_path: pathlib.Path, missing: bool) -> None:
    if missing:
        for source in sources.iterdir():
            source.unlink()
    target = tmp_path / "days"
    target.mkdir()
    daylog.backfill(sources, target, datetime.date(2026, 9, 28))
    assert {path.name: path.read_bytes() for path in target.iterdir()} == {"2026-09-28.jsonl": b""}


@pytest.mark.parametrize("suffix", ["Z", "+00:00"])
@pytest.mark.parametrize("prefix", ["", "2026-09-29T00:30:00Z "])
def test_backfill_legacy_prefix(sources: pathlib.Path, tmp_path: pathlib.Path, suffix: str, prefix: str) -> None:
    event = {"datetime": f"2026-09-28T23:30:00.123456{suffix}", "action": "example"}
    original = (prefix + json.dumps(event) + "\n").encode()
    source = sources / "storage-audit.log"
    source.write_bytes(original)
    target = tmp_path / "days"
    target.mkdir()
    daylog.backfill(sources, target, datetime.date(2026, 9, 29))
    expected = record(event, "2026-09-28T23:30:00.123456Z", "atr.storage.audit")
    assert (target / "2026-09-28.jsonl").read_bytes() == rfc8785.dumps(expected) + b"\n"
    assert (target / "2026-09-29.jsonl").read_bytes() == b""
    assert source.read_bytes() == original


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
        ("storage-audit.log", b'{"datetime":"2025-08-05T12:34:56.789000","action":"old"}\n'),
        ("storage-audit.log", b'{"datetime":"2025-08-05T12:34:56.789000+01:00","action":"old"}\n'),
        ("auth-audit.log", b"2026-09-28T00:00:00Z " + json.dumps(record({})).encode() + b"\n"),
        ("storage-audit.log", b"2026-99-28T00:00:00Z " + json.dumps(record({})).encode() + b"\n"),
        ("storage-audit.log", b"2026-09-28T00:00:00Z not json\n"),
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


def test_close(clock: mock.Mock, tmp_path: pathlib.Path) -> None:
    clock.return_value = datetime.datetime(2026, 10, 2, tzinfo=datetime.UTC)
    first, through = datetime.date(2026, 9, 28), datetime.date(2026, 10, 1)
    entries = [record({"n": 2}), record({"n": 10}), record({"n": 2})]
    later = record({}, "2026-09-30T00:00:00.000000Z")
    (tmp_path / "2026-09-28.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    (tmp_path / "2026-09-30.jsonl").write_text(json.dumps(later) + "\n")
    expected = {f"{day}.jsonl": content for day, content in daylog.build([*entries, later], first, through).items()}
    expected["closed"] = b"2026-10-01\n"

    daylog.close(tmp_path, through)

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == expected
    inodes = {path.name: path.stat().st_ino for path in tmp_path.iterdir()}
    daylog.close(tmp_path, through)
    assert {path.name: path.stat().st_ino for path in tmp_path.iterdir()} == inodes
    with pytest.raises(ValueError, match="Only completed UTC days"):
        daylog.close(tmp_path, through + datetime.timedelta(days=1))
    clock.return_value = datetime.datetime(2026, 10, 4, tzinfo=datetime.UTC)
    daylog.close(tmp_path, datetime.date(2026, 10, 3))
    expected.update({"2026-10-02.jsonl": b"", "2026-10-03.jsonl": b"", "closed": b"2026-10-03\n"})
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == expected


@pytest.mark.parametrize("completed_replacements", [0, 1])
def test_close_interrupted(
    clock: mock.Mock, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, completed_replacements: int
) -> None:
    clock.return_value = datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)
    path = tmp_path / "2026-09-28.jsonl"
    original = json.dumps(record({})).encode() + b"\n"
    path.write_bytes(original)
    canonical = rfc8785.dumps(record({})) + b"\n"
    with monkeypatch.context() as failure:
        replace = mock.Mock(
            wraps=os.replace, side_effect=[mock.DEFAULT] * completed_replacements + [OSError("interrupted")]
        )
        failure.setattr(daylog.os, "replace", replace)
        with pytest.raises(OSError, match="interrupted"):
            daylog.close(tmp_path, datetime.date(2026, 9, 28))
    assert {entry.name for entry in tmp_path.iterdir()} == {path.name}
    assert path.read_bytes() == (canonical if completed_replacements else original)
    daylog.close(tmp_path, datetime.date(2026, 9, 28))
    assert path.read_bytes() == canonical
    assert (tmp_path / "closed").read_bytes() == b"2026-09-28\n"


def test_close_refuses_invalid_or_sealed(clock: mock.Mock, tmp_path: pathlib.Path) -> None:
    clock.return_value = datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)
    path = tmp_path / "2026-09-28.jsonl"
    path.write_bytes(b"not json\n")
    with pytest.raises(ValueError, match=r"2026-09-28\.jsonl:1:"):
        daylog.close(tmp_path, datetime.date(2026, 9, 28))
    assert path.read_bytes() == b"not json\n"
    assert not (tmp_path / "closed").exists()
    path.with_suffix(".seal.json").write_bytes(b"sealed")
    with pytest.raises(FileExistsError):
        daylog.close(tmp_path, datetime.date(2026, 9, 28))
    assert path.read_bytes() == b"not json\n"


@pytest.mark.parametrize("empty_daily", [False, True])
def test_cutover(clock: mock.Mock, sources: pathlib.Path, empty_daily: bool) -> None:
    day = datetime.date(2026, 9, 28)
    historical = record({"source": "historical"}, "2026-09-28T00:00:00.000000Z")
    source = sources / "auth-audit.log"
    original = json.dumps(historical).encode() + b"\n"
    source.write_bytes(original)
    (sources / "keys-submitted.log").unlink()
    (sources / "storage-audit.log").unlink()
    target = sources / "daily"
    if empty_daily:
        target.mkdir()
    clock.return_value = datetime.datetime(2026, 9, 28, 12, tzinfo=datetime.UTC)
    daylog.initialise(target)
    daylog.append(target, "atr.storage.audit", "info", {"source": "live"})
    clock.return_value = datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)
    daylog.close(target, day)
    live = record({"source": "live"}, "2026-09-28T12:00:00.000000Z", "atr.storage.audit")
    assert (target / f"{day}.jsonl").read_bytes() == daylog.build([historical, live], day, day)[str(day)]
    assert source.read_bytes() == original


def test_initialise(clock: mock.Mock, tmp_path: pathlib.Path) -> None:
    clock.return_value = datetime.datetime(2026, 9, 28, tzinfo=datetime.UTC)
    directory = tmp_path / "daily"
    daylog.initialise(directory)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == {"2026-09-28.jsonl": b""}
    clock.return_value = datetime.datetime(2026, 9, 30, tzinfo=datetime.UTC)
    daylog.initialise(directory)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == {"2026-09-28.jsonl": b""}
    daylog.close(directory, datetime.date(2026, 9, 29))
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == {
        "2026-09-28.jsonl": b"",
        "2026-09-29.jsonl": b"",
        "closed": b"2026-09-29\n",
    }


@pytest.mark.parametrize("failure", ["publish", "rename"])
def test_initialise_interrupted(
    clock: mock.Mock, sources: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    clock.side_effect = functools.partial(locked_now, sources, datetime.datetime(2026, 9, 30, tzinfo=datetime.UTC))
    (sources / "auth-audit.log").write_bytes(json.dumps(record({})).encode() + b"\n")
    originals = {path.name: path.read_bytes() for path in sources.iterdir()}
    directory = sources / "daily"
    with monkeypatch.context() as interrupted:
        if failure == "publish":
            publish = mock.Mock(wraps=daylog.sealing.publish, side_effect=[mock.DEFAULT, OSError("interrupted")])
            interrupted.setattr(daylog.sealing, "publish", publish)
        else:
            interrupted.setattr(daylog.os, "rename", mock.Mock(side_effect=OSError("interrupted")))
        with pytest.raises(OSError, match="interrupted"):
            daylog.initialise(directory)
    assert not directory.exists()
    assert {path.name: path.read_bytes() for path in sources.iterdir()} == originals

    daylog.initialise(directory)

    expected = {
        "2026-09-28.jsonl": rfc8785.dumps(record({})) + b"\n",
        "2026-09-29.jsonl": b"",
        "2026-09-30.jsonl": b"",
    }
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == expected
    assert {name: (sources / name).read_bytes() for name in originals} == originals
    inodes = {path.name: path.stat().st_ino for path in directory.iterdir()}
    daylog.initialise(directory)
    assert {path.name: path.stat().st_ino for path in directory.iterdir()} == inodes


@pytest.mark.parametrize("mask", [0o000, 0o077])
def test_permissions(clock: mock.Mock, tmp_path: pathlib.Path, mask: int) -> None:
    group = next((group for group in os.getgroups() if group != os.getgid()), os.getgid())
    os.chown(tmp_path, -1, group)
    tmp_path.chmod(0o2750)
    directory = tmp_path / "daily"
    previous = os.umask(mask)
    try:
        clock.return_value = datetime.datetime(2026, 9, 28, tzinfo=datetime.UTC)
        daylog.initialise(directory)
        clock.return_value = datetime.datetime(2026, 9, 29, tzinfo=datetime.UTC)
        daylog.append(directory, "atr.auth", "info", {})
        assert stat.S_IMODE(directory.stat().st_mode) == 0o2750
        assert directory.stat().st_gid == group
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o640 for path in directory.iterdir())
        clock.return_value = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
        daylog.close(directory, datetime.date(2026, 9, 30))
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o640 for path in directory.iterdir())
        assert all(path.stat().st_gid == group for path in directory.iterdir())
    finally:
        os.umask(previous)
