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
import unittest.mock as mock

import pytest

import atr.attestable as attestable
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sbom.streaming as streaming
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.tasks.checks.sbom as sbom
import atr.tasks.task as task
import tests.unit.recorders as recorders


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


@pytest.mark.parametrize(
    ("content", "concerns"),
    [
        (b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":[]}', 0),
        (b'{"spdxVersion":"SPDX-2.3"}', 0),
        (b"{", 1),
        (b'{"bomFormat":"CycloneDX","specVersion":"1.6","x":1,"x":2}', 1),
    ],
)
async def test_review_emits_only_structural_concerns(review_input, content, concerns) -> None:
    path, recorder, args = review_input
    path.write_bytes(content)
    assert await sbom.review(args) is None
    assert len(recorder.messages) == concerns
    for status, message, data in recorder.messages:
        assert status == sql.CheckResultStatus.CONCERN
        assert data and data["problem"] in message


@pytest.mark.parametrize("error", [OSError("unreadable"), streaming.LimitError("limit")])
async def test_review_limits_and_io_are_operational(review_input, monkeypatch, error) -> None:
    _path, recorder, args = review_input
    monkeypatch.setattr(streaming, "sbom", mock.Mock(side_effect=error))
    with pytest.raises(task.CheckRetryableError, match="SBOM structure could not be checked"):
        await sbom.review(args)
    assert recorder.messages == []


async def test_review_task_cache_and_sidecar(review_input, monkeypatch) -> None:
    _path, _recorder, args = review_input
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
    write_checks = mock.AsyncMock()
    monkeypatch.setattr(attestable, "write_checks_data", write_checks)
    queued = []
    for revision, name in [("00001", "sbom.cdx.json"), ("00002", "sbom.cdx.json"), ("00002", "other.cdx.json")]:
        result = await tasks._sbom_review_task("tester", release, safe.RevisionNumber(revision), safe.RelPath(name))
        assert result is not None
        assert result.revision_number == revision
        queued.append(result)
    assert queued[0].inputs_hash == queued[1].inputs_hash
    assert queued[1].inputs_hash != queued[2].inputs_hash
    assert tasks.resolve(queued[0].task_type) is sbom.review
    assert queued[0].task_type in task.CHECK_TASK_TYPES
    write_checks.assert_awaited_with(
        args.project_key,
        args.version_key,
        safe.RevisionNumber("00002"),
        "other.cdx.json",
        {checks.function_key(sbom.review): queued[2].inputs_hash},
    )
    assert (
        await tasks._sbom_review_task("tester", release, args.revision_number, safe.RelPath("archive.tar.gz")) is None
    )
