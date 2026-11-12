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
import atr.purl as purl


@pytest.mark.parametrize(
    ("platform", "owner_namespace", "package", "version", "expected"),
    [
        (sql.DistributionPlatform.ARTIFACT_HUB, "apache", "example", "1.0.0", None),
        (sql.DistributionPlatform.DOCKER_HUB, "apache", "example", "v1.0.0", "pkg:docker/apache/example@v1.0.0"),
        (sql.DistributionPlatform.DOCKER_HUB, "library", "httpd", "2.4", "pkg:docker/httpd@2.4"),
        (
            sql.DistributionPlatform.MAVEN,
            "org.apache.example",
            "example-core",
            "1.0.0",
            "pkg:maven/org.apache.example/example-core@1.0.0",
        ),
        (sql.DistributionPlatform.NPM, "", "example", "1.0.0", "pkg:npm/example@1.0.0"),
        (
            sql.DistributionPlatform.NPM_SCOPED,
            "apache-example",
            "core",
            "1.0.0",
            "pkg:npm/%40apache-example/core@1.0.0",
        ),
        (sql.DistributionPlatform.PYPI, "", "Apache_Example", "1.0.0", "pkg:pypi/apache-example@1.0.0"),
    ],
)
def test_distribution_is_named_in_its_platform_type(
    platform: sql.DistributionPlatform, owner_namespace: str, package: str, version: str, expected: str | None
) -> None:
    row = sql.Distribution(
        release_key=f"example-{version}",
        platform=platform,
        owner_namespace=owner_namespace,
        package=package,
        version=version,
    )
    assert purl.distribution(row) == expected


def test_software_id_names_a_project_or_one_release() -> None:
    assert purl.software_id("example") == "pkg:software-id/apache.org/example"
    assert purl.software_id("example", "1.0.0") == "pkg:software-id/apache.org/example@1.0.0"
