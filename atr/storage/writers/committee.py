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

from typing import TYPE_CHECKING

import atr.constants as constants
import atr.db as db
import atr.db.interaction as interaction
import atr.mail as mail
import atr.models.args as args
import atr.models.sql as sql
import atr.storage as storage
import atr.tasks as tasks
import atr.util as util

if TYPE_CHECKING:
    import datetime


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

    async def github_uploads_set(self, enabled: bool) -> bool:
        """Allow or stop uploads from GitHub Actions for the committee's projects, returning whether it changed."""
        await self.__data.begin_immediate()
        try:
            committee = await self.__data.committee(key=self.__committee_key, _projects=True).demand(
                storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
            )
            if committee.github_oidc_enabled == enabled:
                await self.__data.rollback()
                return False
            if enabled and (not await interaction.automated_release_signing_key_held(committee.key, self.__data)):
                raise storage.AccessError(
                    f"The {committee.key} PMC has no automated release signing key, which uploads from "
                    f"GitHub Actions need. See {constants.AUTOMATED_RELEASE_SIGNING_URL}",
                    status=409,
                )
            committee.github_oidc_enabled = enabled
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            # The repositories that can upload as of this change. Only an active project takes
            # uploads, and only from its repositories under apache/
            repositories = {
                str(project.key): sorted(project.upload_repositories)
                for project in committee.projects
                if project.is_active
            }
            self.__data.add(
                sql.Task(
                    task_type=sql.TaskType.MESSAGE_SEND,
                    task_args=_github_uploads_notice(
                        committee.key, self.__asf_uid, enabled, repositories
                    ).as_task_args(),
                    asf_uid=constants.SYSTEM_SERVICE_UID,
                )
            )
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            enabled=enabled,
            repositories={key: [*names] for key, names in repositories.items()},
        )
        return True

    async def release_manager_add(self, asf_uid: str) -> bool:
        asf_uid = asf_uid.strip().lower()
        if not asf_uid:
            raise storage.AccessError("An ASF UID is required", status=400)
        await self.__data.begin_immediate()
        try:
            committee = await self.__load_committee()
            if asf_uid in committee.committee_members:
                raise storage.AccessError(
                    f"{asf_uid} is a member of the {self.__committee_key} PMC,"
                    " and is therefore already a release manager",
                    status=400,
                )
            if asf_uid not in committee.committers:
                raise storage.AccessError(
                    f"{asf_uid} is not a committer of the {self.__committee_key} committee",
                    status=400,
                )
            if asf_uid in committee.release_managers:
                await self.__data.rollback()
                return False
            # JSON columns do not track in place mutation, so assign a new list
            committee.release_managers = sorted({*committee.release_managers, asf_uid})
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            release_manager=asf_uid,
        )
        return True

    async def release_manager_remove(self, asf_uid: str) -> bool:
        asf_uid = asf_uid.strip().lower()
        await self.__data.begin_immediate()
        try:
            committee = await self.__load_committee()
            if asf_uid not in committee.release_managers:
                await self.__data.rollback()
                return False
            committee.release_managers = [uid for uid in committee.release_managers if uid != asf_uid]
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            release_manager=asf_uid,
        )
        return True

    async def vote_mode_request_abandon(self, approval_id: int, error: str) -> None:
        await self.__data.begin_immediate()
        try:
            approval = await self.__pending_vote_mode_request(approval_id)
            approval.status = sql.ApprovalStatus.FAILED
            approval.error = error
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            approval_request_id=approval_id,
            approval_status=sql.ApprovalStatus.FAILED.value,
            error=error,
        )

    async def vote_mode_request_record(self, approval_id: int, cap_question_id: int) -> None:
        await self.__data.begin_immediate()
        try:
            approval = await self.__pending_vote_mode_request(approval_id)
            approval.cap_question_id = cap_question_id
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            approval_request_id=approval_id,
            cap_question_id=cap_question_id,
        )

    async def vote_mode_request_reserve(self, action: sql.ApprovalAction, closes_at: datetime.datetime) -> int:
        await self.__data.begin_immediate()
        try:
            committee = await self.__load_committee()
            if action != interaction.committee_vote_mode_request_action(committee):
                raise storage.AccessError("The committee's voting changed, please refresh and try again", status=409)
            if (error := await interaction.committee_vote_mode_request_error(committee, self.__data)) is not None:
                raise storage.AccessError(error, status=409)
            approval = sql.ApprovalRequest(
                committee_key=self.__committee_key,
                action=action,
                requested_by=self.__asf_uid,
                closes_at=closes_at,
            )
            self.__data.add(approval)
            await self.__data.flush()
            approval_id = util.unwrap(approval.id)
            await tasks.cap_approval_resolve(
                approval_id,
                schedule=closes_at + constants.CAP_RESOLVE_INITIAL_DELAY,
                caller_data=self.__data,
            )
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            approval_request_id=approval_id,
            approval_action=action.value,
            closes_at=closes_at.isoformat(),
        )
        return approval_id

    async def __load_committee(self) -> sql.Committee:
        return await self.__data.committee(key=self.__committee_key).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )

    async def __pending_vote_mode_request(self, approval_id: int) -> sql.ApprovalRequest:
        approval = await self.__data.approval_request(
            id=approval_id,
            committee_key=self.__committee_key,
            project_key=None,
            cap_question_id=None,
            status=sql.ApprovalStatus.PENDING,
        ).get()
        if approval is None:
            raise storage.AccessError(f"Approval request {approval_id} is no longer waiting for a CAP vote", status=409)
        return approval


