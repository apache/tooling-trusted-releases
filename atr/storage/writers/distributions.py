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
from typing import TYPE_CHECKING, Final

import sqlmodel

import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.log as log
import atr.models as models
import atr.models.args as args
import atr.shared.distribution as distribution
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.util as util

if TYPE_CHECKING:
    from collections.abc import Sequence

# A row no release in finish is waiting on is deleted once it's been looked for this many times
_RETRY_LIMIT: Final[int] = 5
_VERSION_PATTERN_TOKEN_NAMES: Final[frozenset[str]] = frozenset({"MAJOR_VERSION", "PROJECT_KEY", "VERSION"})


def _distribution_api_error(dd: models.distribution.Data, error: Exception) -> storage.AccessError:
    if dd.platform == models.sql.DistributionPlatform.MAVEN:
        return storage.AccessError(
            "Failed to find the Maven artifact. Check that Owner or Namespace is the Maven groupId "
            "(for example, org.apache.maven), and that the package artifactId and version are correct. "
            f"You can verify Maven coordinates on https://search.maven.org. Original error: {error}",
            status=502,
        )

    return storage.AccessError(f"Failed to get API response from distribution platform: {error}", status=502)


def _ensure_finish_phase(release: models.sql.Release) -> None:
    if release.phase != models.sql.ReleasePhase.RELEASE_PREVIEW:
        raise storage.AccessError("This release is not in the finish phase", status=409)


async def _ensure_not_held(release: models.sql.Release, data: db.Session) -> None:
    if await interaction.release_distribution_checks_held(release, data):
        raise storage.AccessError(distribution.CHECKS_HELD_MESSAGE, status=409)


async def _look_up(dd: models.distribution.Data, staging: bool) -> models.distribution.Metadata | None:
    # None if the platform doesn't have the package
    api_url = distribution.get_api_url(dd, staging)
    if dd.platform == models.sql.DistributionPlatform.MAVEN:
        api_oc = await distribution.json_from_maven_xml(api_url, dd.version)
    else:
        api_oc = await distribution.json_from_distribution_platform(api_url, dd.platform, dd.version)
    match api_oc:
        case outcome.Result(result):
            pass
        case outcome.Error(error):
            log.info(f"No distribution at {api_url}: {error}")
            return None
    upload_date = distribution.distribution_upload_date(dd.platform, result, dd.version)
    if upload_date is None:
        return None
    return models.distribution.Metadata(
        api_url=api_url,
        result=result,
        upload_date=upload_date,
        web_url=distribution.distribution_web_url(dd.platform, result, dd.version),
    )


def _validate_version_pattern(platform: models.sql.DistributionPlatform, pattern: str) -> None:
    unknown = construct.unknown_template_variables(pattern, _VERSION_PATTERN_TOKEN_NAMES)
    if unknown:
        raise storage.AccessError(f"Unknown version tokens: {', '.join(unknown)}", status=400)
    # A Docker Hub tag can be pointed at another image later, so one shared between releases proves nothing
    if (platform == models.sql.DistributionPlatform.DOCKER_HUB) and ("{{VERSION}}" not in pattern):
        raise storage.AccessError(
            "A Docker Hub version must include {{VERSION}}, as a tag shared between releases can be moved", status=400
        )
    # Filled in per release, so check a sample value is a version we'd accept
    probe = pattern.replace("{{MAJOR_VERSION}}", "1").replace("{{PROJECT_KEY}}", "x").replace("{{VERSION}}", "1.0")
    try:
        models.safe.VersionKey(probe)
    except ValueError as e:
        raise storage.AccessError(f"Version is not valid: {e}", status=400) from e


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


