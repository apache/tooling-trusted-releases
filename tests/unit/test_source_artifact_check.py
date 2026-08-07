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

import collections
import datetime
import pathlib
import unittest.mock as mock

import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.render as render
import atr.storage.datatypes as datatypes
import atr.storage.readers.releases as releases
import atr.tasks.checks as checks
import atr.tasks.checks.paths as paths
from tests.unit.recorders import RecorderStub


@pytest.mark.asyncio
async def test_binary_only_artifacts_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
    args = _make_function_args(recorder)
    classifications = {
        "apache-test-1.0-bin.tar.gz": "binary",
        "apache-test-1.0-bin.tar.gz.sha512": "metadata",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    blockers = [(s, m) for s, m, _ in recorder.messages if s == "blocker"]
    assert len(blockers) == 1
    assert "source release artifact" in blockers[0][1]


@pytest.mark.asyncio
async def test_blocker_with_path_goes_to_blockers():
    info = datatypes.PathInfo()
    result = _make_check_result(sql.CheckResultStatus.BLOCKER, "Bad path", primary_rel_path="some-file.tar.gz")
    cs = datatypes.ChecksSubset(checks=[result], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    await reader._GeneralPublic__blocker(cs)  # type: ignore[attr-defined]
    assert len(info.release_level_concerns) == 0
    assert len(info.release_level_blockers) == 0
    path = safe.RelPath("some-file.tar.gz")
    assert path in info.blockers
    assert info.blockers[path][0] is result
    assert path not in info.concerns


@pytest.mark.asyncio
async def test_blocker_without_path_goes_to_release_level_blockers():
    info = datatypes.PathInfo()
    result = _make_check_result(sql.CheckResultStatus.BLOCKER, "No source artifact")
    cs = datatypes.ChecksSubset(checks=[result], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    await reader._GeneralPublic__blocker(cs)  # type: ignore[attr-defined]
    assert len(info.release_level_blockers) == 1
    assert info.release_level_blockers[0] is result
    assert len(info.release_level_concerns) == 0


@pytest.mark.asyncio
async def test_concern_without_path_goes_to_release_level_concerns():
    info = datatypes.PathInfo()
    result = _make_check_result(sql.CheckResultStatus.CONCERN, "Some failure")
    cs = datatypes.ChecksSubset(checks=[result], info=info, match_ignore=lambda _: False)
    reader = _make_reader()
    await reader._GeneralPublic__concerns(cs)  # type: ignore[attr-defined]
    assert len(info.release_level_concerns) == 1
    assert info.release_level_concerns[0] is result


@pytest.mark.asyncio
async def test_fallback_for_partial_db_classifications(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
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
    assert not any(s in {"concern", "blocker"} for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_attestable_when_db_empty(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
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
    assert not any(s in {"concern", "blocker"} for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_classify_binary_only_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
    args = _make_function_args(recorder)
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session({}))
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=None))
    relative_paths = [
        safe.RelPath("apache-test-1.0-bin.tar.gz"),
        safe.RelPath("apache-test-1.0-bin.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    blockers = [(s, m) for s, m, _ in recorder.messages if s == "blocker"]
    assert len(blockers) == 1
    assert "source release artifact" in blockers[0][1]


@pytest.mark.asyncio
async def test_fallback_to_classify_uses_attestable_policy_matchers(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
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
    assert not any(s in {"concern", "blocker"} for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_classify_uses_project_policy_matchers(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
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
    assert not any(s in {"concern", "blocker"} for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_fallback_to_classify_when_no_attestable(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
    args = _make_function_args(recorder)
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session({}))
    monkeypatch.setattr("atr.attestable.load", mock.AsyncMock(return_value=None))
    relative_paths = [
        safe.RelPath("apache-test-1.0-source.tar.gz"),
        safe.RelPath("apache-test-1.0-source.tar.gz.sha512"),
    ]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s in {"concern", "blocker"} for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_keys_file_records_single_actionable_blocker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    (tmp_path / "KEYS").write_text("", encoding="utf-8")
    monkeypatch.setattr(paths.user, "is_admin_async", mock.AsyncMock(return_value=False))
    recorder_problems = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "4")
    recorder_suggestions = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_warnings", "4")
    recorder_notes = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_success", "4")

    await paths._check_path_process_single(
        "testuser",
        safe.StatePath(tmp_path),
        safe.RelPath("KEYS"),
        recorder_problems,
        recorder_suggestions,
        recorder_notes,
        {"KEYS"},
        False,
    )

    assert recorder_problems.messages == [
        (
            "blocker",
            "KEYS: The KEYS file should be uploaded via the 'Keys' section, not included in the artifact bundle",
            {},
        )
    ]
    assert recorder_suggestions.messages == []
    assert recorder_notes.messages == []


@pytest.mark.asyncio
async def test_maven_cyclonedx_sbom_not_flagged_as_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(paths.user, "is_admin_async", mock.AsyncMock(return_value=False))
    recorder_problems = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "4")
    recorder_suggestions = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_warnings", "4")
    recorder_notes = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_success", "4")

    sbom = "atr-maven-plugin-1.0.0-alpha-1-cyclonedx.json"
    artifact = "atr-maven-plugin-1.0.0-alpha-1.jar"
    await paths._check_path_process_single(
        "testuser",
        safe.StatePath(tmp_path),
        safe.RelPath(sbom),
        recorder_problems,
        recorder_suggestions,
        recorder_notes,
        {sbom, artifact},
        False,
    )

    assert recorder_problems.messages == []
    assert recorder_suggestions.messages == []
    assert len(recorder_notes.messages) == 1


@pytest.mark.asyncio
async def test_no_artifacts_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
    args = _make_function_args(recorder)
    classifications = {
        "README.md": "docs",
        "LICENSE": "docs",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    blockers = [(s, m) for s, m, _ in recorder.messages if s == "blocker"]
    assert len(blockers) == 1
    assert "source release artifact" in blockers[0][1]


def test_render_checks_summary_emits_new_badge_classes():
    info = datatypes.PathInfo()
    stat = datatypes.CheckerStats(
        checker="atr.tasks.checks.paths.check_errors",
        counts=collections.Counter(
            {
                sql.CheckResultStatus.SUGGESTION: 1,
                sql.CheckResultStatus.CONCERN: 1,
            }
        ),
        files={},
    )
    info.checker_stats.append(stat)
    element = render.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))
    assert element is not None
    rendered = str(element)
    assert "atr-bg-suggestion" in rendered
    assert "atr-bg-concern" in rendered


def test_render_checks_summary_prominent_entries_first_and_expanded():
    info = datatypes.PathInfo()
    stat = datatypes.CheckerStats(
        checker="atr.tasks.checks.license.files",
        counts=collections.Counter({sql.CheckResultStatus.CONCERN: 1}),
        files={},
    )
    info.checker_stats.append(stat)
    result = _make_check_result(
        sql.CheckResultStatus.BLOCKER,
        "Release must contain at least one source release artifact",
        checker="atr.tasks.checks.paths.check_source",
    )
    info.release_level_blockers.append(result)
    element = render.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))
    assert element is not None
    rendered = str(element)
    assert rendered.index("Paths Check Source") < rendered.index("License Files")
    assert rendered.count("<details") == 1


def test_render_checks_summary_returns_none_when_no_errors():
    info = datatypes.PathInfo()
    element = render.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))
    assert element is None


def test_render_checks_summary_sbom_entry_includes_cta():
    info = datatypes.PathInfo()
    stat = datatypes.CheckerStats(
        checker="atr.tasks.checks.sbom.check",
        counts=collections.Counter({sql.CheckResultStatus.SUGGESTION: 1}),
        files={},
    )
    info.checker_stats.append(stat)
    element = render.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))
    assert element is not None
    assert "Consider creating an SBOM." in str(element)


def test_render_checks_summary_shows_release_level_errors():
    info = datatypes.PathInfo()
    result = _make_check_result(
        sql.CheckResultStatus.CONCERN,
        "Some path issue",
    )
    info.release_level_concerns.append(result)
    element = render.render_checks_summary(info, safe.ProjectKey("test"), safe.VersionKey("1.0"))
    assert element is not None


@pytest.mark.asyncio
async def test_sbom_signature_not_flagged_as_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(paths.user, "is_admin_async", mock.AsyncMock(return_value=False))
    recorder_problems = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "4")
    recorder_suggestions = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_warnings", "4")
    recorder_notes = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_success", "4")

    signature = "apache-test-1.0.tar.gz.cdx.json.asc"
    sbom = "apache-test-1.0.tar.gz.cdx.json"
    await paths._check_path_process_single(
        "testuser",
        safe.StatePath(tmp_path),
        safe.RelPath(signature),
        recorder_problems,
        recorder_suggestions,
        recorder_notes,
        {signature, sbom},
        False,
    )

    assert recorder_problems.messages == []
    assert recorder_suggestions.messages == []
    assert len(recorder_notes.messages) == 1


