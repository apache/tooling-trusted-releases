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

import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.web as web
import atr.storage.readers.releases as releases
import atr.storage.types as types
import atr.tasks.checks as checks
import atr.tasks.checks.paths as paths
from tests.unit.recorders import RecorderStub


@pytest.mark.asyncio
async def test_binary_only_artifacts_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    classifications = {
        "apache-test-1.0-bin.tar.gz": "binary",
        "apache-test-1.0-bin.tar.gz.sha512": "metadata",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    failures = [(s, m) for s, m, _ in recorder.messages if s == "failure"]
    assert len(failures) == 1
    assert "source release artifact" in failures[0][1]


@pytest.mark.asyncio
async def test_blocker_with_path_goes_to_blockers():
    info = types.PathInfo()
    result = _make_check_result(sql.CheckResultStatus.BLOCKER, "Bad path", primary_rel_path="some-file.tar.gz")
    cs = types.ChecksSubset(checks=[result], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    await reader._GeneralPublic__blocker(cs)  # type: ignore[attr-defined]
    assert len(info.release_level_errors) == 0
    assert len(info.release_level_blockers) == 0
    path = safe.RelPath("some-file.tar.gz")
    assert path in info.blockers
    assert info.blockers[path][0] is result
    assert path not in info.errors


@pytest.mark.asyncio
async def test_blocker_without_path_goes_to_release_level_blockers():
    info = types.PathInfo()
    result = _make_check_result(sql.CheckResultStatus.BLOCKER, "No source artifact")
    cs = types.ChecksSubset(checks=[result], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    await reader._GeneralPublic__blocker(cs)  # type: ignore[attr-defined]
    assert len(info.release_level_blockers) == 1
    assert info.release_level_blockers[0] is result
    assert len(info.release_level_errors) == 0


@pytest.mark.asyncio
async def test_failure_without_path_goes_to_release_level_errors():
    info = types.PathInfo()
    result = _make_check_result(sql.CheckResultStatus.FAILURE, "Some failure")
    cs = types.ChecksSubset(checks=[result], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    await reader._GeneralPublic__errors(cs)  # type: ignore[attr-defined]
    assert len(info.release_level_errors) == 1
    assert info.release_level_errors[0] is result


@pytest.mark.asyncio
async def test_fallback_for_partial_db_classifications(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    classifications = {
        "apache-test-1.0-source.tar.gz.sha512": "metadata",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=None))
    relative_paths = [
        safe.RelPath("apache-test-1.0-source.tar.gz"),
        safe.RelPath("apache-test-1.0-source.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s == "failure" for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_attestable_when_db_empty(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session({}))
    attestable_data = mock.MagicMock()
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=attestable_data))
    monkeypatch.setattr(
        "atr.attestable.path_classification",
        lambda _att, path: "source" if path.endswith(".tar.gz") else "metadata",
    )
    relative_paths = [
        safe.RelPath("apache-test-1.0-source.tar.gz"),
        safe.RelPath("apache-test-1.0-source.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s == "failure" for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_classify_binary_only_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session({}))
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=None))
    relative_paths = [
        safe.RelPath("apache-test-1.0-bin.tar.gz"),
        safe.RelPath("apache-test-1.0-bin.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    failures = [(s, m) for s, m, _ in recorder.messages if s == "failure"]
    assert len(failures) == 1
    assert "source release artifact" in failures[0][1]


@pytest.mark.asyncio
async def test_fallback_to_classify_uses_attestable_policy_matchers(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session({}))
    attestable_data = mock.MagicMock()
    attestable_data.policy = {"source_artifact_paths": ["*.tar.gz"]}
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=attestable_data))
    monkeypatch.setattr("atr.attestable.path_classification", lambda _att, _path: None)
    relative_paths = [
        safe.RelPath("my-project-1.0.tar.gz"),
        safe.RelPath("my-project-1.0.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s == "failure" for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_classify_uses_project_policy_matchers(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    monkeypatch.setattr(
        "atr.db.session",
        lambda: _mock_db_session({}, source_artifact_paths=["*.tar.gz"]),
    )
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=None))
    relative_paths = [
        safe.RelPath("my-project-1.0.tar.gz"),
        safe.RelPath("my-project-1.0.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s == "failure" for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_classify_when_no_attestable(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session({}))
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=None))
    relative_paths = [
        safe.RelPath("apache-test-1.0-source.tar.gz"),
        safe.RelPath("apache-test-1.0-source.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s == "failure" for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_no_artifacts_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    classifications = {
        "README.md": "docs",
        "LICENSE": "docs",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    failures = [(s, m) for s, m, _ in recorder.messages if s == "failure"]
    assert len(failures) == 1
    assert "source release artifact" in failures[0][1]


def test_render_checks_summary_returns_none_when_no_errors():
    info = types.PathInfo()
    element = web.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))
    assert element is None


def test_render_checks_summary_shows_release_level_errors():
    info = types.PathInfo()
    result = _make_check_result(
        sql.CheckResultStatus.FAILURE,
        "Release must contain at least one source release artifact",
    )
    info.release_level_errors.append(result)
    element = web.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))
    assert element is not None


@pytest.mark.asyncio
async def test_source_artifact_present_no_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    classifications = {
        "apache-test-1.0-source.tar.gz": "source",
        "apache-test-1.0-source.tar.gz.sha512": "metadata",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s == "failure" for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_source_classified_non_artifact_still_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "3")
    args = _make_function_args(recorder)
    classifications = {
        "README.md": "source",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    failures = [(s, m) for s, m, _ in recorder.messages if s == "failure"]
    assert len(failures) == 1
    assert "source release artifact" in failures[0][1]


def _make_check_result(
    status: sql.CheckResultStatus,
    message: str,
    primary_rel_path: str | None = None,
) -> sql.CheckResult:
    return sql.CheckResult(
        id=0,
        release_key="test-1.0",
        revision_number="00001",
        checker="atr.tasks.checks.paths.check_errors",
        checker_version="3",
        primary_rel_path=primary_rel_path,
        member_rel_path=None,
        created=datetime.datetime.now(datetime.UTC),
        status=status,
        message=message,
        data={},
        inputs_hash=None,
    )


def _make_function_args(recorder_stub: RecorderStub) -> checks.FunctionArguments:
    async def _recorder(_version: str | None) -> checks.Recorder:
        return recorder_stub

    return checks.FunctionArguments(
        recorder=_recorder,
        asf_uid="testuser",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=None,
        extra_args={},
    )


def _make_reader() -> releases.GeneralPublic:
    read = mock.MagicMock()
    read.authorisation.asf_uid = "testuser"
    read_as = mock.MagicMock()
    data = mock.AsyncMock()
    return releases.GeneralPublic(read, read_as, data)


def _mock_db_session(
    classifications: dict[str, str],
    source_artifact_paths: list[str] | None = None,
    binary_artifact_paths: list[str] | None = None,
) -> mock.AsyncMock:
    mock_data = mock.AsyncMock()
    mock_data.release_file_classifications_at = mock.AsyncMock(return_value=classifications)
    mock_project = mock.MagicMock()
    mock_project.policy_source_artifact_paths = source_artifact_paths or []
    mock_project.policy_binary_artifact_paths = binary_artifact_paths or []
    mock_query = mock.MagicMock()
    mock_query.demand = mock.AsyncMock(return_value=mock_project)
    mock_data.project = mock.MagicMock(return_value=mock_query)
    mock_session_ctx = mock.AsyncMock()
    mock_session_ctx.__aenter__ = mock.AsyncMock(return_value=mock_data)
    mock_session_ctx.__aexit__ = mock.AsyncMock(return_value=False)
    return mock_session_ctx
