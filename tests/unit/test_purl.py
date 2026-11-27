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


def test_expected_distribution_has_no_version() -> None:
    row = sql.ExpectedDistribution(
        project_key="example",
        platform=sql.DistributionPlatform.MAVEN,
        owner_namespace="org.apache.example",
        package="example-core",
    )
    assert purl.expected(row) == "pkg:maven/org.apache.example/example-core"


def test_expected_distribution_without_a_platform_type() -> None:
    row = sql.ExpectedDistribution(
        project_key="example",
        platform=sql.DistributionPlatform.ARTIFACT_HUB,
        owner_namespace="apache",
        package="example",
    )
    assert purl.expected(row) is None


def test_software_id_names_a_project_or_one_release() -> None:
    assert purl.software_id("example") == "pkg:software-id/apache.org/example"
    assert purl.software_id("example", "1.0.0") == "pkg:software-id/apache.org/example@1.0.0"


@pytest.mark.parametrize(
    ("text", "platform", "owner_namespace", "package", "pattern"),
    [
        ("pkg:docker/httpd@{{VERSION}}", sql.DistributionPlatform.DOCKER_HUB, None, "httpd", "{{VERSION}}"),
        (
            "pkg:docker/apache/example@{{VERSION}}-jdk17",
            sql.DistributionPlatform.DOCKER_HUB,
            "apache",
            "example",
            "{{VERSION}}-jdk17",
        ),
        (
            "pkg:maven/org.apache.example/example-core@{{VERSION}}",
            sql.DistributionPlatform.MAVEN,
            "org.apache.example",
            "example-core",
            "{{VERSION}}",
        ),
        (
            "pkg:maven/org.apache.logging.log4j/log4j-1.2-api@{{VERSION}}",
            sql.DistributionPlatform.MAVEN,
            "org.apache.logging.log4j",
            "log4j-1.2-api",
            "{{VERSION}}",
        ),
        ("pkg:npm/example@%7B%7BVERSION%7D%7D", sql.DistributionPlatform.NPM, None, "example", "{{VERSION}}"),
        (
            "pkg:npm/%40apache-example/core@{{MAJOR_VERSION}}.0",
            sql.DistributionPlatform.NPM_SCOPED,
            "apache-example",
            "core",
            "{{MAJOR_VERSION}}.0",
        ),
        ("pkg:npm/@apache-example/core@2.0", sql.DistributionPlatform.NPM_SCOPED, "apache-example", "core", "2.0"),
        (" pkg:pypi/Apache_Example@{{VERSION}} ", sql.DistributionPlatform.PYPI, None, "apache-example", "{{VERSION}}"),
    ],
)
def test_expected_package_names_its_platform(
    text: str, platform: sql.DistributionPlatform, owner_namespace: str | None, package: str, pattern: str
) -> None:
    found = purl.expected_package(text)
    namespace = None if (found.owner_namespace is None) else str(found.owner_namespace)
    assert (found.platform, namespace, str(found.package), found.version_pattern) == (
        platform,
        owner_namespace,
        package,
        pattern,
    )


@pytest.mark.parametrize(
    "text",
    [
        "maven/org.apache.example/example-core@{{VERSION}}",
        "pkg:maven/org.apache.example/example-core",
        "pkg:maven/org.apache.example/example-core@{{VERSION}}?type=jar",
        "pkg:maven/org.apache.example/example-core@{{VERSION}}#sub",
        "pkg:github/apache/example@{{VERSION}}",
        "pkg:maven/org.apache.example/..@{{VERSION}}",
    ],
)
def test_expected_package_refuses(text: str) -> None:
    with pytest.raises(ValueError):
        purl.expected_package(text)


def test_expected_package_reads_what_expected_writes() -> None:
    for platform, owner_namespace in [
        (sql.DistributionPlatform.DOCKER_HUB, "library"),
        (sql.DistributionPlatform.MAVEN, "org.apache.example"),
        (sql.DistributionPlatform.NPM_SCOPED, "apache-example"),
    ]:
        row = sql.ExpectedDistribution(
            project_key="example", platform=platform, owner_namespace=owner_namespace, package="core"
        )
        text = purl.expected(row)
        assert text is not None
        found = purl.expected_package(f"{text}@{{{{VERSION}}}}")
        # Docker Hub's default namespace is left out of the Package URL, and filled in again when added
        assert found.platform == platform
        assert str(found.owner_namespace or platform.value.default_owner_namespace) == owner_namespace
        assert str(found.package) == "core"
        assert found.version_pattern == "{{VERSION}}"
