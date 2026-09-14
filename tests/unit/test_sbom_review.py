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
import json
import unittest.mock as mock

import aiohttp
import pytest

import atr.attestable as attestable
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sbom.observations as observations
import atr.sbom.streaming as streaming
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.tasks.checks.sbom as sbom
import atr.tasks.task as task
import tests.unit.recorders as recorders

_LICENSED = (
    b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":'
    b'[{"purl":"pkg:npm/a@1","licenses":[{"license":{"id":"GPL-3.0-only"}}]}]}'
)


@pytest.fixture
def review_input(tmp_path):
    path = tmp_path / "sbom.cdx.json"
    recorder = recorders.RecorderStub(safe.StatePath(path), "atr.tasks.checks.sbom.review", sbom.REVIEW_VERSION)
    args = checks.FunctionArguments(
        recorders.get_recorder(recorder),
        "tester",
        safe.ProjectKey("test"),
        safe.VersionKey("1.0"),
        safe.RevisionNumber("00001"),
        safe.RelPath(path.name),
        {},
    )
    return path, recorder, args


@pytest.fixture
def review_release(monkeypatch):
    release = sql.Release(
        key="test-1.0",
        project_key="test",
        version="1.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
    )
    release.project = sql.Project(key="test", name="Apache Test")
    session = mock.AsyncMock()
    session.__aenter__.return_value = session
    session.release_file_hash_at.return_value = "blake3:content"
    monkeypatch.setattr(db, "session", mock.Mock(return_value=session))
    monkeypatch.setattr(attestable, "load", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(attestable, "write_checks_data", mock.AsyncMock())
    return release, session


async def test_review_aggregates_risk_with_unknown_coverage(review_input, monkeypatch) -> None:
    path, recorder, args = review_input
    path.write_bytes(_document([f"pkg:npm/{name}@{version}" for name in "hgfedcba" for version in ("1", "2")]))
    packages = {f"pkg:npm/{name}": {"archived": True, "rankings_average": 1.0} for name in "abcd"}
    packages["pkg:npm/e"] = {"archived": True, "rankings_average": 0.0, "inferred_mirror": "github.com/apache/e"}
    packages["pkg:npm/f"] = {"archived": True, "rankings_average": 100.0}
    packages["pkg:npm/g"] = {"rankings_average": 0.0}
    collect = mock.AsyncMock(return_value=packages)
    monkeypatch.setattr(observations, "collect", collect)
    assert await sbom.review(args) is None
    assert len(recorder.messages) == 1
    status, message, data = recorder.messages[0]
    assert status == sql.CheckResultStatus.CONCERN
    assert message == "Packages npm/e, npm/a, npm/b (and 2 more) have maintenance risk above 0.6"
    assert (data["packages"], data["scored"], data["unknown"], data["risk_count"]) == (8, 6, 2, 5)
    assert data["risks"][0]["source_purls"] == ["pkg:npm/e@1", "pkg:npm/e@2"]
    assert data["risks"][0]["inferred_mirror"] == "github.com/apache/e"
    assert data["risks"][0]["risk"] == 0.95
    assert data["risks_truncated"] is False
    assert "no deps.dev source veto" in data["attribution"]
    json.dumps(data)
    collect.assert_awaited_once_with([f"pkg:npm/{name}" for name in "abcdefgh"])


async def test_review_aggregates_x_and_unknown_licenses_separately_from_risk(review_input, monkeypatch) -> None:
    path, recorder, args = review_input
    document = json.loads(_LICENSED)
    document["components"] += [
        {"name": "unknown", "licenses": [{"license": {"name": "Custom licence"}}]},
        {"name": "x", "version": "2", "licenses": [{"expression": "MIT AND GPL-3.0-only"}]},
        {"name": "allowed", "licenses": [{"expression": "MIT OR GPL-3.0-only"}]},
        {"name": "category-b", "licenses": [{"license": {"id": "MPL-2.0"}}]},
        {"name": "missing"},
        document["components"][0],
    ]
    path.write_text(json.dumps(document))
    monkeypatch.setattr(
        observations, "collect", mock.AsyncMock(return_value={"pkg:npm/a": {"archived": True, "rankings_average": 0.0}})
    )
    assert await sbom.review(args) is None
    assert len(recorder.messages) == 2
    assert all(message[0] == sql.CheckResultStatus.CONCERN for message in recorder.messages)
    assert recorder.messages[0][1] == (
        "Packages npm/a@1, unknown, x@2 have Category X licences (including unrecognised licences)"
    )
    data = recorder.messages[0][2]
    assert (data["licensed_components"], data["license_count"], data["licenses_truncated"]) == (5, 3, False)
    assert [item["any_unknown"] for item in data["licenses"]] == [False, True, False]
    assert data["licenses"][2]["expressions"] == ["MIT AND GPL-3.0-only"]
    assert [message[2]["finding"] for message in recorder.messages] == ["licenses", "risk"]
    assert "risks" not in data
    assert "licenses" not in recorder.messages[1][2]
    json.dumps(data)


async def test_review_embargo_keeps_local_checks_without_network(review_input, monkeypatch) -> None:
    path, recorder, args = review_input
    collect = mock.AsyncMock()
    monkeypatch.setattr(observations, "collect", collect)
    recorder.embargoed = True
    path.write_bytes(_LICENSED)
    assert await sbom.review(args) is None
    assert len(recorder.messages) == 1
    assert recorder.messages[0][0] == sql.CheckResultStatus.CONCERN
    assert recorder.messages[0][2]["finding"] == "licenses"
    recorder.messages.clear()
    path.write_bytes(b"{")
    assert await sbom.review(args) is None
    assert len(recorder.messages) == 1
    assert recorder.messages[0][0] == sql.CheckResultStatus.CONCERN
    collect.assert_not_called()


@pytest.mark.parametrize(
    ("content", "concerns"),
    [
        (b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":[]}', 0),
        (b'{"spdxVersion":"SPDX-2.3"}', 0),
        (b"{", 1),
        (b'{"bomFormat":"CycloneDX","specVersion":"1.6","x":1,"x":2}', 1),
        (b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":[{"licenses":{}}]}', 1),
    ],
)
async def test_review_emits_only_structural_concerns(review_input, monkeypatch, content, concerns) -> None:
    path, recorder, args = review_input
    collect = mock.AsyncMock(return_value={})
    monkeypatch.setattr(observations, "collect", collect)
    path.write_bytes(content)
    assert await sbom.review(args) is None
    assert len(recorder.messages) == concerns
    for status, message, data in recorder.messages:
        assert status == sql.CheckResultStatus.CONCERN
        assert data and data["problem"] in message
    if content != b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":[]}':
        collect.assert_not_called()


@pytest.mark.parametrize("count", [1, 3, 105])
async def test_review_headline_and_evidence_cap(review_input, monkeypatch, count) -> None:
    path, recorder, args = review_input
    keys = [f"pkg:npm/p{i:03}" for i in range(count)]
    path.write_bytes(_document(list(reversed(keys))))
    monkeypatch.setattr(
        observations,
        "collect",
        mock.AsyncMock(return_value={key: {"archived": True, "rankings_average": 0.0} for key in keys}),
    )
    await sbom.review(args)
    _status, message, data = recorder.messages[0]
    names = ", ".join(key.removeprefix("pkg:") for key in keys[:3])
    remainder = f" (and {count - 3} more)" if (count > 3) else ""
    assert message == f"Packages {names}{remainder} have maintenance risk above 0.6"
    assert data["risk_count"] == count
    assert len(data["risks"]) == min(count, 100)
    assert data["risks_truncated"] == (count > 100)


async def test_review_internal_deadline_is_operational(review_input, monkeypatch) -> None:
    path, recorder, args = review_input
    path.write_bytes(_document(["pkg:npm/a"]))
    monkeypatch.setattr(sbom, "_DEADLINE_SECONDS", 0)
    monkeypatch.setattr(observations, "collect", _wait_for_cancellation)
    with pytest.raises(task.CheckRetryableError, match="lookup did not complete"):
        await sbom.review(args)
    assert recorder.messages == []


async def test_review_license_evidence_cap_preserves_total_and_headline(review_input) -> None:
    path, recorder, args = review_input
    document = json.loads(_LICENSED)
    document["components"] = [
        {"name": f"p{i:03}", "licenses": [{"expression": "GPL-3.0-only"}]} for i in reversed(range(105))
    ]
    path.write_text(json.dumps(document))
    assert await sbom.review(args) is None
    assert len(recorder.messages) == 1
    _status, message, data = recorder.messages[0]
    assert message == "Packages p000, p001, p002 (and 102 more) have Category X licences"
    assert data["license_count"] == 105
    assert len(data["licenses"]) == 100
    assert data["licenses_truncated"] is True


@pytest.mark.parametrize("error", [OSError("unreadable"), streaming.LimitError("limit")])
async def test_review_limits_and_io_are_operational(review_input, monkeypatch, error) -> None:
    _path, recorder, args = review_input
    monkeypatch.setattr(streaming, "sbom", mock.Mock(side_effect=error))
    with pytest.raises(task.CheckRetryableError, match="SBOM structure could not be checked"):
        await sbom.review(args)
    assert recorder.messages == []


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientError("offline"),
        streaming.MalformedError("JSON"),
        streaming.LimitError("size"),
        ValueError("record"),
    ],
)
async def test_review_provider_failures_are_operational(review_input, monkeypatch, error) -> None:
    path, recorder, args = review_input
    path.write_bytes(_LICENSED)
    monkeypatch.setattr(observations, "collect", mock.AsyncMock(side_effect=error))
    with pytest.raises(task.CheckRetryableError, match="lookup did not complete"):
        await sbom.review(args)
    assert len(recorder.messages) == 1
    assert recorder.messages[0][0] == sql.CheckResultStatus.CONCERN
    assert recorder.messages[0][2]["finding"] == "licenses"


async def test_review_strict_threshold_and_missing_scores(review_input, monkeypatch) -> None:
    path, recorder, args = review_input
    path.write_bytes(_document(["pkg:npm/a", "pkg:npm/b"]))
    monkeypatch.setattr(sbom, "RISK_THRESHOLD", 0.95)
    monkeypatch.setattr(
        observations, "collect", mock.AsyncMock(return_value={"pkg:npm/a": {"archived": True, "rankings_average": 0.0}})
    )
    assert await sbom.review(args) is None
    assert recorder.messages == []


async def test_review_task_cache_and_sidecar(review_input, review_release) -> None:
    _path, _recorder, args = review_input
    release, _session = review_release
    queued = []
    for revision, name in [
        ("00001", "sbom.cdx.json"),
        ("00001", "sbom.cdx.json"),
        ("00002", "sbom.cdx.json"),
        ("00002", "other.cdx.json"),
    ]:
        result = await tasks._sbom_review_task("tester", release, safe.RevisionNumber(revision), safe.RelPath(name))
        assert result is not None
        assert result.revision_number == revision
        queued.append(result)
    assert queued[0].inputs_hash == queued[1].inputs_hash
    assert queued[1].inputs_hash != queued[2].inputs_hash
    assert queued[2].inputs_hash != queued[3].inputs_hash
    assert tasks.resolve(queued[0].task_type) is sbom.review
    assert queued[0].task_type in task.CHECK_TASK_TYPES
    assert task.TASK_TYPE_TIMEOUT_SECONDS[queued[0].task_type] == 600
    attestable.write_checks_data.assert_awaited_with(
        args.project_key,
        args.version_key,
        safe.RevisionNumber("00002"),
        "other.cdx.json",
        {checks.function_key(sbom.review): queued[3].inputs_hash},
    )
    assert (
        await tasks._sbom_review_task("tester", release, args.revision_number, safe.RelPath("archive.tar.gz")) is None
    )


@pytest.mark.parametrize("change", ["release", "embargo", "threshold", "version", "content", "recheck"])
async def test_review_task_cache_scope(review_input, review_release, monkeypatch, change) -> None:
    _path, _recorder, args = review_input
    release, session = review_release
    first = await tasks._sbom_review_task("tester", release, args.revision_number, args.primary_rel_path)
    if change == "release":
        release.key = "other-1.0"
    elif change == "embargo":
        release.expedited = True
    elif change == "threshold":
        monkeypatch.setattr(sbom, "RISK_THRESHOLD", 0.7)
    elif change == "version":
        monkeypatch.setattr(sbom, "REVIEW_VERSION", sbom.REVIEW_VERSION + ".next")
    elif change == "content":
        session.release_file_hash_at.return_value = "blake3:changed"
    elif change == "recheck":
        release.check_cache_key = "recheck"
    second = await tasks._sbom_review_task("tester", release, args.revision_number, args.primary_rel_path)
    assert first.inputs_hash != second.inputs_hash


def _document(purls: list[str]) -> bytes:
    return json.dumps(
        {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [{"purl": purl} for purl in purls]}
    ).encode()


async def _wait_for_cancellation(_keys: list[str]) -> None:
    await asyncio.Future()
