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

import aiofiles.os
import aioshutil
import sqlmodel

import atr.construct as construct
import atr.db as db
import atr.mail as mail
import atr.models.basic as basic
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.tasks.message as message
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
        committee_key: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized")
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
            raise storage.AccessError("Not authorized")
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
        asf_uid: str,
        fullname: str,
        subject_template_hash: str | None = None,
        email_cc: list[str] | None = None,
        email_bcc: list[str] | None = None,
    ) -> None:
        permitted = util.permitted_announce_recipients(asf_uid)
        all_addrs = [email_to] + (email_cc or []) + (email_bcc or [])
        for addr in all_addrs:
            if addr not in permitted:
                raise storage.AccessError(f"You are not permitted to send announcements to {addr}")

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
            )
        )
        if (committee := release.project.committee) is None:
            raise storage.AccessError("Release has no committee - Invalid state")

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
                    }"
                )

        # Fetch the current subject template and verify the hash
        subject_template = await construct.announce_release_subject_default(project_key)
        if subject_template_hash is not None:
            current_hash = construct.template_hash(subject_template)
            if current_hash != subject_template_hash:
                raise storage.AccessError("Subject template has been modified since the form was loaded")

        # Substitute the subject template
        options = construct.AnnounceReleaseOptions(
            asfuid=asf_uid,
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
            raise storage.AccessError("Release already exists")
        # TODO: This is not reliable because of race conditions
        # But it adds a layer of protection in most cases
        preserve = release.project.policy_preserve_download_files
        if preserve is True:
            await self.__hard_link_downloads(committee, unfinished_path, download_path_suffix, dry_run=True)

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
            if unfinished_revisions_path:
                # This removes all of the prior revisions
                # Each prior revision directory is immutable
                await util.delete_immutable_directory(
                    unfinished_revisions_path,
                    reason="user {self.__asf_uid} is releasing {project_key} {version_key} {preview_revision_number}",
                )
        except Exception as e:
            raise storage.AccessError(f"Error moving files: {e!s}")

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
                task_args=message.Send(
                    email_sender=f"{asf_uid}@apache.org",
                    email_to=email_to,
                    subject=subject,
                    body=body,
                    in_reply_to=None,
                    email_cc=email_cc or [],
                    email_bcc=email_bcc or [],
                    footer_category=mail.MailFooterCategory.NONE,
                ).model_dump(),
                asf_uid=asf_uid,
                project_key=str(project_key),
                version_key=str(version_key),
            )
            self.__data.add(task)

            await self.__promote_in_database(release, preview_revision_number, release_date)
            await self.__data.commit()
        except storage.AccessError as e:
            raise e
        except Exception as e:
            raise storage.AccessError(
                f"Files moved successfully, but error queuing announcement: {e!s}. Manual cleanup needed."
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
        # TODO: Rename *_dir functions to _path functions
        downloads_base_path = paths.get_downloads_dir()
        downloads_path = downloads_base_path / committee.key
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
        self, release: sql.Release, preview_revision_number: safe.RevisionNumber, release_date: datetime.datetime
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
