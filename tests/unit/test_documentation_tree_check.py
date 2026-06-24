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

import pytest

import atr.models.safe as safe
import atr.tasks.checks.paths as paths
from tests.unit.recorders import RecorderStub


@pytest.mark.asyncio
async def test_artifact_tree_within_limit_records_no_blocker(tmp_path):
    recorder = _recorder(tmp_path)
    relative_paths = []
    for i in range(300):
        stem = f"binaries/lang-{i}/apache-openoffice-4.1.16-{i}.tar.gz"
        relative_paths += [safe.RelPath(stem), safe.RelPath(stem + ".asc"), safe.RelPath(stem + ".sha512")]
    await paths._check_documentation_tree(recorder, relative_paths)
    assert recorder.messages == []


@pytest.mark.asyncio
async def test_bundled_docs_over_limit_records_blocker(tmp_path):
    recorder = _recorder(tmp_path)
    relative_paths = [safe.RelPath(f"javadoc/page-{i}.html") for i in range(paths._DOC_TREE_MAX_FILES + 1)]
    await paths._check_documentation_tree(recorder, relative_paths)
    blockers = [(s, m) for s, m, _ in recorder.messages if s == "blocker"]
    assert len(blockers) == 1
    assert "documentation" in blockers[0][1]


@pytest.mark.asyncio
async def test_bundled_docs_within_limit_records_no_blocker(tmp_path):
    recorder = _recorder(tmp_path)
    relative_paths = [safe.RelPath(f"docs/page-{i}.html") for i in range(paths._DOC_TREE_MAX_FILES)]
    await paths._check_documentation_tree(recorder, relative_paths)
    assert recorder.messages == []


def test_is_bundled_doc_excludes_artifacts_metadata_and_top_level():
    assert paths._is_bundled_doc(safe.RelPath("javadoc/index.html")) is True
    assert paths._is_bundled_doc(safe.RelPath("binaries/de/app-1.0.tar.gz")) is False
    assert paths._is_bundled_doc(safe.RelPath("binaries/de/app-1.0.tar.gz.asc")) is False
    assert paths._is_bundled_doc(safe.RelPath("sub/sbom.cdx.json")) is False
    assert paths._is_bundled_doc(safe.RelPath("index.html")) is False


def _recorder(tmp_path) -> RecorderStub:
    return RecorderStub(safe.StatePath(tmp_path), "atr.tasks.checks.paths.check_errors", "5")
