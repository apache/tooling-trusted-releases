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

import atr.models.sql as sql
import atr.shared.distribution as distribution
import atr.storage as storage
import atr.storage.writers.distributions as distributions

_MAVEN = sql.DistributionPlatform.MAVEN


def test_outstanding_message_counts_rows_being_looked_for() -> None:
    rows = [
        _row("found", sql.DistributionStatus.FOUND),
        _row("ignored", sql.DistributionStatus.IGNORED),
        _row("staging-record", sql.DistributionStatus.FOUND, staging=True),
    ]
    assert distribution.outstanding_message(rows) == ""

    rows.append(_row("staged", sql.DistributionStatus.CHECKING, staging=True))
    assert distribution.outstanding_message(rows) == (
        "This release cannot be announced until 1 distribution is found or ignored."
    )


def test_summary_leaves_out_staging_records() -> None:
    rows = [
        _row("found", sql.DistributionStatus.FOUND),
        _row("ignored", sql.DistributionStatus.IGNORED),
        _row("waiting", sql.DistributionStatus.CHECKING),
        _row("staged", sql.DistributionStatus.CHECKING, staging=True),
        _row("staging-record", sql.DistributionStatus.FOUND, staging=True),
    ]
    assert distribution.summary(rows) == (
        "1 of 4 distributions found, 1 staged and not yet found, 1 not found, 1 ignored."
    )


def test_validate_version_pattern_accepts_known_tokens() -> None:
    distributions._validate_version_pattern(_MAVEN, "{{VERSION}}-jdk17")
    distributions._validate_version_pattern(_MAVEN, "{{MAJOR_VERSION}}.x-{{PROJECT_KEY}}")
    distributions._validate_version_pattern(sql.DistributionPlatform.DOCKER_HUB, "{{VERSION}}-jdk17")


def test_validate_version_pattern_rejects_docker_hub_tag_shared_between_releases() -> None:
    with pytest.raises(storage.AccessError):
        distributions._validate_version_pattern(sql.DistributionPlatform.DOCKER_HUB, "{{MAJOR_VERSION}}")


def test_validate_version_pattern_rejects_unknown_token() -> None:
    with pytest.raises(storage.AccessError):
        distributions._validate_version_pattern(_MAVEN, "{{REVISION}}")


def test_validate_version_pattern_rejects_unsafe_version() -> None:
    with pytest.raises(storage.AccessError):
        distributions._validate_version_pattern(_MAVEN, "{{VERSION}}/../x")


def test_version_for_fills_in_tokens() -> None:
    expected = sql.ExpectedDistribution(
        project_key="example",
        platform=sql.DistributionPlatform.DOCKER_HUB,
        package="example",
        version_pattern="{{MAJOR_VERSION}}-{{PROJECT_KEY}}-{{VERSION}}",
    )
    assert expected.version_for("10.1.63") == "10-example-10.1.63"


def test_version_for_same_as_release_by_default() -> None:
    expected = sql.ExpectedDistribution(
        project_key="example", platform=sql.DistributionPlatform.MAVEN, owner_namespace="org.example", package="example"
    )
    assert expected.version_for("10.1.63") == "10.1.63"


def _row(package: str, status: sql.DistributionStatus, *, staging: bool = False) -> sql.Distribution:
    return sql.Distribution(
        release_key="example-1.0",
        platform=sql.DistributionPlatform.PYPI,
        package=package,
        version="1.0",
        staging=staging,
        status=status,
    )
