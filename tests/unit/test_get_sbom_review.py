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
import inspect
import unittest.mock as mock

import pytest
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.get.sbom as sbom
import atr.htm as htm
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sbom.models.licenses as licenses


@pytest.mark.parametrize("with_score", [False, True])
async def test_license_concern_uses_full_score_table_without_duplicate_evidence(monkeypatch, with_score):
    licences = _finding(
        {
            "finding": "licenses",
            "license_count": 120,
            "licensed_components": 200,
        }
    )
    exception = _finding(None, sql.CheckResultStatus.EXCEPTION)
    exception.message = "Maintenance lookup did not complete"
    score = results.SBOMToolScore(
        kind="sbom_tool_score",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        file_path=safe.RelPath("sbom.cdx.json"),
        warnings=[],
        errors=[],
        outdated=None,
        cli_errors=None,
        license_errors=[
            licenses.Issue(
                component_name=f"<script>x</script>-{i}",
                component_version="1",
                license_expression="GPL-3.0-only",
                category=licenses.Category.X,
            ).model_dump_json()
            for i in range(120)
        ],
    )
    page = await _page(
        monkeypatch,
        {"licenses": licences, "exception": exception},
        sql.TaskStatus.BROKEN,
        score=score if with_score else None,
    )
    assert "1 concern recorded" in page
    assert "SBOM review did not complete" in page
    assert exception.message in page
    assert ">Exception</span>" in page
    assert 'href="#sbom-review-licenses">Category X licences in 120 dependencies.</a>' in page
    assert licences.message not in page
    assert 'id="sbom-review-licenses"' in page
    assert "Category X licences in 120 dependencies." in page
    assert "Declared expression" not in page
    if with_score:
        assert "&lt;script&gt;x&lt;/script&gt;-119@1" in page
        assert "<script>" not in page
        assert page.count("License Category") == 1
    else:
        assert "No SBOM score found" in page
        assert "License Category" not in page


@pytest.mark.parametrize("truncated", [False, True])
def test_maintenance_shows_all_stored_rows_coverage_and_sources(truncated):
    data = {
        "finding": "risk",
        "threshold": 0.6,
        "packages": 200,
        "scored": 180,
        "unknown": 20,
        "risk_count": 150,
        "risks_truncated": truncated,
        "generated_at": "2026-09-15T13:00:00+00:00",
        "attribution": "ecosyste.ms (CC BY-SA 4.0)",
        "risks": [
            {
                "key": f"pkg:npm/package-{i}",
                "source_purls": [f"pkg:npm/package-{i}@1"],
                "risk": 0.855,
                "archived": True,
                "active_maintainers": 0,
                "repository_url": "https://example.org/repository",
            }
            for i in range(100 if truncated else 150)
        ],
    }
    block = htm.Block()
    finding = _finding(data)
    sbom._review_summary(block, _task(), {"risk": finding}, "/report")
    sbom._maintenance_section(block, finding, _task(), False)
    page = str(block.collect())
    assert 'href="#sbom-review-risk">150 packages with maintenance risk above 0.6.</a>' in page
    assert finding.message not in page
    assert "180/200 packages in the SBOM were given risk scores." in page
    assert "pkg:npm/package-99@1" in page
    assert "0.855" in page
    assert "Active maintainers: 0" in page
    assert "Latest release: Unknown" in page
    assert "https://example.org/repository" in page
    assert "<summary>Over 0.6</summary>" in page
    if truncated:
        assert (
            "These are the top 100 riskiest dependencies over 0.6. There are 150 total dependencies over 0.6 risk."
        ) in page
    else:
        assert "pkg:npm/package-149@1" in page
        assert "These are the top" not in page


@pytest.mark.parametrize(
    ("status", "embargoed", "expected"),
    [
        (None, False, "has not run"),
        (sql.TaskStatus.QUEUED, False, "queued"),
        (sql.TaskStatus.ACTIVE, False, "running"),
        (sql.TaskStatus.COMPLETED, False, "Coverage is unavailable"),
        (sql.TaskStatus.COMPLETED, True, "skipped for embargoed releases"),
    ],
)
def test_maintenance_without_findings_does_not_invent_coverage(status, embargoed, expected):
    block = htm.Block()
    sbom._maintenance_section(block, None, _task(status) if status else None, embargoed)
    page = str(block.collect())
    assert expected in page
    assert "scored" not in page
    assert "healthy" not in page


