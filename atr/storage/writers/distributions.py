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

# Removing this will cause circular imports
from __future__ import annotations

import datetime
import sqlite3

import aiohttp
import sqlalchemy.exc as exc

import atr.db as db
import atr.log as log
import atr.models.basic as basic
import atr.models.distribution as distribution
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.tasks.gha as gha
import atr.util as util


class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ):
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write.authorisation.asf_uid


class FoundationCommitter(GeneralPublic):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationCommitter, data: db.Session):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_name: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_name: str,
    ):
        super().__init__(write, write_as, data, committee_name)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
        self.__asf_uid = asf_uid
        self.__committee_name = committee_name

    async def automate(
        self,
        release_name: str,
        platform: sql.DistributionPlatform,
        committee_name: str,
        owner_namespace: str | None,
        project_name: str,
        version_name: str,
        phase: str,
        revision_number: str | None,
        package: str,
        version: str,
        staging: bool,
    ) -> sql.Task:
        dist_task = sql.Task(
            task_type=sql.TaskType.DISTRIBUTION_WORKFLOW,
            task_args=gha.DistributionWorkflow(
                name=release_name,
                namespace=owner_namespace or "",
                package=package,
                version=version,
                project_name=project_name,
                version_name=version_name,
                phase=phase,
                platform=platform.name,
                staging=staging,
                asf_uid=self.__asf_uid,
                committee_name=committee_name,
                arguments={},
            ).model_dump(),
            asf_uid=util.unwrap(self.__asf_uid),
            added=datetime.datetime.now(datetime.UTC),
            status=sql.TaskStatus.QUEUED,
            project_name=project_name,
            version_name=version_name,
            revision_number=revision_number,
        )
        self.__data.add(dist_task)
        await self.__data.commit()
        await self.__data.refresh(dist_task)
        return dist_task

    async def record(
        self,
        release_name: str,
        platform: sql.DistributionPlatform,
        owner_namespace: str | None,
        package: str,
        version: str,
        staging: bool,
        upload_date: datetime.datetime | None,
        api_url: str,
        web_url: str | None = None,
    ) -> tuple[sql.Distribution, bool]:
        distribution = sql.Distribution(
            platform=platform,
            release_name=release_name,
            owner_namespace=owner_namespace or "",
            package=package,
            version=version,
            staging=staging,
            upload_date=upload_date,
            api_url=api_url,
            web_url=web_url,
        )
        self.__data.add(distribution)
        try:
            await self.__data.commit()
        except exc.IntegrityError as e:
            # "The names and numeric values for existing result codes are fixed and unchanging."
            # https://www.sqlite.org/rescode.html
            # e.orig.sqlite_errorcode == 1555
            # e.orig.sqlite_errorname == "SQLITE_CONSTRAINT_PRIMARYKEY"
            match e.orig:
                # TODO: Document this
                case sqlite3.IntegrityError(sqlite_errorcode=sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY):
                    if not staging:
                        upgraded = await self.__upgrade_staging_to_final(
                            release_name,
                            platform,
                            owner_namespace,
                            package,
                            version,
                            upload_date,
                            api_url,
                            web_url,
                        )
                        if upgraded is not None:
                            return upgraded, False
                    return distribution, False
            raise e
        return distribution, True

    async def record_from_data(
        self,
        release_name: str,
        staging: bool,
        dd: distribution.Data,
    ) -> tuple[sql.Distribution, bool, distribution.Metadata]:
        template_url = await self.__template_url(dd, staging)
        api_url = template_url.format(
            owner_namespace=dd.owner_namespace,
            package=dd.package,
            version=dd.version,
        )
        if dd.platform == sql.DistributionPlatform.MAVEN:
            # We do this here because the CDNs break the namespace up into a / delimited URL
            owner = (dd.owner_namespace or "").replace(".", "/")
            api_url = template_url.format(
                owner_namespace=owner,
                package=dd.package,
                version=dd.version,
            )
            api_oc = await self.__json_from_maven_xml(api_url, dd.version)
        else:
            api_oc = await self.__json_from_distribution_platform(api_url, dd.platform, dd.version)
        match api_oc:
            case outcome.Result(result):
                pass
            case outcome.Error(error):
                log.error(f"Failed to get API response from {api_url}: {error}")
                raise storage.AccessError(f"Failed to get API response from distribution platform: {error}")
        upload_date = self.__distribution_upload_date(dd.platform, result, dd.version)
        if upload_date is None:
            raise storage.AccessError("Failed to get upload date from distribution platform")
        web_url = self.__distribution_web_url(dd.platform, result, dd.version)
        metadata = distribution.Metadata(
            api_url=api_url,
            result=result,
            upload_date=upload_date,
            web_url=web_url,
        )
        dist, added = await self.record(
            release_name=release_name,
            platform=dd.platform,
            owner_namespace=dd.owner_namespace,
            package=dd.package,
            version=dd.version,
            staging=staging,
            upload_date=upload_date,
            api_url=api_url,
            web_url=web_url,
        )
        return dist, added, metadata

    def __distribution_upload_date(  # noqa: C901
        self,
        platform: sql.DistributionPlatform,
        data: basic.JSON,
        version: str,
    ) -> datetime.datetime | None:
        match platform:
            case sql.DistributionPlatform.ARTIFACT_HUB:
                if not (versions := distribution.ArtifactHubResponse.model_validate(data).available_versions):
                    return None
                return datetime.datetime.fromtimestamp(versions[0].ts, tz=datetime.UTC)
            case sql.DistributionPlatform.DOCKER_HUB:
                if not (pushed_at := distribution.DockerResponse.model_validate(data).tag_last_pushed):
                    return None
                return datetime.datetime.fromisoformat(pushed_at.rstrip("Z"))
            # case sql.DistributionPlatform.GITHUB:
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

    def __distribution_web_url(  # noqa: C901
        self,
        platform: sql.DistributionPlatform,
        data: basic.JSON,
        version: str,
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
                    return f"https://artifacthub.io/packages/helm/{repo_name}/{pkg_name}/{version}"
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
            # case sql.DistributionPlatform.GITHUB:
            #     gh = GitHubResponse.model_validate(data)
            #     return gh.html_url
            case sql.DistributionPlatform.MAVEN:
                return None
            case sql.DistributionPlatform.NPM:
                nr = distribution.NpmResponse.model_validate(data)
                # return nr.homepage
                return f"https://www.npmjs.com/package/{nr.name}/v/{version}"
            case sql.DistributionPlatform.NPM_SCOPED:
                nr = distribution.NpmResponse.model_validate(data)
                # TODO: This is not correct
                return nr.homepage
            case sql.DistributionPlatform.PYPI:
                info = distribution.PyPIResponse.model_validate(data).info
                return info.release_url or info.project_url
        raise NotImplementedError(f"Platform {platform.name} is not yet supported")

    async def __json_from_distribution_platform(
        self, api_url: str, platform: sql.DistributionPlatform, version: str
    ) -> outcome.Outcome[basic.JSON]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    response.raise_for_status()
                    response_json = await response.json()
            result = basic.as_json(response_json)
        except aiohttp.ClientError as e:
            return outcome.Error(e)
        match platform:
            case sql.DistributionPlatform.NPM | sql.DistributionPlatform.NPM_SCOPED:
                if version not in distribution.NpmResponse.model_validate(result).time:
                    e = RuntimeError(f"Version '{version}' not found")
                    return outcome.Error(e)
        return outcome.Result(result)

    async def __json_from_maven_cdn(
        self, api_url: str, group_id: str, artifact_id: str, version: str
    ) -> outcome.Outcome[basic.JSON]:
        import datetime

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    response.raise_for_status()

            # Use current time as timestamp since we're just validating the package exists
            timestamp_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)

            # Convert to dict matching MavenResponse structure
            result_dict = {
                "response": {
                    "start": 0,
                    "docs": [
                        {
                            "g": group_id,
                            "a": artifact_id,
                            "v": version,
                            "timestamp": timestamp_ms,
                        }
                    ],
                }
            }
            result = basic.as_json(result_dict)
            return outcome.Result(result)
        except aiohttp.ClientError as e:
            return outcome.Error(e)

    async def __json_from_maven_xml(self, api_url: str, version: str) -> outcome.Outcome[basic.JSON]:
        import datetime
        import xml.etree.ElementTree as ET

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    response.raise_for_status()
                    xml_text = await response.text()

            # Parse the XML
            root = ET.fromstring(xml_text)

            # Extract versioning info
            group = root.find("groupId")
            artifact = root.find("artifactId")
            versioning = root.find("versioning")
            if versioning is None:
                e = RuntimeError("No versioning element found in Maven metadata")
                return outcome.Error(e)

            # Get lastUpdated timestamp (format: yyyyMMddHHmmss)
            last_updated_elem = versioning.find("lastUpdated")
            if (last_updated_elem is None) or (not last_updated_elem.text):
                e = RuntimeError("No lastUpdated timestamp found in Maven metadata")
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
                    e = RuntimeError(f"Version '{version}' not found in Maven metadata")
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
        except aiohttp.ClientError as e:
            return outcome.Error(e)
        except ET.ParseError as e:
            return outcome.Error(RuntimeError(f"Failed to parse Maven XML: {e}"))

    async def __template_url(
        self,
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
            raise storage.AccessError("Staging is currently supported only for ArtifactHub, PyPI and Maven Central.")

        template_url = dd.platform.value.template_staging_url
        if template_url is None:
            raise storage.AccessError("This platform does not provide a staging API endpoint.")

        return template_url

    async def __upgrade_staging_to_final(
        self,
        release_name: str,
        platform: sql.DistributionPlatform,
        owner_namespace: str | None,
        package: str,
        version: str,
        upload_date: datetime.datetime | None,
        api_url: str,
        web_url: str | None,
    ) -> sql.Distribution | None:
        tag = f"{release_name} {platform} {owner_namespace or ''} {package} {version}"
        existing = await self.__data.distribution(
            release_name=release_name,
            platform=platform,
            owner_namespace=(owner_namespace or ""),
            package=package,
            version=version,
        ).demand(RuntimeError(f"Distribution {tag} not found"))
        if existing.staging:
            existing.staging = False
            existing.upload_date = upload_date
            existing.api_url = api_url
            existing.web_url = web_url
            await self.__data.commit()
            return existing
        return None

    async def delete_distribution(
        self,
        release_name: str,
        platform: sql.DistributionPlatform,
        owner_namespace: str,
        package: str,
        version: str,
    ) -> None:
        distribution = await self.__data.distribution(
            release_name=release_name,
            platform=platform,
            owner_namespace=owner_namespace,
            package=package,
            version=version,
        ).demand(
            RuntimeError(f"Distribution {release_name} {platform} {owner_namespace} {package} {version} not found")
        )
        await self.__data.delete(distribution)
        await self.__data.commit()
