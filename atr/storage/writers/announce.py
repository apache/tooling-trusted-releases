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
import re
from typing import Final

import aiofiles.os
import aioshutil
import sqlmodel

import atr.analysis as analysis
import atr.config as config
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.mail as mail
import atr.models.args as args
import atr.models.basic as basic
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

    async def release(  # noqa: C901
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        preview_revision_number: safe.RevisionNumber,
        email_to: str,
        body: str,
        download_path_suffix: safe.RelPath | None,
        fullname: str,
        subject_template_hash: str | None = None,
        email_cc: list[str] | None = None,
        email_bcc: list[str] | None = None,
    ) -> None:
        permitted = util.permitted_announce_recipients(self.__asf_uid, committee_key=self.__committee_key)
        all_addrs = [email_to] + (email_cc or []) + (email_bcc or [])
        for addr in all_addrs:
            if addr not in permitted:
                raise storage.AccessError(f"You are not permitted to send announcements to {addr}", status=403)

        unfinished_dir: str = ""
        finished_dir: str = ""

        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
            latest_revision_number=str(preview_revision_number),
            _project_release_policy=True,
            _revisions=True,
            _distributions=True,
            _release_policy=True,
        ).demand(
            storage.AccessError(
                f"Release {project_key!s} {version_key!s} {preview_revision_number!s} does not exist",
                status=404,
            )
        )
        storage.ensure_project_active(release.project)
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
        preserve = release.project.policy_preserve_download_files
        if preserve is True:
            await self.__hard_link_downloads(committee, unfinished_path, download_path_suffix, dry_run=True)

        # Ensure that the permissions of every directory are 755
        await asyncio.to_thread(util.chmod_directories, unfinished_path)

        published_revision: int | None = None
        if svn_publish_url := config.get().SVN_PUBLISH_URL:
            svn_relpath = paths.committee_downloads_dir(committee).path.relative_to(paths.get_downloads_dir().path)
            if download_path_suffix is not None:
                svn_relpath = svn_relpath / download_path_suffix.as_path()
            target_url = f"{svn_publish_url.rstrip('/')}/{svn_relpath}"
            log_message = (
                f"Publish {project_key!s}-{version_key!s}\n\n"
                f"Committee: {committee.key}\n"
                f"Project: {project_key!s}\n"
                f"Version: {version_key!s}\n"
                "Tool: ATR"
            )
            try:
                published_revision = await svn.publish_release(
                    unfinished_path.path, target_url, self.__asf_uid, log_message
                )
            except svn.CommandExecutionError as e:
                if "E160020" in e.output:
                    match = re.search(r"path '([^']+)'", e.output)
                    detail = f": {match.group(1)}" if match else ""
                    raise storage.AccessError(f"Release file already exists in SVN{detail}", status=409) from e
                raise storage.AccessError("SVN publish failed; release was not announced", status=500) from e

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
            if unfinished_revisions_path:
                # This removes all of the prior revisions
                # Each prior revision directory is immutable
                await util.delete_immutable_directory(
                    unfinished_revisions_path,
                    reason=f"user {self.__asf_uid} is releasing {project_key} {version_key} {preview_revision_number}",
                )
        except Exception as e:
            raise storage.AccessError(f"Error moving files: {e!s}", status=500)

        # TODO: Add an audit log entry here
        # TODO: We should consider copying the files instead of hard linking
        # That way, we can write protect the pristine ATR files
        await self.__hard_link_downloads(
            committee,
            finished_path,
            download_path_suffix,
            preserve=preserve,
        )

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

            # Record the artifacts before promoting, since promotion deletes the
            # revisions and cascade-deletes the file state we read classifications from
            await self.__write_artifact_rows(release, finished_path, preview_revision_number, published_revision)
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

    async def __hard_link_downloads(
        self,
        committee: sql.Committee,
        unfinished_path: safe.StatePath,
        download_path_suffix: safe.RelPath | None,
        dry_run: bool = False,
        preserve: bool = False,
    ) -> None:
        """Hard link the release files to the downloads directory."""
        downloads_path = paths.committee_downloads_dir(committee)
        if download_path_suffix is not None:
            downloads_path = downloads_path / download_path_suffix.as_path()
        # The "exist_ok" parameter means to overwrite files if True
        # We only overwrite if we're not preserving, so we supply "not preserve"
        # TODO: Add a test for this
        await util.create_hard_link_clone(
            unfinished_path,
            downloads_path,
            do_not_create_dest_dir=dry_run,
            exist_ok=not preserve,
            dry_run=dry_run,
        )

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
                return stripped.lower()
        return None

    async def __write_artifact_rows(
        self,
        release: sql.Release,
        finished_path: safe.StatePath,
        revision_number: safe.RevisionNumber,
        svn_revision: int | None,
    ) -> None:
        revision_seq = int(str(revision_number))
        # The preview revision created when a vote passes has no checks of its own, so the
        # signature check sits on the parent draft it was promoted from
        parent_revision_number = await self.__parent_revision_number(release.key, revision_number)
        rel_paths = {str(p) async for p in util.paths_recursive(finished_path)}
        classifications = await self.__data.release_file_classifications_at(release.key, revision_seq)

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
                    classification=classifications.get(rel),
                    svn_revision=svn_revision,
                )
            )
