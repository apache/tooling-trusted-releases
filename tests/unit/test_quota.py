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
import re
from typing import Final

import atr.errors as errors
import atr.models.safe as safe
import atr.quota as quota
import atr.storage as storage

_TROUBLESHOOTING: Final = pathlib.Path(__file__).parents[2] / "atr" / "docs" / "troubleshooting.md"


def test_every_kind_has_a_troubleshooting_section():
    anchors = set(re.findall(r"^#{2,3} .+ \{#([\w-]+)\}$", _TROUBLESHOOTING.read_text(), flags=re.MULTILINE))
    assert {kind.value for kind in quota.Kind} <= anchors


def test_exceeded_error_carries_status_and_help_id():
    violation = quota.Violation(quota.Kind.RELEASE_TOO_LARGE, "Too large.")
    error = storage.QuotaExceededError(violation)
    assert str(error) == "Too large."
    assert errors.response_status_code(error) == 413
    assert errors.help_id(error) == "release-too-large"
    assert errors.help_url("release-too-large") == "/docs/troubleshooting#release-too-large"


def test_free_space_refused_below_the_minimum():
    assert quota.free_space_violation(100, 100) is None
    violation = quota.free_space_violation(99, 100)
    assert violation is not None
    assert violation.kind is quota.Kind.SERVER_LOW_ON_SPACE
    assert violation.status == 507


def test_project_bytes_over_the_limit():
    limits = quota.Limits(project_bytes=100)
    assert quota.project_bytes_violation(60, 40, limits) is None
    violation = quota.project_bytes_violation(60, 41, limits)
    assert violation is not None
    assert violation.kind is quota.Kind.PROJECT_TOO_LARGE
    assert violation.status == 413


def test_project_over_its_limit_can_still_make_a_revision_that_adds_nothing():
    assert quota.project_bytes_violation(500, 0, quota.Limits(project_bytes=100)) is None


def test_project_releases_refused_at_the_limit():
    limits = quota.Limits(project_releases=2)
    assert quota.project_releases_violation(1, limits) is None
    violation = quota.project_releases_violation(2, limits)
    assert violation is not None
    assert violation.kind is quota.Kind.PROJECT_TOO_MANY_RELEASES
    assert violation.status == 400


def test_release_at_its_limits_is_allowed():
    limits = quota.Limits(release_bytes=30, release_files=2)
    sizes = {safe.RelPath("a.tar.gz"): 10, safe.RelPath("a.tar.gz.asc"): 20}
    assert quota.release_violation(sizes, limits) is None


def test_release_over_bytes_limit():
    limits = quota.Limits(release_bytes=29, release_files=2)
    sizes = {safe.RelPath("a.tar.gz"): 10, safe.RelPath("a.tar.gz.asc"): 20}
    violation = quota.release_violation(sizes, limits)
    assert violation is not None
    assert violation.kind is quota.Kind.RELEASE_TOO_LARGE
    assert violation.status == 413


def test_release_over_file_limit():
    limits = quota.Limits(release_files=1)
    sizes = {safe.RelPath("a.tar.gz"): 10, safe.RelPath("a.tar.gz.asc"): 20}
    violation = quota.release_violation(sizes, limits)
    assert violation is not None
    assert violation.kind is quota.Kind.RELEASE_TOO_MANY_FILES
    assert violation.status == 400


def test_single_file_limit():
    limits = quota.Limits(release_bytes=quota.MAX_FILE_BYTES * 10)
    sizes = {safe.RelPath("a.tar.gz"): quota.MAX_FILE_BYTES + 1}
    violation = quota.release_violation(sizes, limits)
    assert violation is not None
    assert violation.kind is quota.Kind.FILE_TOO_LARGE
    assert violation.status == 413
    assert "a.tar.gz" in violation.message
    assert quota.release_violation({safe.RelPath("a.tar.gz"): quota.MAX_FILE_BYTES}, limits) is None
