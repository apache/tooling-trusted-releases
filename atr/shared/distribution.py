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

import datetime
import enum
import urllib.parse

import aiohttp
import pydantic

import atr.db as db
import atr.form as form
import atr.htm as htm
import atr.models.basic as basic
import atr.models.distribution as distribution
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util
from atr.storage import outcome

# async def __json_from_maven_cdn(
#     self, api_url: str, group_id: str, artifact_id: str, version: str
# ) -> outcome.Outcome[models.basic.JSON]:
#     import datetime
#
#     try:
#         async with util.create_secure_session() as session:
#             async with session.get(api_url) as response:
#                 response.raise_for_status()
#
#         # Use current time as timestamp since we're just validating the package exists
#         timestamp_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
#
#         # Convert to dict matching MavenResponse structure
#         result_dict = {
#             "response": {
#                 "start": 0,
#                 "docs": [
#                     {
#                         "g": group_id,
#                         "a": artifact_id,
#                         "v": version,
#                         "timestamp": timestamp_ms,
#                     }
#                 ],
#             }
#         }
#         result = models.basic.as_json(result_dict)
#         return outcome.Result(result)
#     except aiohttp.ClientError as e:
#         return outcome.Error(e)


class DistributionError(RuntimeError): ...


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
    release_key: safe.ReleaseKey = form.label("Release name", widget=form.Widget.HIDDEN)
    platform: form.Enum[DistributionPlatform] = form.label("Platform", widget=form.Widget.HIDDEN)
    owner_namespace: str = form.label("Owner namespace", widget=form.Widget.HIDDEN)
    package: str = form.label("Package", widget=form.Widget.HIDDEN)
    version: str = form.label("Version", widget=form.Widget.HIDDEN)


class DistributionAutomateForm(form.Form):
    platform: form.Enum[DistributionPlatform] = form.label(
        "Platform", widget=form.Widget.SELECT, enum_filter_include=[DistributionPlatform.MAVEN.value]
    )
    owner_namespace: safe.OptionalAlphanumeric = form.label(
        "Owner or Namespace",
        "Who owns or names the package (Maven groupId, npm @scope, Docker namespace, "
        "GitHub owner, ArtifactHub repo). Leave blank if not used.",
    )
    package: safe.Alphanumeric = form.label("Package")
    version: safe.VersionKey = form.label("Version")
    details: form.Bool = form.label(
        "Include details",
        "Include the details of the distribution in the response",
    )

    @pydantic.model_validator(mode="after")
    def validate_owner_namespace(self) -> DistributionAutomateForm:
        sql_platform = self.platform.to_sql()  # type: ignore[attr-defined]
        default_owner_namespace = sql_platform.value.default_owner_namespace

        if default_owner_namespace and (not self.owner_namespace):
            self.owner_namespace = default_owner_namespace

        util.validate_distribution_owner_namespace(sql_platform, self.owner_namespace)

        return self


class DistributionRecordForm(form.Form):
    platform: form.Enum[DistributionPlatform] = form.label("Platform", widget=form.Widget.SELECT)
    owner_namespace: safe.OptionalAlphanumeric = form.label(
        "Owner or Namespace",
        "Who owns or names the package (Maven groupId, npm @scope, Docker namespace, "
        "GitHub owner, ArtifactHub repo). Leave blank if not used.",
    )
    package: safe.Alphanumeric = form.label("Package")
    version: safe.VersionKey = form.label("Version")
    details: form.Bool = form.label(
        "Include details",
        "Include the details of the distribution in the response",
    )

    @pydantic.model_validator(mode="after")
    def validate_owner_namespace(self) -> DistributionRecordForm:
        sql_platform = self.platform.to_sql()  # type: ignore[attr-defined]
        default_owner_namespace = sql_platform.value.default_owner_namespace

        if default_owner_namespace and (not self.owner_namespace):
            self.owner_namespace = default_owner_namespace

        util.validate_distribution_owner_namespace(sql_platform, self.owner_namespace)

        return self