@pytest.mark.asyncio
async def test_source_artifact_present_no_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
    args = _make_function_args(recorder)
    classifications = {
        "apache-test-1.0-source.tar.gz": "source",
        "apache-test-1.0-source.tar.gz.sha512": "metadata",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    assert not any(s in {"concern", "blocker"} for s, _, _ in recorder.messages)


@pytest.mark.asyncio
async def test_source_classified_non_artifact_still_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path):
    recorder = RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_source", "4")
    args = _make_function_args(recorder)
    classifications = {
        "README.md": "source",
    }
    monkeypatch.setattr("atr.db.session", lambda: _mock_db_session(classifications))
    relative_paths = [safe.RelPath(p) for p in classifications]
    await paths._check_source_artifact_present(args, recorder, relative_paths, safe.StatePath(tmp_path))
    blockers = [(s, m) for s, m, _ in recorder.messages if s == "blocker"]
    assert len(blockers) == 1
    assert "source release artifact" in blockers[0][1]


def _make_check_result(
    status: sql.CheckResultStatus,
    message: str,
    primary_rel_path: str | None = None,
    checker: str = "atr.tasks.checks.paths.check_errors",
) -> sql.CheckResult:
    return sql.CheckResult(
        id=0,
        release_key="test-1.0",
        revision_number="00001",
        checker=checker,
        checker_version="4",
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
    mock_release = mock.MagicMock(key="test-1.0")
    mock_release_query = mock.MagicMock()
    mock_release_query.demand = mock.AsyncMock(return_value=mock_release)
    mock_data.release = mock.MagicMock(return_value=mock_release_query)
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