class ReleaseManager(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsReleaseManager,
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
        release = await self.__validate_release_in_committee(release_key)
        if staging and release.is_embargoed:
            raise storage.AccessError("Embargoed releases cannot be distributed to staging", status=403)
        # The distribution is recorded once it's made, and that is refused until the release is published
        await _ensure_not_held(release, self.__data)
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

    async def change_version(
        self,
        release_key: models.safe.ReleaseKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: str,
        package: str,
        version: str,
        new_version: models.safe.VersionKey,
    ) -> None:
        """Change the version an outstanding distribution is looked for under."""
        release = await self.__validate_release_in_committee(release_key)
        _ensure_finish_phase(release)
        await _ensure_not_held(release, self.__data)
        row = await self.__data.distribution(str(release_key), platform, owner_namespace, package, version).demand(
            storage.AccessError("Distribution not found", status=404)
        )
        if not row.is_outstanding:
            raise storage.AccessError("This distribution has already been found or ignored", status=409)
        if str(new_version) == version:
            raise storage.AccessError("Already checking for that version", status=400)
        taken = await self.__data.distribution(
            str(release_key), platform, owner_namespace, package, str(new_version)
        ).get()
        if (taken is not None) and (not taken.staging):
            raise storage.AccessError(f"{package} {new_version} is already recorded for this release", status=409)
        if taken is not None:
            await self.__data.delete(taken)
        await self.__data.delete(row)
        await self.__data.flush()
        self.__data.add(
            models.sql.Distribution(
                release_key=str(release_key),
                platform=platform,
                owner_namespace=owner_namespace,
                package=package,
                version=str(new_version),
                status=models.sql.DistributionStatus.CHECKING,
                created_by=self.__asf_uid,
            )
        )
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_change_version",
            release_key=str(release_key),
            platform=platform.name,
            package=package,
            version=version,
            new_version=str(new_version),
        )

    async def check_now(self, release_key: models.safe.ReleaseKey) -> int:
        """Reset the release's outstanding distributions so the next check picks them up. Returns the count."""
        release = await self.__validate_release_in_committee(release_key)
        _ensure_finish_phase(release)
        await _ensure_not_held(release, self.__data)
        checking = await self.__data.distribution(
            release_key=str(release_key), status=models.sql.DistributionStatus.CHECKING
        ).all()
        outstanding = [row for row in checking if row.is_outstanding]
        for row in outstanding:
            row.retries = 0
            row.last_checked = None
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_check_now",
            release_key=str(release_key),
            count=len(outstanding),
        )
        return len(outstanding)

    async def ignore(
        self,
        release_key: models.safe.ReleaseKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: str,
        package: str,
        version: str,
        reason: str,
    ) -> None:
        release = await self.__validate_release_in_committee(release_key)
        _ensure_finish_phase(release)
        await _ensure_not_held(release, self.__data)
        if not reason.strip():
            raise storage.AccessError("A reason is required", status=400)
        row = await self.__data.distribution(str(release_key), platform, owner_namespace, package, version).demand(
            storage.AccessError("Distribution not found", status=404)
        )
        if not row.is_outstanding:
            raise storage.AccessError("This distribution has already been found or ignored", status=409)
        row.status = models.sql.DistributionStatus.IGNORED
        row.ignored_by = self.__asf_uid
        row.ignored_reason = reason.strip()
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_ignore",
            release_key=str(release_key),
            platform=platform.name,
            package=package,
            version=version,
            reason=row.ignored_reason,
        )

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
        await self.__validate_release_in_committee(release_key)
        namespace = str(owner_namespace) if owner_namespace else (platform.value.default_owner_namespace or "")
        checked = datetime.datetime.now(datetime.UTC)
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
            status=models.sql.DistributionStatus.CHECKING if pending else models.sql.DistributionStatus.FOUND,
            retries=0,
            last_checked=checked,
            upload_date=upload_date,
            api_url=api_url,
            web_url=web_url,
            created_by=self.__asf_uid,
        )
        if staging and (existing is not None) and (not existing.staging):
            return existing, False
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
        if existing.status != models.sql.DistributionStatus.FOUND:
            existing.last_checked = checked
            if pending:
                if existing.status == models.sql.DistributionStatus.CHECKING:
                    existing.retries = existing.retries + 1
            else:
                existing.status = models.sql.DistributionStatus.FOUND
                existing.ignored_by = None
                existing.ignored_reason = None
                existing.upload_date = upload_date
                existing.api_url = api_url
                existing.web_url = web_url
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
        release = await self.__validate_release_in_committee(release_key)
        # Recording looks the package up, which would reveal an embargoed version
        await _ensure_not_held(release, self.__data)
        if staging and (release.phase != models.sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT):
            raise storage.AccessError("A staging distribution can only be recorded during compose", status=409)
        default_namespace = dd.platform.value.default_owner_namespace
        if default_namespace and (not dd.owner_namespace):
            dd = dd.model_copy(update={"owner_namespace": models.safe.OwnerNamespace(default_namespace)})
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

    async def unignore(
        self,
        release_key: models.safe.ReleaseKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: str,
        package: str,
        version: str,
    ) -> None:
        release = await self.__validate_release_in_committee(release_key)
        _ensure_finish_phase(release)
        await _ensure_not_held(release, self.__data)
        row = await self.__data.distribution(str(release_key), platform, owner_namespace, package, version).demand(
            storage.AccessError("Distribution not found", status=404)
        )
        if row.status != models.sql.DistributionStatus.IGNORED:
            raise storage.AccessError("This distribution is not ignored", status=409)
        row.status = models.sql.DistributionStatus.CHECKING
        row.ignored_by = None
        row.ignored_reason = None
        row.retries = 0
        row.last_checked = None
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_unignore",
            release_key=str(release_key),
            platform=platform.name,
            package=package,
            version=version,
        )

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
            existing.status = models.sql.DistributionStatus.CHECKING if pending else models.sql.DistributionStatus.FOUND
            existing.last_checked = datetime.datetime.now(datetime.UTC)
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
        release = await self.__validate_release_in_committee(release_key)
        distribution = await self.__data.distribution(
            release_key=str(release_key),
            platform=platform,
            owner_namespace=owner_namespace,
            package=package,
            version=version,
        ).demand(RuntimeError(f"Distribution {release_key} {platform} {owner_namespace} {package} {version} not found"))
        if distribution.is_outstanding and (release.phase == models.sql.ReleasePhase.RELEASE_PREVIEW):
            raise storage.AccessError("This distribution has not been found yet, so it can only be ignored", status=409)
        await self.__data.delete(distribution)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_delete",
            release_key=str(release_key),
            platform=platform.name,
        )

    async def expect_for_release_no_commit(self, release_key: models.safe.ReleaseKey) -> None:
        release = await self.__data.release(key=str(release_key)).demand(
            storage.AccessError(f"Release '{release_key}' not found.", status=404)
        )
        expected = await self.__data.expected_distribution(project_key=release.project_key).all()
        wanted: dict[tuple[models.sql.DistributionPlatform, str, str], set[str]] = {}
        for row in expected:
            wanted.setdefault((row.platform, row.owner_namespace, row.package), set()).add(
                row.version_for(release.version)
            )
        # A staged distribution is looked for as a release, and stays marked as staging until it's found
        for staged in await self.__data.distribution(release_key=release.key).all():
            if not staged.staging:
                continue
            versions = wanted.get((staged.platform, staged.owner_namespace, staged.package))
            # Never found on staging, or the project expects this package under another version
            if (not staged.is_staged) or ((versions is not None) and (staged.version not in versions)):
                await self.__data.delete(staged)
            elif staged.status == models.sql.DistributionStatus.FOUND:
                staged.status = models.sql.DistributionStatus.CHECKING
                staged.retries = 0
                staged.last_checked = None
        await self.__data.flush()
        for (platform, owner_namespace, package), versions in wanted.items():
            for version in sorted(versions):
                existing = await self.__data.distribution(
                    release.key, platform, owner_namespace, package, version
                ).get()
                if existing is not None:
                    continue
                self.__data.add(
                    models.sql.Distribution(
                        release_key=release.key,
                        platform=platform,
                        owner_namespace=owner_namespace,
                        package=package,
                        version=version,
                        status=models.sql.DistributionStatus.CHECKING,
                        created_by=self.__asf_uid,
                    )
                )

    async def expected_add(
        self,
        project_key: models.safe.ProjectKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: models.safe.OwnerNamespace | None,
        packages: Sequence[models.safe.Alphanumeric],
        version_pattern: str,
    ) -> int:
        project = await self.__validate_project_in_committee(project_key)
        namespace = str(owner_namespace) if owner_namespace else (platform.value.default_owner_namespace or "")
        try:
            util.validate_distribution_owner_namespace(platform, namespace)
        except ValueError as e:
            raise storage.AccessError(str(e.args[0]), status=400) from e
        pattern = version_pattern.strip() or "{{VERSION}}"
        _validate_version_pattern(platform, pattern)
        added = 0
        for package in dict.fromkeys(str(package) for package in packages):
            existing = await self.__data.expected_distribution(
                str(project_key), platform, namespace, package, pattern
            ).get()
            if existing is not None:
                continue
            self.__data.add(
                models.sql.ExpectedDistribution(
                    project_key=str(project_key),
                    platform=platform,
                    owner_namespace=namespace,
                    package=package,
                    version_pattern=pattern,
                    created_by=self.__asf_uid,
                )
            )
            added += 1
        if added:
            project.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_expected_add",
            project_key=str(project_key),
            platform=platform.name,
            added=added,
        )
        return added

    async def expected_add_from_latest_release(self, project_key: models.safe.ProjectKey) -> int:
        project = await self.__validate_project_in_committee(project_key)
        recorded = await self.__data.distribution(project_key=str(project_key), _with_release=True).all()
        expected = await self.__data.expected_distribution(project_key=str(project_key)).all()
        _latest, missing = distribution.expected_from_latest_release(recorded, expected)
        for row in missing:
            row.created_by = self.__asf_uid
            self.__data.add(row)
        if missing:
            project.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_expected_add_from_release",
            project_key=str(project_key),
            added=len(missing),
        )
        return len(missing)

    async def expected_remove(
        self,
        project_key: models.safe.ProjectKey,
        platform: models.sql.DistributionPlatform,
        owner_namespace: str,
        package: str,
        version_pattern: str,
    ) -> None:
        project = await self.__validate_project_in_committee(project_key)
        expected = await self.__data.expected_distribution(
            str(project_key), platform, owner_namespace, package, version_pattern
        ).demand(storage.AccessError("Expected distribution not found", status=404))
        await self.__data.delete(expected)
        project.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            operation="distribution_expected_remove",
            project_key=str(project_key),
            platform=platform.name,
            package=package,
        )

    async def __validate_project_in_committee(self, project_key: models.safe.ProjectKey) -> models.sql.Project:
        project = await self.__data.project(key=str(project_key)).demand(
            storage.AccessError(f"Project '{project_key}' not found.", status=404)
        )
        if project.committee_key != self.__committee_key:
            raise storage.AccessError(f"Project {project_key} is not in committee {self.__committee_key}", status=403)
        storage.ensure_project_active(project)
        return project

    async def __validate_release_in_committee(self, release_key: models.safe.ReleaseKey) -> models.sql.Release:
        release = await self.__data.release(key=str(release_key), _project=True).demand(
            storage.AccessError(f"Release '{release_key}' not found.", status=404)
        )
        if release.project.committee_key != self.__committee_key:
            raise storage.AccessError(
                f"Release {release_key} is not in committee {self.__committee_key}",
                status=403,
            )
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        return release


