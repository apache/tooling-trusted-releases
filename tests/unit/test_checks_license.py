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
import tarfile

import atr.constants as constants
import atr.models.sql as sql
import atr.tasks.checks.license as license

TEST_ARCHIVE = pathlib.Path(__file__).parent.parent / "e2e" / "test_files" / "apache-test-0.2.tar.gz"
TEST_ARCHIVE_BASENAME: str = TEST_ARCHIVE.name

NOTICE_VALID: str = (
    "Apache Test\n"
    "Copyright 2024 The Apache Software Foundation\n"
    "\n"
    "This product includes software developed at\n"
    "The Apache Software Foundation (http://www.apache.org/).\n"
)


def test_files_missing_cache_dir():
    results = list(license._files_check_core_logic(pathlib.Path("/nonexistent"), is_podling=False))
    assert len(results) == 1
    assert results[0].status == sql.CheckResultStatus.FAILURE
    assert "not available" in results[0].message.lower()


def test_files_multiple_root_dirs(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "root-a").mkdir()
    (cache_dir / "root-b").mkdir()
    results = list(license._files_check_core_logic(cache_dir, is_podling=False))
    assert len(results) >= 1
    assert results[0].status == sql.CheckResultStatus.FAILURE
    assert "root directory" in results[0].message.lower()


def test_files_no_root_dirs(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "LICENSE").write_text("stray file")
    results = list(license._files_check_core_logic(cache_dir, is_podling=False))
    assert len(results) >= 1
    assert results[0].status == sql.CheckResultStatus.FAILURE
    assert "0" in results[0].message


def test_files_podling_without_disclaimer(tmp_path):
    cache_dir = _cache_with_root(tmp_path)
    root = cache_dir / "apache-test-0.2"
    (root / "LICENSE").write_text(constants.APACHE_LICENSE_2_0)
    (root / "NOTICE").write_text(NOTICE_VALID)
    results = list(license._files_check_core_logic(cache_dir, is_podling=True))
    assert any(isinstance(r, license.ArtifactResult) and (r.status == sql.CheckResultStatus.BLOCKER) for r in results)


def test_files_single_root_with_stray_top_level_file(tmp_path):
    cache_dir = _cache_with_root(tmp_path)
    root = cache_dir / "apache-test-0.2"
    root.mkdir(exist_ok=True)
    (root / "LICENSE").write_text(constants.APACHE_LICENSE_2_0)
    (root / "NOTICE").write_text(NOTICE_VALID)
    (cache_dir / "stray.txt").write_text("ignored")
    results = list(license._files_check_core_logic(cache_dir, is_podling=False))
    statuses = [r.status for r in results if isinstance(r, license.ArtifactResult)]
    assert sql.CheckResultStatus.SUCCESS in statuses


def test_files_valid_license_and_notice(tmp_path):
    cache_dir = _cache_with_root(tmp_path)
    root = cache_dir / "apache-test-0.2"
    (root / "LICENSE").write_text(constants.APACHE_LICENSE_2_0)
    (root / "NOTICE").write_text(NOTICE_VALID)
    results = list(license._files_check_core_logic(cache_dir, is_podling=False))
    artifact_results = [r for r in results if isinstance(r, license.ArtifactResult)]
    assert all(r.status == sql.CheckResultStatus.SUCCESS for r in artifact_results)


def test_headers_check_data_fields_match_model(tmp_path):
    cache_dir = _extract_test_archive(tmp_path)
    results = list(license._headers_check_core_logic(cache_dir, TEST_ARCHIVE_BASENAME, [], "none"))
    artifact_results = [r for r in results if isinstance(r, license.ArtifactResult)]
    final_result = artifact_results[-1]
    expected_fields = set(license.ArtifactData.model_fields.keys())
    actual_fields = set(final_result.data.keys())
    assert actual_fields == expected_fields


def test_headers_check_excludes_matching_files(tmp_path):
    cache_dir = _extract_test_archive(tmp_path)
    results_without_excludes = list(license._headers_check_core_logic(cache_dir, TEST_ARCHIVE_BASENAME, [], "none"))
    results_with_excludes = list(
        license._headers_check_core_logic(cache_dir, TEST_ARCHIVE_BASENAME, ["*.py"], "policy")
    )

    def get_files_checked(results: list) -> int:
        for r in results:
            if isinstance(r, license.ArtifactResult) and r.data and ("files_checked" in r.data):
                return r.data["files_checked"]
        return 0

    without_excludes = get_files_checked(results_without_excludes)
    with_excludes = get_files_checked(results_with_excludes)
    assert with_excludes < without_excludes


def test_headers_check_includes_excludes_source_none(tmp_path):
    cache_dir = _extract_test_archive(tmp_path)
    results = list(license._headers_check_core_logic(cache_dir, TEST_ARCHIVE_BASENAME, [], "none"))
    artifact_results = [r for r in results if isinstance(r, license.ArtifactResult)]
    assert len(artifact_results) > 0
    final_result = artifact_results[-1]
    assert final_result.data["excludes_source"] == "none"


def test_headers_check_includes_excludes_source_policy(tmp_path):
    cache_dir = _extract_test_archive(tmp_path)
    results = list(license._headers_check_core_logic(cache_dir, TEST_ARCHIVE_BASENAME, [], "policy"))
    artifact_results = [r for r in results if isinstance(r, license.ArtifactResult)]
    final_result = artifact_results[-1]
    assert final_result.data["excludes_source"] == "policy"


def _cache_with_root(tmp_path: pathlib.Path) -> pathlib.Path:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    root = cache_dir / "apache-test-0.2"
    root.mkdir()
    return cache_dir


def _extract_test_archive(tmp_path: pathlib.Path) -> pathlib.Path:
    cache_dir = tmp_path / "headers_cache"
    cache_dir.mkdir()
    with tarfile.open(TEST_ARCHIVE) as tf:
        tf.extractall(cache_dir, filter="data")
    return cache_dir
