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

import atr.db as db
import atr.log as log
import atr.models as models
import atr.models.args as args
import atr.shared.distribution as distribution
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.util as util


def _distribution_api_error(dd: models.distribution.Data, error: Exception) -> storage.AccessError:
    if dd.platform == models.sql.DistributionPlatform.MAVEN:
        return storage.AccessError(
            "Failed to find the Maven artifact. Check that Owner or Namespace is the Maven groupId "
            "(for example, org.apache.maven), and that the package artifactId and version are correct. "
            f"You can verify Maven coordinates on https://search.maven.org. Original error: {error}",
            status=502,
        )

    return storage.AccessError(f"Failed to get API response from distribution platform: {error}", status=502)


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
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def automate(
        self,
        release_key: models.safe.ReleaseKey,
        platform: models.sql.DistributionPlatform,
        committee_key: str,
        owner_namespace: models.safe.OwnerNamespace | None,
        project_key: models.safe.ProjectKey,
        version_key: models.safe.VersionKey,
        phase: str,
        revision_number: str | None,
        package: models.safe.Alphanumeric,
        version: models.safe.VersionKey,
        staging: bool,
    ) -> models.sql.Task:
        project = await self.__data.project(key=str(project_key)).demand(
            storage.AccessError(f"Project '{project_key}' not found.")
        )
        storage.ensure_project_active(project)
        dist_task = models.sql.Task(
            task_type=models.sql.TaskType.DISTRIBUTION_WORKFLOW,
            task_args=args.DistributionWorkflow(
                name=str(release_key),
                namespace=str(owner_namespace) if owner_namespace else "",
                package=package,
                version=version,
                project_key=project_key,
                version_key=version_key,
                phase=phase,
                platform=platform.name,
                staging=staging,
                asf_uid=self.__asf_uid,
                committee_key=committee_key,
                arguments={},
            ).model_dump(),
            asf_uid=util.unwrap(self.__asf_uid),
            added=datetime.datetime.now(datetime.UTC),
            status=models.sql.TaskStatus.QUEUED,
            project_key=str(project_key),
            version_key=str(version_key),
            revision_number=revision_number,
        )
        self.__data.add(dist_task)
        await self.__data.commit()
        await self.__data.refresh(dist_task)
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_automate",
            release_key=str(release_key),
            platform=platform.name,
        )
        return dist_task

    async def record(
        self,
        release_key: models.safe.ReleaseKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: models.safe.OwnerNamespace | None,
        package: models.safe.Alphanumeric,
        version: models.safe.VersionKey,
        staging: bool,
        pending: bool,
        upload_date: datetime.datetime | None,
        api_url: str | None = None,
        web_url: str | None = None,
    ) -> tuple[models.sql.Distribution, bool]:
        release = await self.__data.release(key=str(release_key), _project=True).demand(
            storage.AccessError(f"Release '{release_key}' not found.")
        )
        storage.ensure_project_active(release.project)
        namespace = str(owner_namespace) if owner_namespace else ""
        existing = await self.__data.distribution(
            str(release_key), platform, namespace, str(package), str(version)
        ).get()
        dist = models.sql.Distribution(
            platform=platform,
            release_key=str(release_key),
            owner_namespace=namespace,
            package=str(package),
            version=str(version),
            staging=staging,
            pending=pending,
            retries=0,
            upload_date=upload_date,
            api_url=api_url,
            web_url=web_url,
            created_by=self.__asf_uid,
        )
        if existing is None:
            self.__data.add(dist)
            await self.__data.commit()
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                operation="distribution_record",
                release_key=str(release_key),
                platform=platform.name,
            )
            return dist, True
        # If we're doing production and existing was for staging, upgrade it
        if (not staging) and existing.staging:
            upgraded = await self.__upgrade_staging_to_final(
                release_key,
                platform,
                namespace,
                str(package),
                str(version),
                pending,
                upload_date,
                api_url,
                web_url,
            )
            if upgraded is not None:
                self.__write_as.append_to_audit_log(
                    asf_uid=self.__asf_uid,
                    operation="distribution_upgrade",
                    release_key=str(release_key),
                    platform=platform.name,
                )
                return upgraded, False
        if existing.pending:
            if pending:
                existing.retries = existing.retries + 1
                await self.__data.commit()
                return existing, False
            else:
                existing.pending = False
                await self.__data.commit()
                return existing, False
        return dist, False

    async def record_from_data(
        self,
        release_key: models.safe.ReleaseKey,
        staging: bool,
        dd: models.distribution.Data,
        allow_retries: bool = False,
    ) -> tuple[models.sql.Distribution, bool, models.distribution.Metadata | None]:
        release = await self.__data.release(key=str(release_key), _project=True).demand(
            storage.AccessError(f"Release '{release_key}' not found.")
        )
        storage.ensure_project_active(release.project)
        api_url = distribution.get_api_url(dd, staging)
        if dd.platform == models.sql.DistributionPlatform.MAVEN:
            api_oc = await distribution.json_from_maven_xml(api_url, dd.version)
        else:
            api_oc = await distribution.json_from_distribution_platform(api_url, dd.platform, dd.version)
        match api_oc:
            case outcome.Result(result):
                pass
            case outcome.Error(error):
                log.error(f"Failed to get API response from {api_url}: {error}")
                if allow_retries:
                    dist, added = await self.record(
                        release_key=release_key,
                        platform=dd.platform,
                        owner_namespace=dd.owner_namespace,
                        package=dd.package,
                        version=dd.version,
                        staging=staging,
                        pending=True,
                        upload_date=None,
                        api_url=None,
                        web_url=None,
                    )
                    return dist, added, None
                raise _distribution_api_error(dd, error)
        upload_date = distribution.distribution_upload_date(dd.platform, result, dd.version)
        if upload_date is None:
            raise storage.AccessError("Failed to get upload date from distribution platform", status=502)
        web_url = distribution.distribution_web_url(dd.platform, result, dd.version)
        metadata = models.distribution.Metadata(
            api_url=api_url,
            result=result,
            upload_date=upload_date,
            web_url=web_url,
        )
        dist, added = await self.record(
            release_key=release_key,
            platform=dd.platform,
            owner_namespace=dd.owner_namespace,
            package=dd.package,
            version=dd.version,
            staging=staging,
            pending=False,
            upload_date=upload_date,
            api_url=api_url,
            web_url=web_url,
        )
        return dist, added, metadata

    async def __upgrade_staging_to_final(
        self,
        release_key: models.safe.ReleaseKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: str | None,
        package: str,
        version: str,
        pending: bool,
        upload_date: datetime.datetime | None,
        api_url: str | None,
        web_url: str | None,
    ) -> models.sql.Distribution | None:
        tag = f"{release_key} {platform} {owner_namespace or ''} {package} {version}"
        existing = await self.__data.distribution(
            release_key=str(release_key),
            platform=platform,
            owner_namespace=(owner_namespace or ""),
            package=package,
            version=version,
        ).demand(RuntimeError(f"Distribution {tag} not found"))
        if existing.staging:
            existing.staging = False
            existing.pending = pending
            existing.upload_date = upload_date
            existing.api_url = api_url
            existing.web_url = web_url
            existing.created_by = self.__asf_uid
            await self.__data.commit()
            return existing
        return None

    async def delete_distribution(
        self,
        release_key: models.safe.ReleaseKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: str,
        package: str,
        version: str,
    ) -> None:
        release = await self.__data.release(key=str(release_key), _project=True).demand(
            storage.AccessError(f"Release '{release_key}' not found.")
        )
        storage.ensure_project_active(release.project)
        distribution = await self.__data.distribution(
            release_key=str(release_key),
            platform=platform,
            owner_namespace=owner_namespace,
            package=package,
            version=version,
        ).demand(RuntimeError(f"Distribution {release_key} {platform} {owner_namespace} {package} {version} not found"))
        await self.__data.delete(distribution)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_delete",
            release_key=str(release_key),
            platform=platform.name,
        )