class CommitteeMember(ReleaseManager):
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


class FoundationAdmin(FoundationCommitter):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session):
        super().__init__(write, write_as, data)
        self.__data = data

    async def check(
        self,
        release_key: str,
        platform: models.sql.DistributionPlatform,
        owner_namespace: str,
        package: str,
        version: str,
    ) -> None:
        via = models.sql.validate_instrumented_attribute
        row = await self.__data.distribution(release_key, platform, owner_namespace, package, version).get()
        if (row is None) or (row.status != models.sql.DistributionStatus.CHECKING):
            return
        # The row can change during the lookup, so the writes below only apply while it's still CHECKING
        still_checking = (
            via(models.sql.Distribution.release_key) == release_key,
            via(models.sql.Distribution.platform) == platform,
            via(models.sql.Distribution.owner_namespace) == owner_namespace,
            via(models.sql.Distribution.package) == package,
            via(models.sql.Distribution.version) == version,
            via(models.sql.Distribution.status) == models.sql.DistributionStatus.CHECKING,
        )
        release = await self.__data.release(key=release_key).get()
        # Looking for an embargoed version would reveal it to the platform
        if (release is not None) and await interaction.release_distribution_checks_held(release, self.__data):
            return
        # Only a release in finish waits on its distributions. Any other row gets a few tries
        in_finish = (release is not None) and (release.phase == models.sql.ReleasePhase.RELEASE_PREVIEW)
        if (not in_finish) and (row.retries >= _RETRY_LIMIT):
            await self.__data.execute(sqlmodel.delete(models.sql.Distribution).where(*still_checking))
            await self.__data.commit()
            return
        try:
            # In finish a staging row is one that was staged, and is looked for as a release
            metadata = await _look_up(row.distribution_data(), row.staging and (not in_finish))
        except Exception:
            log.exception(f"Could not look up {platform.name} {owner_namespace} {package} {version}")
            metadata = None
        update = sqlmodel.update(models.sql.Distribution).where(*still_checking)
        checked = datetime.datetime.now(datetime.UTC)
        if metadata is None:
            update = update.values(last_checked=checked, retries=via(models.sql.Distribution.retries) + 1)
        else:
            update = update.values(
                last_checked=checked,
                status=models.sql.DistributionStatus.FOUND,
                staging=row.staging and (not in_finish),
                upload_date=metadata.upload_date,
                api_url=metadata.api_url,
                web_url=metadata.web_url,
            )
        await self.__data.execute(update)
        await self.__data.commit()

    async def expected_replace_no_commit(
        self,
        project_key: models.safe.ProjectKey,
        distributions: Sequence[models.api.ProjectConfigDistributionArgs],
    ) -> None:
        """Make the project's expected distributions exactly those given."""
        wanted: dict[tuple[models.sql.DistributionPlatform, str, str, str], None] = {}
        for entry in distributions:
            namespace = str(entry.owner_namespace) if entry.owner_namespace else ""
            namespace = namespace or (entry.platform.value.default_owner_namespace or "")
            try:
                util.validate_distribution_owner_namespace(entry.platform, namespace)
            except ValueError as e:
                raise storage.AccessError(str(e.args[0]), status=400) from e
            pattern = entry.version.strip() or "{{VERSION}}"
            _validate_version_pattern(entry.platform, pattern)
            for package in entry.packages:
                wanted[(entry.platform, namespace, str(package), pattern)] = None
        existing = await self.__data.expected_distribution(project_key=str(project_key)).all()
        kept = set()
        for row in existing:
            key = (row.platform, row.owner_namespace, row.package, row.version_pattern)
            if key in wanted:
                kept.add(key)
            else:
                await self.__data.delete(row)
        # The project may have been added in this transaction, and has to be written first
        await self.__data.flush()
        for key in wanted:
            if key in kept:
                continue
            platform, namespace, package, pattern = key
            self.__data.add(
                models.sql.ExpectedDistribution(
                    project_key=str(project_key),
                    platform=platform,
                    owner_namespace=namespace,
                    package=package,
                    version_pattern=pattern,
                )
            )