async def test_review_uses_current_check_hash_and_newest_task(monkeypatch):
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    monkeypatch.setattr(db, "session", sessionmaker)
    monkeypatch.setattr(
        sbom.interaction.attestable,
        "load_checks",
        mock.AsyncMock(return_value={"sbom.cdx.json": {"atr.tasks.checks.sbom.review": "current"}}),
    )
    older = _task(sql.TaskStatus.COMPLETED)
    older.completed = older.added
    queued = _task(sql.TaskStatus.QUEUED)
    queued.added = older.added + datetime.timedelta(seconds=1)
    wrong_path = _task(sql.TaskStatus.QUEUED)
    wrong_path.added = queued.added + datetime.timedelta(seconds=1)
    wrong_path.primary_rel_path = "other.cdx.json"
    wrong_revision = _task(sql.TaskStatus.QUEUED)
    wrong_revision.added = wrong_path.added + datetime.timedelta(seconds=1)
    wrong_revision.revision_number = "00002"
    current = _finding({"problem": "Current problem"})
    current.inputs_hash = "current"
    stale = _finding({"problem": "Stale problem"})
    stale.inputs_hash = "stale"
    try:
        async with sessionmaker() as data:
            data.add_all([older, queued, wrong_path, wrong_revision, current, stale])
            await data.commit()
        task, findings = await sbom._review(_release(), safe.RelPath("sbom.cdx.json"))
        assert task.id == queued.id
        assert findings["structure"].id == current.id
    finally:
        await engine.dispose()


async def test_structure_remains_visible_without_tool_score(monkeypatch):
    page = await _page(monkeypatch, {"structure": _finding({"problem": "Duplicate components"})})
    assert 'href="#sbom-review-structure"' in page
    assert 'id="sbom-review-structure"' in page
    assert page.count("Duplicate components") == 2
    assert "No SBOM score found" in page


def _finding(data, status=sql.CheckResultStatus.CONCERN):
    return sql.CheckResult(
        release_key="test-1.0",
        revision_number="00001",
        checker="atr.tasks.checks.sbom.review",
        primary_rel_path="sbom.cdx.json",
        created=datetime.datetime.now(datetime.UTC),
        status=status,
        message=data.get("problem", "Packages a, b, c (and 117 more)") if data else "Lookup failed",
        data=data,
    )


async def _page(monkeypatch, findings, status=sql.TaskStatus.COMPLETED, score=None):
    monkeypatch.setattr(sbom.shared.sbom, "release_in_phase", mock.AsyncMock(return_value=_release()))
    score_task = sql.Task(task_type=sql.TaskType.SBOM_TOOL_SCORE, result=score) if score else None
    monkeypatch.setattr(sbom, "_score_task", mock.AsyncMock(return_value=score_task))
    monkeypatch.setattr(sbom, "_breakdown", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(sbom, "_review", mock.AsyncMock(return_value=(_task(status), findings)))
    monkeypatch.setattr(sbom.util, "as_url", mock.Mock(return_value="/report/test/1.0/sbom.cdx.json"))
    blank = mock.AsyncMock()
    monkeypatch.setattr(sbom.template, "blank", blank)
    handler = inspect.getclosurevars(inspect.unwrap(sbom.quality)).nonlocals["func"]
    await handler(None, "sbom/quality", safe.ProjectKey("test"), safe.VersionKey("1.0"), safe.RelPath("sbom.cdx.json"))
    return str(blank.call_args.kwargs["content"])


def _release():
    release = sql.Release(
        key="test-1.0",
        project_key="test",
        version="1.0",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
    )
    release.project = sql.Project(key="test", name="Apache Test")
    release._latest_revision_number = "00001"
    return release


def _task(status=sql.TaskStatus.COMPLETED):
    return sql.Task(
        task_type=sql.TaskType.SBOM_REVIEW,
        status=status,
        task_args={},
        asf_uid="test",
        project_key="test",
        version_key="1.0",
        revision_number="00001",
        primary_rel_path="sbom.cdx.json",
    )
