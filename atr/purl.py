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

"""Package URLs for ASF releases, and for the packages they're distributed as elsewhere."""

from typing import Final

import packageurl

import atr.models.distribution as models_distribution
import atr.models.safe as safe
import atr.models.sql as sql

_DOMAIN: Final[str] = "apache.org"


def distribution(row: sql.Distribution) -> str | None:
    """The Package URL of a distribution in its platform's own type, or None if the platform has no type."""
    return _package(row.platform, row.owner_namespace, row.package, row.version)


def expected(row: sql.ExpectedDistribution) -> str | None:
    """The Package URL of an expected distribution, which has no version, or None if the platform has no type."""
    return _package(row.platform, row.owner_namespace, row.package, None)


def expected_package(text: str) -> models_distribution.ExpectedPackage:
    """The expected distribution package a Package URL specifies, using its version as the version pattern."""
    try:
        parsed = packageurl.PackageURL.from_string(text.strip())
    except ValueError as e:
        raise ValueError(f"{text} is not a Package URL: {e}") from e
    if not parsed.version:
        raise ValueError(f"{text} has no version. Add one, such as @{{{{VERSION}}}} for the release's version")
    if parsed.qualifiers or parsed.subpath:
        raise ValueError(f"{text} has qualifiers or a subpath, which ATR does not support")
    platform, namespace = _platform(parsed)
    return models_distribution.ExpectedPackage(
        platform=platform,
        owner_namespace=safe.OwnerNamespace(namespace) if namespace else None,
        package=safe.PackageName(parsed.name),
        version_pattern=parsed.version,
    )


def software_id(project_key: str, version: str | None = None) -> str:
    """The Package URL of a project, `pkg:software-id/apache.org/<project_key>`, or of one of its releases."""
    return f"pkg:software-id/{_DOMAIN}/{project_key}" + ("" if version is None else f"@{version}")


def _package(platform: sql.DistributionPlatform, owner_namespace: str, package: str, version: str | None) -> str | None:
    match platform:
        case sql.DistributionPlatform.ARTIFACT_HUB:
            return None
        case sql.DistributionPlatform.DOCKER_HUB:
            # Official images sit in Docker Hub's default namespace, which a Package URL leaves out
            official = owner_namespace == platform.value.default_owner_namespace
            return _render("docker", None if official else owner_namespace, package, version)
        case sql.DistributionPlatform.MAVEN:
            return _render("maven", owner_namespace, package, version)
        case sql.DistributionPlatform.NPM:
            return _render("npm", None, package, version)
        case sql.DistributionPlatform.NPM_SCOPED:
            return _render("npm", f"@{owner_namespace}", package, version)
        case sql.DistributionPlatform.PYPI:
            return _render("pypi", None, package, version)


def _platform(parsed: packageurl.PackageURL) -> tuple[sql.DistributionPlatform, str | None]:
    match parsed.type:
        case "docker":
            return sql.DistributionPlatform.DOCKER_HUB, parsed.namespace
        case "maven":
            return sql.DistributionPlatform.MAVEN, parsed.namespace
        case "npm" if parsed.namespace:
            return sql.DistributionPlatform.NPM_SCOPED, parsed.namespace.removeprefix("@")
        case "npm":
            return sql.DistributionPlatform.NPM, None
        case "pypi":
            return sql.DistributionPlatform.PYPI, None
    raise ValueError(f"ATR does not support pkg:{parsed.type} packages")


def _render(kind: str, namespace: str | None, name: str, version: str | None) -> str:
    # The library applies each type's own rules, such as PyPI's lower case and hyphens
    return packageurl.PackageURL(kind, namespace, name, version).to_string()
