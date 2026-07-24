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

import pathlib
import types
import unittest.mock as mock

import pytest

import atr.models as models
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.sbom
import atr.tasks as tasks
import atr.tasks.sbom as sbom


@pytest.mark.asyncio
async def test_sbom_generate_cyclonedx_records_artifact_rel_path(tmp_path: pathlib.Path) -> None:
    data = mock.MagicMock()
    data.commit = mock.AsyncMock()
    data.refresh = mock.AsyncMock()
    release_query = mock.MagicMock()
    release_query.demand = mock.AsyncMock(return_value=_draft_release())
    data.release = mock.MagicMock(return_value=release_query)
    writer = _writer_with(data)

    revision = safe.StatePath(tmp_path)
    rel_path = safe.RelPath("binaries/artifact.tar.gz")

    task = await writer.generate_cyclonedx(
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        safe.RevisionNumber("00001"),
        revision / rel_path,
        revision / safe.RelPath(f"{rel_path!s}.cdx.json"),
        rel_path,
    )

    assert str(task.task_args["source_name"]) == "binaries/artifact.tar.gz"


def test_sbom_generate_results_round_trip() -> None:
    task = sql.Task(task_type=sql.TaskType.SBOM_GENERATE, task_args={}, asf_uid="test")
    api_results = models.api.SbomGenerateResults(endpoint="/sbom/generate", task=task)

    parsed = models.api.validate_sbom_generate(api_results.model_dump(mode="json"))

    assert parsed.task.task_type == sql.TaskType.SBOM_GENERATE


@pytest.mark.asyncio
async def test_sbom_generate_revision_blocks_non_draft() -> None:
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    release_query = mock.MagicMock()
    release_query.demand = mock.AsyncMock(return_value=_draft_release(sql.ReleasePhase.RELEASE_CANDIDATE))
    data.release = mock.MagicMock(return_value=release_query)
    writer = _writer_with(data)

    with pytest.raises(storage.AccessError, match="candidate draft"):
        await writer.generate_cyclonedx_revision(
            safe.ProjectKey("project"),
            safe.VersionKey("1.0.0"),
            safe.RevisionNumber("00001"),
            safe.RelPath("artifact.tar.gz"),
        )


@pytest.mark.asyncio
async def test_sbom_generate_revision_reuses_ongoing_task() -> None:
    existing = sql.Task(task_type=sql.TaskType.SBOM_GENERATE, task_args={}, asf_uid="tester")
    data = mock.MagicMock()
    data.begin_immediate = mock.AsyncMock()
    release_query = mock.MagicMock()
    release_query.demand = mock.AsyncMock(return_value=_draft_release())
    data.release = mock.MagicMock(return_value=release_query)
    task_query = mock.MagicMock()
    task_query.all = mock.AsyncMock(return_value=[existing])
    data.task = mock.MagicMock(return_value=task_query)
    writer = _writer_with(data)

    result = await writer.generate_cyclonedx_revision(
        safe.ProjectKey("project"),
        safe.VersionKey("1.0.0"),
        safe.RevisionNumber("00001"),
        safe.RelPath("artifact.tar.gz"),
    )

    assert result is existing
    data.add.assert_not_called()
    data.task.assert_called_once_with(
        status_in=[sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE],
        task_type=sql.TaskType.SBOM_GENERATE,
        asf_uid="tester",
        project_key="project",
        version_key="1.0.0",
        primary_rel_path="artifact.tar.gz",
    )


def test_sbom_generate_task_resolves() -> None:
    assert tasks.resolve(sql.TaskType.SBOM_GENERATE) is sbom.generate


