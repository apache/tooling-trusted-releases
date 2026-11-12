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

import atr.models.sql as sql

_DOMAIN: Final[str] = "apache.org"


def distribution(row: sql.Distribution) -> str | None:
    """The Package URL of a distribution in its platform's own type, or None if the platform has no type."""
    match row.platform:
        case sql.DistributionPlatform.ARTIFACT_HUB:
            return None
        case sql.DistributionPlatform.DOCKER_HUB:
            # Official images sit in Docker Hub's default namespace, which a Package URL leaves out
            official = row.owner_namespace == row.platform.value.default_owner_namespace
            return _render("docker", None if official else row.owner_namespace, row.package, row.version)
        case sql.DistributionPlatform.MAVEN:
            return _render("maven", row.owner_namespace, row.package, row.version)
        case sql.DistributionPlatform.NPM:
            return _render("npm", None, row.package, row.version)
        case sql.DistributionPlatform.NPM_SCOPED:
            return _render("npm", f"@{row.owner_namespace}", row.package, row.version)
        case sql.DistributionPlatform.PYPI:
            return _render("pypi", None, row.package, row.version)


def software_id(project_key: str, version: str | None = None) -> str:
    """The Package URL of a project, `pkg:software-id/apache.org/<project_key>`, or of one of its releases."""
    return f"pkg:software-id/{_DOMAIN}/{project_key}" + ("" if version is None else f"@{version}")


def _render(kind: str, namespace: str | None, name: str, version: str) -> str:
    # The library applies each type's own rules, such as PyPI's lower case and hyphens
    return packageurl.PackageURL(kind, namespace, name, version).to_string()
