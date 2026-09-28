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
import stat
from typing import Any

import pytest

import atr.auditlog as auditlog
import atr.config as config
import atr.models.safe as safe


def file_mode(path: pathlib.Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def read_events(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_write_release_log(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = write_source(
        tmp_path,
        monkeypatch,
        [
            wrap(
                {
                    "datetime": "2026-08-10T12:00:02.000Z",
                    "action": "atr.storage.writers.release._start_release",
                    "asf_uid": "sbp",
                    "project_key": "proj",
                    "version": "1.0",
                    "description": "first\u0085second\u2028third\u2029fourth",
                }
            ),
            wrap(
                {
                    "datetime": "2026-08-10T12:00:01.000Z",
                    "action": "revision_create",
                    "asf_uid": "sbp",
                    "project_key": "proj",
                    "version_key": "1.0",
                    "revision_number": "00001",
                }
            ),
            wrap(
                {
                    "datetime": "2026-08-10T12:00:03.000Z",
                    "action": "atr.storage.writers.vote.FoundationCommitter.cast_trusted",
                    "release_key": "proj-1.0",
                    "voter_asf_uid": "sbp",
                }
            ),
            wrap({"datetime": "2026-08-10T12:00:04.000Z", "action": "other", "project_key": "proj", "version": "2.0"}),
            wrap({"datetime": "2026-08-10T12:00:05.000Z", "action": "policy_edit", "project_key": "proj"}),
            "not json at all",
            "",
        ],
    )
    unrelated = {"datetime": "2026-08-10T12:00:01.000Z", "project_key": "proj", "version_key": "1.0"}
    (source.parent / "2026-08-11.jsonl").write_text(
        wrap(unrelated, "atr.auth") + "\n" + wrap(unrelated, "atr.keys.submitted") + "\n", encoding="utf-8"
    )

    count = await auditlog.write_release_log(safe.ProjectKey("proj"), safe.VersionKey("1.0"))

    target = tmp_path / "audit" / "releases" / "proj" / "1.0.jsonl"
    events = read_events(target)
    assert count == 3
    assert [event["datetime"] for event in events] == [
        "2026-08-10T12:00:01.000Z",
        "2026-08-10T12:00:02.000Z",
        "2026-08-10T12:00:03.000Z",
    ]
    assert all(event["project_key"] == "proj" for event in events)
    assert all(event["version_key"] == "1.0" for event in events)
    assert all("version" not in event for event in events)
    assert events[2]["release_key"] == "proj-1.0"
    assert events[1]["description"] == "first\u0085second\u2028third\u2029fourth"
    assert file_mode(target) == 0o444


async def test_write_release_log_appends_missing_marker(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stray = {"datetime": "2026-08-10T12:00:01.000Z", "action": "announce", "project_key": "proj", "version_key": "1.0"}
    source = write_source(tmp_path, monkeypatch, [wrap(stray)])
    marker = {
        "datetime": "2026-08-10T12:00:02.000Z",
        "action": "announce",
        "project_key": "proj",
        "version_key": "1.0",
        "email_to": "announce@proj.apache.org",
    }

    before = datetime.datetime.now(datetime.UTC)
    count = await auditlog.write_release_log(
        safe.ProjectKey("proj"), safe.VersionKey("1.0"), until="2026-08-10T12:00:02.000Z", marker=marker
    )

    target = tmp_path / "audit" / "releases" / "proj" / "1.0.jsonl"
    events = read_events(target)
    assert count == 2
    assert [event["action"] for event in events] == ["announce", "announce"]
    assert events[1]["email_to"] == "announce@proj.apache.org"
    assert len(read_events(source)) == 1
    records = [entry for path in source.parent.glob("????-??-??.jsonl") for entry in read_events(path)]
    written = next(entry for entry in records if entry["event"] == marker)
    assert before <= datetime.datetime.fromisoformat(written["timestamp"]) <= datetime.datetime.now(datetime.UTC)
    assert len(written["timestamp"]) == 27
    assert written["timestamp"].endswith("Z")
    assert (
        await auditlog.write_release_log(
            safe.ProjectKey("proj"), safe.VersionKey("1.0"), until="2026-08-10T12:00:02.000Z", marker=marker
        )
        == 2
    )
    assert sum(len(read_events(path)) for path in source.parent.glob("????-??-??.jsonl")) == 2


async def test_write_release_log_boundary_and_marker(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    announced = {
        "datetime": "2026-08-10T12:00:02.000Z",
        "action": "announce",
        "project_key": "proj",
        "version_key": "1.0",
    }
    archived = {"datetime": "2026-08-10T12:00:03.000Z", "action": "archive", "project_key": "proj", "version": "1.0"}
    created = {"datetime": "2026-08-10T12:00:01.000Z", "action": "create", "project_key": "proj", "version": "1.0"}
    source = write_source(tmp_path, monkeypatch, [wrap(created), wrap(announced), wrap(archived)])
    project_key = safe.ProjectKey("proj")
    version_key = safe.VersionKey("1.0")
    until = "2026-08-10T12:00:02.000Z"

    count = await auditlog.write_release_log(project_key, version_key, until=until, marker=announced)

    target = tmp_path / "audit" / "releases" / "proj" / "1.0.jsonl"
    assert count == 2
    assert [event["action"] for event in read_events(target)] == ["create", "announce"]
    assert len(source.read_text(encoding="utf-8").splitlines()) == 3


async def test_write_release_log_replaces_readonly(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {"datetime": "2026-08-10T12:00:01.000Z", "action": "a", "project_key": "proj", "version_key": "1.0"}
    later = {"datetime": "2026-08-10T12:00:02.000Z", "action": "b", "project_key": "proj", "version_key": "1.0"}
    source = write_source(tmp_path, monkeypatch, [wrap(entry)])

    assert await auditlog.write_release_log(safe.ProjectKey("proj"), safe.VersionKey("1.0")) == 1
    source.write_text(wrap(entry) + "\n" + wrap(later) + "\n", encoding="utf-8")
    assert await auditlog.write_release_log(safe.ProjectKey("proj"), safe.VersionKey("1.0")) == 2

    target = tmp_path / "audit" / "releases" / "proj" / "1.0.jsonl"
    assert [event["action"] for event in read_events(target)] == ["a", "b"]
    assert file_mode(target) == 0o444


def wrap(event: dict[str, Any], logger: str = "atr.storage.audit") -> str:
    return json.dumps(
        {"event": event, "level": "info", "logger": logger, "timestamp": "2026-08-10T12:00:00.000000Z"},
        ensure_ascii=False,
    )


def write_source(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> pathlib.Path:
    source = tmp_path / "audit" / "daily" / "2026-08-10.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(config.get(), "STATE_DIR", str(tmp_path), raising=False)
    return source