def distribution_upload_date(  # noqa: C901
    platform: sql.DistributionPlatform,
    data: basic.JSON,
    version_key: safe.VersionKey,
) -> datetime.datetime | None:
    version = str(version_key)
    match platform:
        case sql.DistributionPlatform.ARTIFACT_HUB:
            if not (versions := distribution.ArtifactHubResponse.model_validate(data).available_versions):
                return None
            return datetime.datetime.fromtimestamp(versions[0].ts, tz=datetime.UTC)
        case sql.DistributionPlatform.DOCKER_HUB:
            if not (pushed_at := distribution.DockerResponse.model_validate(data).tag_last_pushed):
                return None
            return datetime.datetime.fromisoformat(pushed_at.rstrip("Z"))
        # case models.sql.DistributionPlatform.GITHUB:
        #     if not (published_at := GitHubResponse.model_validate(data).published_at):
        #         return None
        #     return datetime.datetime.fromisoformat(published_at.rstrip("Z"))
        case sql.DistributionPlatform.MAVEN:
            m = distribution.MavenResponse.model_validate(data)
            docs = m.response.docs
            if not docs:
                return None
            timestamp = docs[0].timestamp
            if not timestamp:
                return None
            return datetime.datetime.fromtimestamp(timestamp / 1000, tz=datetime.UTC)
        case sql.DistributionPlatform.NPM | sql.DistributionPlatform.NPM_SCOPED:
            if not (times := distribution.NpmResponse.model_validate(data).time):
                return None
            # Versions can be in the form "1.2.3" or "v1.2.3", so we check for both
            if not (upload_time := times.get(version) or times.get(f"v{version}")):
                return None
            return datetime.datetime.fromisoformat(upload_time.rstrip("Z"))
        case sql.DistributionPlatform.PYPI:
            if not (urls := distribution.PyPIResponse.model_validate(data).urls):
                return None
            if not (upload_time := urls[0].upload_time_iso_8601):
                return None
            return datetime.datetime.fromisoformat(upload_time.rstrip("Z"))
    raise NotImplementedError(f"Platform {platform.name} is not yet supported")


def distribution_web_url(  # noqa: C901
    platform: sql.DistributionPlatform,
    data: basic.JSON,
    version: safe.VersionKey,
) -> str | None:
    match platform:
        case sql.DistributionPlatform.ARTIFACT_HUB:
            ah = distribution.ArtifactHubResponse.model_validate(data)
            repo_name = ah.repository.name if ah.repository else None
            pkg_name = ah.name
            ver = ah.version
            if repo_name and pkg_name:
                if ver:
                    return f"https://artifacthub.io/packages/helm/{repo_name}/{pkg_name}/{ver}"
                return f"https://artifacthub.io/packages/helm/{repo_name}/{pkg_name}/{version!s}"
            if ah.home_url:
                return ah.home_url
            for link in ah.links:
                if link.url:
                    return link.url
            return None
        case sql.DistributionPlatform.DOCKER_HUB:
            # The best we can do on Docker Hub is:
            # f"https://hub.docker.com/_/{package}"
            return None
        # case models.sql.DistributionPlatform.GITHUB:
        #     gh = GitHubResponse.model_validate(data)
        #     return gh.html_url
        case sql.DistributionPlatform.MAVEN:
            return None
        case sql.DistributionPlatform.NPM:
            nr = distribution.NpmResponse.model_validate(data)
            # return nr.homepage
            return f"https://www.npmjs.com/package/{nr.name}/v/{version!s}"
        case sql.DistributionPlatform.NPM_SCOPED:
            nr = distribution.NpmResponse.model_validate(data)
            # TODO: This is not correct
            return nr.homepage
        case sql.DistributionPlatform.PYPI:
            info = distribution.PyPIResponse.model_validate(data).info
            return info.release_url or info.project_url
    raise NotImplementedError(f"Platform {platform.name} is not yet supported")


def get_api_url(dd: distribution.Data, staging: bool | None = None):
    namespace = str(dd.owner_namespace) if dd.owner_namespace else None
    template_url = _template_url(dd, staging)
    package = urllib.parse.quote(str(dd.package))
    version = urllib.parse.quote(str(dd.version))
    api_url = template_url.format(
        owner_namespace=namespace,
        package=package,
        version=version,
    )
    if dd.platform == sql.DistributionPlatform.MAVEN:
        # We do this here because the CDNs break the namespace up into a / delimited URL
        owner = (namespace or "").replace(".", "/")
        api_url = template_url.format(
            owner_namespace=owner,
            package=package,
            version=version,
        )
    return api_url


def html_submitted_values_table(block: htm.Block, dd: distribution.Data) -> None:
    namespace = str(dd.owner_namespace) if dd.owner_namespace else "-"
    tbody = htm.tbody[
        html_tr("Platform", dd.platform.name),
        html_tr("Owner or Namespace", namespace),
        html_tr("Package", str(dd.package)),
        html_tr("Version", str(dd.version)),
    ]
    block.table(".table.table-striped.table-bordered")[tbody]


def html_tr(label: str, value: str) -> htm.Element:
    return htm.tr[htm.th[label], htm.td[value]]


def html_tr_a(label: str, value: str | None) -> htm.Element:
    return htm.tr[htm.th[label], htm.td[htm.a(href=value)[value] if value else "-"]]


