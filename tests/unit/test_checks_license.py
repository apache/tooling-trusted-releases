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

import atr.tasks.checks.license as license

TEST_ARCHIVE = pathlib.Path(__file__).parent.parent / "e2e" / "test_files" / "apache-test-0.2.tar.gz"


def test_headers_check_data_fields_match_model():
    results = list(license._headers_check_core_logic(str(TEST_ARCHIVE), [], "none"))
    artifact_results = [r for r in results if isinstance(r, license.ArtifactResult)]
    final_result = artifact_results[-1]
    expected_fields = set(license.ArtifactData.model_fields.keys())
    actual_fields = set(final_result.data.keys())
    assert actual_fields == expected_fields


def test_headers_check_excludes_matching_files():
    results_without_excludes = list(license._headers_check_core_logic(str(TEST_ARCHIVE), [], "none"))
    results_with_excludes = list(license._headers_check_core_logic(str(TEST_ARCHIVE), ["*.py"], "policy"))

    def get_files_checked(results: list) -> int:
        for r in results:
            if isinstance(r, license.ArtifactResult) and r.data and ("files_checked" in r.data):
                return r.data["files_checked"]
        return 0

    without_excludes = get_files_checked(results_without_excludes)
    with_excludes = get_files_checked(results_with_excludes)
    assert with_excludes < without_excludes


def test_headers_check_includes_excludes_source_none():
    results = list(license._headers_check_core_logic(str(TEST_ARCHIVE), [], "none"))
    artifact_results = [r for r in results if isinstance(r, license.ArtifactResult)]
    assert len(artifact_results) > 0
    final_result = artifact_results[-1]
    assert final_result.data["excludes_source"] == "none"


def test_headers_check_includes_excludes_source_policy():
    results = list(license._headers_check_core_logic(str(TEST_ARCHIVE), [], "policy"))
    artifact_results = [r for r in results if isinstance(r, license.ArtifactResult)]
    final_result = artifact_results[-1]
    assert final_result.data["excludes_source"] == "policy"
