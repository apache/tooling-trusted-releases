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

from __future__ import annotations

import enum

import pydantic

import atr.db as db
import atr.form as form
import atr.htm as htm
import atr.models.distribution as distribution
import atr.models.sql as sql


class DistributionPlatform(enum.Enum):
    """Wrapper enum for distribution platforms."""

    ARTIFACT_HUB = "Artifact Hub"
    DOCKER_HUB = "Docker Hub"
    MAVEN = "Maven Central"
    NPM = "npm"
    NPM_SCOPED = "npm (scoped)"
    PYPI = "PyPI"

    def to_sql(self) -> sql.DistributionPlatform:
        """Convert to SQL enum."""
        match self:
            case DistributionPlatform.ARTIFACT_HUB:
                return sql.DistributionPlatform.ARTIFACT_HUB
            case DistributionPlatform.DOCKER_HUB:
                return sql.DistributionPlatform.DOCKER_HUB
            case DistributionPlatform.MAVEN:
                return sql.DistributionPlatform.MAVEN
            case DistributionPlatform.NPM:
                return sql.DistributionPlatform.NPM
            case DistributionPlatform.NPM_SCOPED:
                return sql.DistributionPlatform.NPM_SCOPED
            case DistributionPlatform.PYPI:
                return sql.DistributionPlatform.PYPI

    @classmethod
    def from_sql(cls, platform: sql.DistributionPlatform) -> DistributionPlatform:
        """Convert from SQL enum."""
        match platform:
            case sql.DistributionPlatform.ARTIFACT_HUB:
                return cls.ARTIFACT_HUB
            case sql.DistributionPlatform.DOCKER_HUB:
                return cls.DOCKER_HUB
            case sql.DistributionPlatform.MAVEN:
                return cls.MAVEN
            case sql.DistributionPlatform.NPM:
                return cls.NPM
            case sql.DistributionPlatform.NPM_SCOPED:
                return cls.NPM_SCOPED
            case sql.DistributionPlatform.PYPI:
                return cls.PYPI


class DeleteForm(form.Form):
    release_name: str = form.label("Release name", widget=form.Widget.HIDDEN)
    platform: form.Enum[DistributionPlatform] = form.label("Platform", widget=form.Widget.HIDDEN)
    owner_namespace: str = form.label("Owner namespace", widget=form.Widget.HIDDEN)
    package: str = form.label("Package", widget=form.Widget.HIDDEN)
    version: str = form.label("Version", widget=form.Widget.HIDDEN)


class DistributeForm(form.Form):
    platform: form.Enum[DistributionPlatform] = form.label("Platform", widget=form.Widget.SELECT)
    owner_namespace: str = form.label(
        "Owner or Namespace",
        "Who owns or names the package (Maven groupId, npm @scope, Docker namespace, "
        "GitHub owner, ArtifactHub repo). Leave blank if not used.",
    )
    package: str = form.label("Package")
    version: str = form.label("Version")
    details: form.Bool = form.label(
        "Include details",
        "Include the details of the distribution in the response",
    )

    @pydantic.model_validator(mode="after")
    def validate_owner_namespace(self) -> DistributeForm:
        platform_name: str = self.platform.name  # type: ignore[attr-defined]
        sql_platform = self.platform.to_sql()  # type: ignore[attr-defined]
        default_owner_namespace = sql_platform.value.default_owner_namespace
        requires_owner_namespace = sql_platform.value.requires_owner_namespace

        if default_owner_namespace and (not self.owner_namespace):
            self.owner_namespace = default_owner_namespace

        if requires_owner_namespace and (not self.owner_namespace):
            raise ValueError(f'Platform "{platform_name}" requires an owner or namespace.')

        if (not requires_owner_namespace) and (not default_owner_namespace) and self.owner_namespace:
            raise ValueError(f'Platform "{platform_name}" does not require an owner or namespace.')

        return self


def html_submitted_values_table(block: htm.Block, dd: distribution.Data) -> None:
    tbody = htm.tbody[
        html_tr("Platform", dd.platform.name),
        html_tr("Owner or Namespace", dd.owner_namespace or "-"),
        html_tr("Package", dd.package),
        html_tr("Version", dd.version),
    ]
    block.table(".table.table-striped.table-bordered")[tbody]


def html_tr(label: str, value: str) -> htm.Element:
    return htm.tr[htm.th[label], htm.td[value]]


def html_tr_a(label: str, value: str | None) -> htm.Element:
    return htm.tr[htm.th[label], htm.td[htm.a(href=value)[value] if value else "-"]]


async def release_validated(
    project: str, version: str, committee: bool = False, staging: bool | None = None, release_policy: bool = False
) -> sql.Release:
    match staging:
        case True:
            phase = {sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}
        case False:
            phase = {sql.ReleasePhase.RELEASE_PREVIEW}
        case None:
            phase = {sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, sql.ReleasePhase.RELEASE_PREVIEW}
    async with db.session() as data:
        release = await data.release(
            project_name=project,
            version=version,
            _committee=committee,
            _release_policy=release_policy,
        ).demand(RuntimeError(f"Release {project} {version} not found"))
        if release.phase not in phase:
            raise RuntimeError(f"Release {project} {version} is not in {phase}")
        # if release.project.status != sql.ProjectStatus.ACTIVE:
        #     raise RuntimeError(f"Project {project} is not active")
    return release


async def release_validated_and_committee(
    project: str, version: str, *, staging: bool | None = None, release_policy: bool = False
) -> tuple[sql.Release, sql.Committee]:
    release = await release_validated(project, version, committee=True, staging=staging, release_policy=release_policy)
    committee = release.committee
    if committee is None:
        raise RuntimeError(f"Release {project} {version} has no committee")
    return release, committee