async def json_from_distribution_platform(
    api_url: str, platform: sql.DistributionPlatform, version_key: safe.VersionKey
) -> outcome.Outcome[basic.JSON]:
    version = str(version_key)
    try:
        async with util.create_secure_session() as session:
            async with session.get(api_url) as response:
                response.raise_for_status()
                response_json = await response.json()
        result = basic.as_json(response_json)
    except aiohttp.ClientError as e:
        return outcome.Error(e)
    match platform:
        case sql.DistributionPlatform.NPM | sql.DistributionPlatform.NPM_SCOPED:
            if version not in distribution.NpmResponse.model_validate(result).time:
                e = DistributionError(f"Version '{version}' not found")
                return outcome.Error(e)
    return outcome.Result(result)


async def json_from_maven_xml(api_url: str, version_key: safe.VersionKey) -> outcome.Outcome[basic.JSON]:
    import datetime

    import defusedxml.ElementTree as ElementTree

    version = str(version_key)
    try:
        async with util.create_secure_session() as session:
            async with session.get(api_url) as response:
                response.raise_for_status()
                xml_text = await response.text()

        # Parse the XML
        root = ElementTree.fromstring(xml_text)

        # Extract versioning info
        group = root.find("groupId")
        artifact = root.find("artifactId")
        versioning = root.find("versioning")
        if versioning is None:
            e = DistributionError("No versioning element found in Maven metadata")
            return outcome.Error(e)

        # Get lastUpdated timestamp (format: yyyyMMddHHmmss)
        last_updated_elem = versioning.find("lastUpdated")
        if (last_updated_elem is None) or (not last_updated_elem.text):
            e = DistributionError("No lastUpdated timestamp found in Maven metadata")
            return outcome.Error(e)

        # Convert lastUpdated string to Unix timestamp in milliseconds
        last_updated_str = last_updated_elem.text
        dt = datetime.datetime.strptime(last_updated_str, "%Y%m%d%H%M%S")
        dt = dt.replace(tzinfo=datetime.UTC)
        timestamp_ms = int(dt.timestamp() * 1000)

        # Verify the version exists
        versions_elem = versioning.find("versions")
        if versions_elem is not None:
            versions = [v.text for v in versions_elem.findall("version") if v.text]
            if version not in versions:
                e = DistributionError(f"Version '{version}' not found in Maven metadata")
                return outcome.Error(e)

        # Convert to dict matching MavenResponse structure
        result_dict = {
            "response": {
                "start": 0,
                "docs": [
                    {
                        "g": group.text if (group is not None) else "",
                        "a": artifact.text if (artifact is not None) else "",
                        "v": version,
                        "timestamp": timestamp_ms,
                    }
                ],
            }
        }
        result = basic.as_json(result_dict)
        return outcome.Result(result)
    except (aiohttp.ClientError, DistributionError) as e:
        return outcome.Error(e)
    except ElementTree.ParseError as e:
        return outcome.Error(RuntimeError(f"Failed to parse Maven XML: {e}"))


async def release_validated(
    project: safe.ProjectKey,
    version: safe.VersionKey,
    committee: bool = False,
    staging: bool | None = None,
    release_policy: bool = False,
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
            project_key=str(project),
            version=str(version),
            _committee=committee,
            _release_policy=release_policy,
        ).demand(RuntimeError(f"Release {project!s} {version!s} not found"))
        if release.phase not in phase:
            raise RuntimeError(f"Release {project!s} {version!s} is not in {phase}")
        # if release.project.status != sql.ProjectStatus.ACTIVE:
        #     raise RuntimeError(f"Project {project} is not active")
    return release


async def release_validated_and_committee(
    project: safe.ProjectKey, version: safe.VersionKey, *, staging: bool | None = None, release_policy: bool = False
) -> tuple[sql.Release, sql.Committee]:
    release = await release_validated(project, version, committee=True, staging=staging, release_policy=release_policy)
    committee = release.committee
    if committee is None:
        raise RuntimeError(f"Release {project} {version} has no committee")
    return release, committee


def _template_url(
    dd: distribution.Data,
    staging: bool | None = None,
) -> str:
    if staging is False:
        return dd.platform.value.template_url

    supported = {
        sql.DistributionPlatform.ARTIFACT_HUB,
        sql.DistributionPlatform.PYPI,
        sql.DistributionPlatform.MAVEN,
    }
    if dd.platform not in supported:
        raise RuntimeError("Staging is currently supported only for ArtifactHub, PyPI and Maven Central.")

    template_url = dd.platform.value.template_staging_url
    if template_url is None:
        raise RuntimeError("This platform does not provide a staging API endpoint.")

    return template_url