class FoundationAdmin(CommitteeMember):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeAdmin,
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

    async def archive(self, archived: datetime.datetime | None) -> None:
        """Retire the committee, dating the retirement where we know the date."""
        await self.__data.begin_immediate()
        try:
            committee = await self.__data.committee(key=self.__committee_key).demand(
                storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
            )
            committee.is_archived = True
            committee.archived = archived
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            archived=archived.isoformat() if (archived is not None) else None,
        )

    async def catalog_reviewed_mark(self) -> None:
        """Record that the PMC has reviewed the catalogue we seeded for them.

        This is one-way. Once a catalogue is marked reviewed, there is no path back.
        If we need to "unmark" it can be done in the database.
        """
        await self.__data.begin_immediate()
        try:
            committee = await self.__data.committee(key=self.__committee_key).demand(
                storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
            )
            committee.catalog_reviewed = True
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            catalog_reviewed=True,
        )

    async def restore(self) -> None:
        await self.__data.begin_immediate()
        try:
            committee = await self.__data.committee(key=self.__committee_key).demand(
                storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
            )
            if not committee.is_archived:
                raise storage.AccessError(f"Committee is not retired: {self.__committee_key}", status=409)
            committee.is_archived = False
            committee.archived = None
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
        )

    async def roster_person_remove(self, asf_uid: str) -> None:
        asf_uid = asf_uid.strip().lower()
        await self.__data.begin_immediate()
        try:
            committee = await self.__roster_committee()
            for attr in ("committee_members", "committers", "release_managers"):
                # JSON columns do not track in place mutation, so assign a new list
                setattr(committee, attr, [uid for uid in getattr(committee, attr) if uid != asf_uid])
            await self.__delete_stray_users({asf_uid})
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            roster_uid=asf_uid,
        )

    async def roster_person_set(self, asf_uid: str, fullname: str, pmc: bool) -> None:
        asf_uid = asf_uid.strip().lower()
        if not asf_uid:
            raise storage.AccessError("An ASF UID is required", status=400)
        await self.__data.begin_immediate()
        try:
            committee = await self.__roster_committee()
            committee.committers = sorted({*committee.committers, asf_uid})
            members = {*committee.committee_members, asf_uid} if pmc else {*committee.committee_members} - {asf_uid}
            committee.committee_members = sorted(members)
            user = await self.__data.user(asf_uid=asf_uid).get()
            if user is None:
                self.__data.add(sql.User(asfuid=asf_uid, name=fullname or None))
            elif fullname:
                user.name = fullname
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            roster_uid=asf_uid,
            fullname=fullname,
            pmc=pmc,
        )

    async def roster_reset(self) -> None:
        await self.__data.begin_immediate()
        try:
            committee = await self.__roster_committee()
            removed = {*committee.committee_members, *committee.committers, *committee.release_managers} - {"test"}
            # The pristine roster, as created by server._initialise_test_environment
            committee.committee_members = ["test"]
            committee.committers = ["test"]
            committee.release_managers = ["test"]
            await self.__delete_stray_users(removed)
            committee.mark_updated(by=self.__asf_uid, update_type=sql.UpdateType.MANUAL)
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            committee_key=self.__committee_key,
            removed=[uid for uid in sorted(removed)],
        )

    async def __delete_stray_users(self, asf_uids: set[str]) -> None:
        committees = await self.__data.committee().all()
        keep = {
            uid
            for committee in committees
            if committee.key != "test"
            for uid in (*committee.committee_members, *committee.committers, *committee.release_managers)
        }
        for asf_uid in asf_uids - keep - {"test"}:
            if user := await self.__data.user(asf_uid=asf_uid).get():
                await self.__data.delete(user)

    async def __roster_committee(self) -> sql.Committee:
        if self.__committee_key != "test":
            raise storage.AccessError("Roster editing is only available for the test committee", status=403)
        return await self.__data.committee(key=self.__committee_key).demand(
            storage.AccessError(f"Committee not found: {self.__committee_key}", status=404)
        )


def _github_uploads_notice(
    committee_key: str, asf_uid: str, enabled: bool, repositories: dict[str, list[str]]
) -> args.Send:
    committee_url = util.absolute_url(f"/committees/{committee_key}")
    if enabled:
        subject = f"[ATR] Uploads from GitHub Actions allowed for {committee_key}"
        listed = [
            f"  {project_key}: {', '.join(names) or 'none'}" for project_key, names in sorted(repositories.items())
        ]
        body = (
            f"{asf_uid} has allowed uploads from GitHub Actions for the {committee_key} PMC.\n\n"
            "A workflow in one of a project's repositories can now upload files to that project's drafts, "
            "when a PMC member or release manager starts it. These are the repositories that applies to:\n\n"
            + "\n".join(listed)
            + f"\n\nTo stop this, or to review it, see {committee_url}\n"
        )
    else:
        subject = f"[ATR] Uploads from GitHub Actions stopped for {committee_key}"
        body = (
            f"{asf_uid} has stopped uploads from GitHub Actions for the {committee_key} PMC.\n\n"
            f"To allow them again, see {committee_url}\n"
        )
    return args.Send(
        email_sender=mail.NOREPLY_EMAIL_ADDRESS,
        email_to=f"private@{committee_key}.apache.org",
        subject=subject,
        body=body,
        in_reply_to=None,
        footer_category=mail.MailFooterCategory.AUTO,
    )