def test_strip_temp_prefix_leaves_component_names_relative_to_the_scan_root() -> None:
    temp_dir = "/example/cyclonedx_sbom_abc123"
    doc = {
        "components": [
            {"bom-ref": "aaa", "type": "file", "name": f"{temp_dir}/META-INF/maven/pom.xml"},
            {"bom-ref": "bbb", "type": "library", "name": "plexus-utils", "version": "4.0.2"},
        ]
    }

    sbom._strip_temp_prefix(doc, temp_dir)

    assert doc["components"][0]["name"] == "/META-INF/maven/pom.xml"
    # A path is not a versioned thing, so stripping it must not invent one
    assert "version" not in doc["components"][0]
    assert doc["components"][1]["name"] == "plexus-utils"


def _syft_shaped_doc() -> dict:
    # syft describes the scanned archive with a synthetic file component and roots the dependency
    # graph on the real package it found inside
    return {
        "metadata": {"component": {"bom-ref": "file-wrapper", "type": "file", "name": "widget-1.0.jar"}},
        "components": [
            {"bom-ref": "pkg:maven/org.example/widget@1.0", "type": "library", "name": "widget", "version": "1.0"},
            {"bom-ref": "pkg:maven/org.example/helper@2.0", "type": "library", "name": "helper", "version": "2.0"},
        ],
        "dependencies": [
            {"ref": "pkg:maven/org.example/widget@1.0", "dependsOn": ["pkg:maven/org.example/helper@2.0"]},
        ],
    }


def test_promote_primary_component_replaces_the_file_wrapper_with_the_graph_root() -> None:
    doc = _syft_shaped_doc()

    sbom._promote_primary_component(doc)

    # The primary is now the real package, not the synthetic file
    assert doc["metadata"]["component"]["bom-ref"] == "pkg:maven/org.example/widget@1.0"
    assert doc["metadata"]["component"]["type"] == "library"
    # It no longer appears twice, and the graph still resolves against it
    assert [c["bom-ref"] for c in doc["components"]] == ["pkg:maven/org.example/helper@2.0"]
    assert doc["dependencies"][0]["ref"] == doc["metadata"]["component"]["bom-ref"]


def test_promote_primary_component_abstains_without_a_single_root() -> None:
    # A distribution that bundles sibling modules has no one primary, so the file wrapper stands
    doc = _syft_shaped_doc()
    doc["dependencies"] = [
        {"ref": "pkg:maven/org.example/widget@1.0", "dependsOn": []},
        {"ref": "pkg:maven/org.example/helper@2.0", "dependsOn": []},
    ]

    sbom._promote_primary_component(doc)

    assert doc["metadata"]["component"]["bom-ref"] == "file-wrapper"
    assert len(doc["components"]) == 2


def test_promote_primary_component_abstains_without_a_dependency_graph() -> None:
    doc = _syft_shaped_doc()
    del doc["dependencies"]

    sbom._promote_primary_component(doc)

    assert doc["metadata"]["component"]["bom-ref"] == "file-wrapper"


def test_task_get_results_round_trip_typed_result() -> None:
    task = sql.Task(task_type=sql.TaskType.SBOM_GENERATE, task_args={}, asf_uid="test")
    task.result = results.SBOMGenerate(
        kind="sbom_generate",
        path="artifact.tar.gz.cdx.json",
        bom_version=2,
        revision_number="00002",
    )
    api_results = models.api.TaskGetResults(endpoint="/task/get", task=task)

    parsed = models.api.validate_task_get(api_results.model_dump(mode="json"))

    assert isinstance(parsed.task.result, results.SBOMGenerate)
    assert parsed.task.result.revision_number == "00002"


def _draft_release(phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        project=types.SimpleNamespace(is_active=True),
        is_embargoed=False,
        phase=phase,
    )


def _writer_with(data: mock.MagicMock) -> atr.storage.writers.sbom.CommitteeParticipant:
    writer = object.__new__(atr.storage.writers.sbom.CommitteeParticipant)
    writer._CommitteeParticipant__data = data
    writer._CommitteeParticipant__asf_uid = "tester"
    writer._CommitteeParticipant__committee_key = "project"
    writer._CommitteeParticipant__write = mock.MagicMock()
    return writer
