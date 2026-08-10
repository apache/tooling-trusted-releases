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

import asyncio
import copy
import datetime
import pathlib
import tempfile
from typing import Final

import aiofiles.os
import aioshutil
import sqlmodel

import atr.analysis as analysis
import atr.catalog_site as catalog_site
import atr.config as config
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.log as log
import atr.mail as mail
import atr.models.args as args
import atr.models.basic as basic
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.svn as svn
import atr.tasks.checks as checks
import atr.tasks.checks.signature as signature
import atr.util as util

_SIGNATURE_CHECKER_KEY: Final[str] = checks.function_key(signature.check)


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

    async def release(  # noqa: C901
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        preview_revision_number: safe.RevisionNumber | None,
        email_to: str,
        body: str,
        download_path_suffix: safe.RelPath | None,
        fullname: str,
        subject_template_hash: str | None = None,
        email_cc: list[str] | None = None,
        email_bcc: list[str] | None = None,
        *,
        acknowledge_unreachable: bool = False,
        auto_archive_prior: bool = False,
    ) -> None:
        unfinished_dir: str = ""
        finished_dir: str = ""

        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
            _project_release_policy=True,
            _committee=True,
            _revisions=True,
            _distributions=True,
            _release_policy=True,
        ).demand(
            storage.AccessError(
                f"Release {project_key!s} {version_key!s} does not exist",
                status=404,
            )
        )
        if release.project.committee_key != self.__committee_key:
            raise storage.AccessError(f"Project {project_key} is not in committee {self.__committee_key}", status=403)
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        latest_revision_number = release.safe_latest_revision_number
        if (preview_revision_number is not None) and (preview_revision_number != latest_revision_number):
            raise storage.AccessError(
                f"Revision {preview_revision_number!s} is not the latest revision, {latest_revision_number!s}",
                status=409,
            )
        preview_revision_number = latest_revision_number

        # Loaded the release first so a project's stored announce recipients can
        # be folded into the permitted set before we validate the addresses.
        permitted = util.permitted_announce_recipients(
            self.__asf_uid, committee_key=self.__committee_key, project=release.project
        )
        all_addrs = [email_to] + (email_cc or []) + (email_bcc or [])
        for addr in all_addrs:
            if addr not in permitted:
                raise storage.AccessError(f"You are not permitted to send announcements to {addr}", status=403)
        if (committee := release.project.committee) is None:
            raise storage.AccessError("Release has no committee - Invalid state", status=500)

        policy = release.release_policy or release.project.release_policy
        if policy and policy.file_tag_mappings:
            missing = []
            tags = policy.file_tag_mappings.keys()
            distributions = [
                d.platform.value.gh_slug for d in release.distributions if (not d.staging) and (not d.pending)
            ]
            for tag in tags:
                if tag not in distributions:
                    missing.append(tag)
            if missing:
                raise storage.AccessError(
                    f"This release cannot be announced until the following distributions have been recorded: {
                        ', '.join(missing)
                    }",
                    status=409,
                )

        # Fetch the current subject template and verify the hash
        subject_template = await construct.announce_release_subject_default(project_key)
        if subject_template_hash is not None:
            current_hash = construct.template_hash(subject_template)
            if current_hash != subject_template_hash:
                raise storage.AccessError("Subject template has been modified since the form was loaded", status=409)

        # Substitute the subject template
        options = construct.AnnounceReleaseOptions(
            asfuid=self.__asf_uid,
            fullname=fullname,
            project_key=project_key,
            version_key=version_key,
            revision_number=preview_revision_number,
        )
        subject, _ = await construct.announce_release_subject_and_body(subject_template, "", options)

        # Prepare paths for file operations
        unfinished_revisions_path = paths.release_directory_base(release)
        unfinished_path = unfinished_revisions_path / release.unwrap_revision_number
        unfinished_dir = str(unfinished_path)
        release_date = datetime.datetime.now(datetime.UTC)
        predicted_finished_release = self.__predicted_finished_release(release, release_date)
        finished_path = paths.release_directory(predicted_finished_release)
        finished_dir = str(finished_path)
        if await aiofiles.os.path.exists(finished_dir):
            raise storage.AccessError("Release already exists", status=409)
        # TODO: This is not reliable because of race conditions
        # But it adds a layer of protection in most cases
        completed_publish = await interaction.release_completed_svn_publish_task_for_revision(
            project_key,
            release.safe_version_key,
            preview_revision_number,
            caller_data=self.__data,
        )
        if completed_publish is None:
            raise storage.AccessError(
                "This release cannot be announced until it has been published to SVN",
                status=409,
            )
        effective_download_path_suffix = self.__download_path_suffix_from_task(completed_publish)
        published_revision = self.__publish_revision_from_task(completed_publish)
        if published_revision is None:
            log.warning(
                f"SVN publication for {project_key!s} {version_key!s} {preview_revision_number!s} "
                "is recorded but has no revision number"
            )
        try:
            kind = config.svn_publish_kind()
            target = util.svn_publish_target()
            public_url = util.publication_check_url(
                committee, effective_download_path_suffix, util.DownloadFile.METADATA
            )
            internal_url = util.svn_publish_internal_url(committee, effective_download_path_suffix)
        except ValueError as exc:
            log.warning(f"SVN publication target is not configured for {project_key!s} {version_key!s}: {exc}")
        else:
            if kind is config.SvnPublishKind.ASF_DISTRIBUTION:
                await self.__check_publication_artifacts(unfinished_path, target, public_url, acknowledge_unreachable)
            else:
                await self.__check_local_publication_artifacts(unfinished_path, internal_url, published_revision)

        if (not config.is_dev_environment()) and (not await mail.relay_reachable()):
            raise storage.AccessError(
                "The mail relay appears to be unavailable, so the announcement cannot be sent."
                " Please check https://status.apache.org/ and try again later.",
                status=503,
            )

        # Ensure that the permissions of every directory are 755
        await asyncio.to_thread(util.chmod_directories, unfinished_path)

        try:
            # Move the release files from somewhere in unfinished to somewhere in finished
            # The whole finished hierarchy is write once for each directory, and then read only
            # TODO: Set permissions to help enforce this, or find alternative methods
            await aioshutil.move(unfinished_dir, finished_dir)
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                version_key=str(version_key),
                revision_number=str(preview_revision_number),
                source_directory=unfinished_dir,
                target_directory=finished_dir,
                email_to=email_to,
                email_cc=basic.as_json(email_cc or []),
                email_bcc=basic.as_json(email_bcc or []),
            )
        except Exception as e:
            raise storage.AccessError(f"Error moving files: {e!s}", status=500)

        try:
            task = sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.MESSAGE_SEND,
                task_args=args.Send(
                    email_sender=f"{self.__asf_uid}@apache.org",
                    email_to=email_to,
                    subject=subject,
                    body=body,
                    in_reply_to=None,
                    email_cc=email_cc or [],
                    email_bcc=email_bcc or [],
                    footer_category=mail.MailFooterCategory.NONE,
                ).as_task_args(),
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                version_key=str(version_key),
            )
            self.__data.add(task)

            # A release has been made, so tell the releases list. This is separate from the
            # announcement above, and goes out from noreply rather than the release manager
            notification = construct.release_notification(committee, release.project, str(version_key), release_date)
            self.__data.add(
                sql.Task(
                    status=sql.TaskStatus.QUEUED,
                    task_type=sql.TaskType.MESSAGE_SEND,
                    task_args=notification.as_task_args(),
                    asf_uid=self.__asf_uid,
                    project_key=str(project_key),
                    version_key=str(version_key),
                )
            )

            # A released version is a new page on the static site.
            await catalog_site.queue_regeneration(self.__data, self.__asf_uid, str(project_key))

            # Record the artifacts before promoting, since promotion deletes the
            # revisions and cascade-deletes the file state we read classifications from
            await self.__write_artifact_rows(
                release,
                committee,
                finished_path,
                preview_revision_number,
                published_revision,
                effective_download_path_suffix,
                release_date,
            )
            await self.__promote_in_database(release, preview_revision_number, release_date)
            self.__data.add(
                sql.LifecycleEvent(
                    project_key=release.project_key,
                    cycle_key=release.cycle_key,
                    version_key=release.key,
                    event=sql.LifecycleEventType.RELEASE,
                    effective=release_date,
                    published=release_date,
                    reference_urls=[f"https://lists.apache.org/list.html?{email_to}"],
                )
            )
            await self.__data.commit()
        except storage.AccessError:
            raise
        except Exception as e:
            raise storage.AccessError(
                f"Files moved successfully, but error queuing announcement: {e!s}. Manual cleanup needed.",
                status=500,
            )
        try:
            if unfinished_revisions_path:
                # This removes all of the prior revisions
                # Each prior revision directory is immutable
                await util.delete_immutable_directory(
                    unfinished_revisions_path,
                    reason=f"user {self.__asf_uid} is releasing {project_key} {version_key} {preview_revision_number}",
                )
        except Exception as e:
            raise storage.AccessError(
                f"Release announced, but error deleting prior revisions: {e!s}. Manual cleanup needed.",
                status=500,
            )
        await self.__archive_prior_release(release, auto_archive_prior)

    async def __archive_prior_release(self, release_row: sql.Release, auto_archive_prior: bool) -> None:
        if not auto_archive_prior:
            return
        if not release_row.archive_prior_release:
            return
        if not release_row.project.policy_auto_archive_prior_release:
            return
        archive_prior = await interaction.prior_release_for_archive(
            release_row.project,
            release_row.version,
            caller_data=self.__data,
        )
        if archive_prior is None:
            return
        if archive_prior.project_key != release_row.project_key:
            raise storage.AccessError("Release announced, but resolved prior release belongs to another project")
        try:
            archive_error = await self.__archive_release(
                release_row.safe_project_key,
                archive_prior.safe_version_key,
                archive_prior,
            )
        except Exception as e:
            raise storage.AccessError(
                f"Release announced, but archiving prior release '{archive_prior.version}' failed: {e!s}",
                status=500,
            ) from e
        if archive_error is not None:
            raise storage.AccessError(
                f"Release announced, but archiving prior release '{archive_prior.version}' failed: {archive_error}",
                status=500,
            )

    async def __archive_release(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        release_row: sql.Release,
    ) -> str | None:
        if release_row.phase != sql.ReleasePhase.RELEASE:
            return f"Release {project_key!s} {version_key!s} is not in the release phase"

        archive_date = datetime.datetime.now(datetime.UTC)
        via = sql.validate_instrumented_attribute
        update_stmt = (
            sqlmodel.update(sql.Release)
            .where(via(sql.Release.key) == release_row.key)
            .where(via(sql.Release.is_archived).is_(False))
            .values(archived=archive_date, is_archived=True)
        )
        update_result = await self.__data.execute_query(update_stmt)
        if getattr(update_result, "rowcount", 0) != 1:
            return f"Release {project_key!s} {version_key!s} is already archived"

        self.__data.add(
            sql.LifecycleEvent(
                project_key=release_row.project_key,
                cycle_key=release_row.cycle_key,
                version_key=release_row.key,
                event=sql.LifecycleEventType.ARCHIVE,
                effective=archive_date,
                published=archive_date,
            )
        )
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version=str(version_key),
            archived=archive_date.isoformat(),
        )
        return None

    async def __check_local_publication_artifacts(
        self,
        unfinished_path: safe.StatePath,
        internal_url: str,
        svn_revision: int | None,
    ) -> None:
        temp_dir = await asyncio.to_thread(tempfile.mkdtemp, dir=paths.get_tmp_dir())
        try:
            export_path = pathlib.Path(temp_dir) / "export"
            try:
                await svn.export(internal_url, svn_revision, export_path)
            except svn.CommandExecutionError as exc:
                raise storage.AccessError(
                    f"The local SVN publish repository could not be checked: {svn.error_message(exc)}",
                    status=409,
                ) from None
            differences = await util.tree_differences(unfinished_path.path, export_path)
            if unexpected := differences.only_in_other:
                log.warning(
                    f"The SVN publication contains {len(unexpected)} unexpected files; the first is {unexpected[0]}"
                )
            if missing := differences.only_in_base:
                raise storage.AccessError(
                    f"This release cannot be announced, because {len(missing)} files are missing from the"
                    f" SVN publication; the first missing file is {missing[0]}.",
                    status=409,
                )
            if differing := differences.differing:
                raise storage.AccessError(
                    f"This release cannot be announced, because {len(differing)} files in the SVN publication"
                    f" differ from the release files; the first differing file is {differing[0]}.",
                    status=409,
                )
        finally:
            try:
                await aioshutil.rmtree(temp_dir)
            except OSError as exc:
                log.warning(f"Could not remove the publication check directory {temp_dir}: {exc}")

    async def __check_publication_artifacts(
        self,
        unfinished_path: safe.StatePath,
        target: util.SvnPublishTarget,
        public_url: str,
        acknowledge_unreachable: bool,
    ) -> None:
        rel_paths = await self.__artifact_rel_paths(unfinished_path)
        if not rel_paths:
            return
        summary = await util.check_propagation(target, public_url, rel_paths)
        if summary.unprobed:
            log.warning(
                f"Propagation check for {public_url} probed {summary.total} of {len(rel_paths)} artifacts;"
                f" {summary.unprobed} were not checked"
            )
        if missing := summary.missing:
            raise storage.AccessError(
                f"This release cannot be announced yet, because only {summary.reachable} of {summary.total}"
                f" checked artifacts are available on the download server; the first missing artifact is"
                f" {missing[0].public_url}. Publication may still be propagating, so please try again later.",
                status=409,
            )
        if blocked := summary.blocked:
            raise storage.AccessError(
                f"This release cannot be announced, because {len(blocked)} of {summary.total} checked artifacts"
                f" are not publicly accessible; the first is {blocked[0].public_url} ({blocked[0].error}).",
                status=409,
            )
        unreachable = summary.unreachable
        if not unreachable:
            return
        first = unreachable[0]
        if acknowledge_unreachable:
            log.warning(
                f"Announcing despite an unreachable download server; first failure: {first.public_url} ({first.error})"
            )
            return
        raise storage.PropagationUnreachableError(
            f"The download server could not be checked ({first.error}). It may be experiencing an outage;"
            " see https://status.apache.org/ for its status.",
            status=503,
        )

    async def __artifact_rel_paths(self, preview_path: safe.StatePath) -> list[str]:
        rel_paths: list[str] = []
        async for rel in util.paths_recursive(preview_path):
            rel_str = str(rel)
            if analysis.is_artifact(rel_str):
                rel_paths.append(rel_str)
        rel_paths.sort()
        return rel_paths

    def __download_path_suffix_from_task(self, task: sql.Task) -> safe.RelPath | None:
        candidate = task.task_args.get("download_path_suffix")
        if isinstance(candidate, str) and candidate:
            return safe.RelPath(candidate)
        return None

    def __publish_revision_from_task(self, task: sql.Task) -> int | None:
        result = task.result
        if isinstance(result, results.SvnPublish):
            return result.svn_revision
        if isinstance(result, dict):
            candidate = result.get("svn_revision")
            if isinstance(candidate, int):
                return candidate
        return None

    def __predicted_finished_release(self, release: sql.Release, release_date: datetime.datetime) -> sql.Release:
        # Taking a deep copy stops this from being a SQLAlchemy proxy object
        # https://docs.sqlalchemy.org/en/20/orm/session_basics.html
        predicted_finished_release = copy.deepcopy(release)
        predicted_finished_release.phase = sql.ReleasePhase.RELEASE
        predicted_finished_release.released = release_date
        return predicted_finished_release

    async def __promote_in_database(
        self,
        release: sql.Release,
        preview_revision_number: safe.RevisionNumber,
        release_date: datetime.datetime,
    ) -> None:
        """Promote a release preview to a release and delete its old revisions."""
        via = sql.validate_instrumented_attribute

        update_stmt = (
            sqlmodel.update(sql.Release)
            .where(
                via(sql.Release.key) == release.key,
                via(sql.Release.phase) == sql.ReleasePhase.RELEASE_PREVIEW,
                sql.latest_revision_number_query() == str(preview_revision_number),
            )
            .values(
                phase=sql.ReleasePhase.RELEASE,
                released=release_date,
            )
        )
        update_result = await self.__data.execute_query(update_stmt)
        # Avoid a type error with update_result.rowcount
        # Could not find another way to do it, other than using a Protocol
        rowcount: int = getattr(update_result, "rowcount", 0)
        if rowcount != 1:
            raise RuntimeError("A newer revision appeared, please refresh and try again.")

        delete_revisions_stmt = sqlmodel.delete(sql.Revision).where(
            via(sql.Revision.release_key) == release.key,
        )
        await self.__data.execute_query(delete_revisions_stmt)

    async def __parent_revision_number(
        self, release_key: str, revision_number: safe.RevisionNumber
    ) -> safe.RevisionNumber:
        revision = await self.__data.revision(
            release_key=release_key,
            number=str(revision_number),
            _parent=True,
        ).get()
        if (revision is not None) and (revision.parent is not None):
            return revision.parent.safe_number
        # Fall back to the revision itself when there's no parent (a single-revision release)
        return revision_number

    async def __signature_fingerprint(
        self,
        release: sql.Release,
        revision_number: safe.RevisionNumber,
        signature_path: str,
    ) -> str | None:
        results = await interaction.check_results_for_revision(
            release.safe_project_key,
            release.safe_version_key,
            revision_number,
            checker=_SIGNATURE_CHECKER_KEY,
            rel_path=signature_path,
            include_legacy_revision_results=True,
            caller_data=self.__data,
        )
        for result in results:
            if result.status != sql.CheckResultStatus.NOTE:
                continue
            payload = result.data if isinstance(result.data, dict) else {}
            candidate = payload.get("fingerprint")
            if isinstance(candidate, str) and (stripped := candidate.strip()):
                fingerprint = stripped.lower()
                # A signer's SigningKey rows are derived when its key is imported, so this is not
                # expected to be reached; it only stops a key we somehow cannot model from failing the
                # whole announce
                via = sql.validate_instrumented_attribute
                modelled = await self.__data.execute(
                    sqlmodel.select(via(sql.SigningKey.fingerprint)).where(
                        via(sql.SigningKey.fingerprint) == fingerprint
                    )
                )
                if modelled.scalar_one_or_none() is not None:
                    return fingerprint
                log.warning(f"{fingerprint} verified but has no SigningKey row; recording the artifact unattributed")
                return None
        return None

    async def __write_artifact_rows(
        self,
        release: sql.Release,
        committee: sql.Committee,
        finished_path: safe.StatePath,
        revision_number: safe.RevisionNumber,
        svn_revision: int | None,
        download_path_suffix: safe.RelPath | None,
        dated: datetime.datetime,
    ) -> None:
        revision_seq = int(str(revision_number))
        # The preview revision created when a vote passes has no checks of its own, so the
        # signature check sits on the parent draft it was promoted from
        parent_revision_number = await self.__parent_revision_number(release.key, revision_number)
        rel_paths = {str(p) async for p in util.paths_recursive(finished_path)}
        classifications = await self.__data.release_file_classifications_at(release.key, revision_seq)
        # The directory the files publish to under the dist root, the same for every artifact here
        dist_dir = str(paths.committee_dist_relpath(committee, download_path_suffix))

        for rel in sorted(rel_paths):
            if not analysis.is_artifact(rel):
                continue
            signature_path = f"{rel}.asc" if f"{rel}.asc" in rel_paths else None
            checksum_path: str | None = None
            for suffix in (".sha512", ".sha256"):
                candidate = f"{rel}{suffix}"
                if candidate in rel_paths:
                    checksum_path = candidate
                    break
            sbom_path: str | None = None
            for candidate in analysis.sbom_candidates(rel, analysis.SBOM_SUFFIXES):
                if candidate in rel_paths:
                    sbom_path = candidate
                    break
            fingerprint = (
                await self.__signature_fingerprint(release, parent_revision_number, signature_path)
                if signature_path is not None
                else None
            )
            self.__data.add(
                sql.Artifact(
                    project_key=release.project_key,
                    version=release.version,
                    artifact_path=rel,
                    release_key=release.key,
                    key_fingerprint=fingerprint,
                    signature_path=signature_path,
                    checksum_path=checksum_path,
                    sbom_path=sbom_path,
                    classification=classifications.get(rel),
                    svn_revision=svn_revision,
                    download_path_suffix=dist_dir,
                    managed=True,
                    dated=dated,
                )
            )


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
