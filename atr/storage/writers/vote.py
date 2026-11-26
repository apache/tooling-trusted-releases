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
from typing import Final, Literal

import sqlalchemy
import sqlmodel

import atr.config as config
import atr.constants as constants
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.log as log
import atr.mail as mail
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.tabulate as tabulate
import atr.user as user
import atr.util as util

_RESOLVED_RELEASE_ATTRIBUTES: Final[list[str]] = [
    "activity_at",
    "current_vote_seq",
    "inactivity_notice_key",
    "phase",
    "podling_thread_id",
    "vote_resolved",
    "vote_thread_url",
    "voted_revision_number",
]


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

    async def cast(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        choice: sql.VoteChoice,
        comment: str,
        fullname: str,
        expected_vote_seq: int | None = None,
    ) -> tuple[list[str], str]:
        """Record an ATR BallotPaper and queue its mailing-list receipt email."""
        if (choice == sql.VoteChoice.NO) and (not comment.strip()):
            raise storage.AccessError("A comment is required when voting -1.", status=400)
        await self.__data.begin_immediate()
        try:
            self.__data.expire_all()
            release = await self.__data.release(
                project_key=str(project_key),
                version=str(version_key),
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                _project=True,
                _committee=True,
            ).get()
            if release is None:
                raise storage.AccessError("Vote is no longer open.", status=409)
            storage.ensure_project_active(release.project)
            self.__write.ensure_release_writable(release)
            vote_seq = _open_vote_seq(release, expected_vote_seq)
            committee = _release_committee(release)
            if release.latest_revision_number is None:
                raise storage.AccessError("No revision found for this release.", status=409)
            current_vote_task, start_mid, subject = await self.__announcement(release)
            email_to = current_vote_task.task_args.get("email_to")
            if (not isinstance(email_to, str)) or (not email_to.strip()):
                raise storage.AccessError("The vote announcement has no recipient.", status=500)
            vote_round = interaction.vote_round_for_release(release)
            is_binding, _ = await user.is_binding_for_release(
                committee, self.__asf_uid, vote_round, caller_data=self.__data
            )
            previous_ballot = await interaction.latest_ballot_for_voter(
                release.key, vote_seq, self.__asf_uid, caller_data=self.__data
            )
            receipt_message_id = mail.message_id_create()
            body = format_vote_email_body(
                choice.value, self.__asf_uid, fullname, is_binding, comment, _vote_potency_label(release, is_binding)
            )
            task = sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.MESSAGE_SEND,
                task_args=args.Send(
                    email_sender=f"{self.__asf_uid}@apache.org",
                    email_to=email_to,
                    email_cc=current_vote_task.task_args.get("email_cc", []),
                    email_bcc=current_vote_task.task_args.get("email_bcc", []),
                    subject=subject,
                    body=body,
                    in_reply_to=start_mid,
                    message_id=receipt_message_id,
                    footer_category=mail.MailFooterCategory.USER,
                ).as_task_args(),
                asf_uid=self.__asf_uid,
                project_key=release.project_key,
                version_key=release.version,
            )
            ballot = sql.BallotPaper(
                release_key=release.key,
                vote_seq=vote_seq,
                vote_round=vote_round,
                voter_asf_uid=self.__asf_uid,
                voter_fullname=fullname,
                choice=choice,
                comment=comment,
                is_binding_at_cast=is_binding,
                revision_number_at_cast=release.latest_revision_number,
                receipt_message_id=receipt_message_id,
            )
            self.__data.add_all([task, ballot])
            await self.__data.flush()
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            ballot_id=ballot.id,
            release_key=release.key,
            vote_seq=vote_seq,
            vote_round=vote_round,
            voter_asf_uid=self.__asf_uid,
            choice=choice.value,
            is_binding_at_cast=is_binding,
            receipt_message_id=receipt_message_id,
            replaced_ballot_id=previous_ballot.id if (previous_ballot is not None) else None,
        )
        return [email_to], receipt_message_id

    async def __announcement(self, release: sql.Release) -> tuple[sql.Task, str, str]:
        task = await interaction.release_current_vote_task(release, self.__data)
        start_mid = interaction.task_mid_get(task) if (task is not None) else None
        if (task is None) or (start_mid is None) or (not isinstance(task.result, results.VoteInitiate)):
            raise storage.AccessError(
                "The vote announcement email has not been sent yet; please try again shortly", status=409
            )
        return task, start_mid, f"Re: {task.result.subject}"


class FoundationAdmin(FoundationCommitter):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session):
        super().__init__(write, write_as, data)
        self.__write_as = write_as

    def presentations_bypass_set(self, enabled: bool) -> None:
        conf = config.get()
        previous = conf.PRESENTATIONS_VOTE_RESOLUTION_BYPASS
        if previous == enabled:
            return
        conf.PRESENTATIONS_VOTE_RESOLUTION_BYPASS = enabled
        self.__write_as.append_to_audit_log(asf_uid=self.__write_as.asf_uid, previous=previous, enabled=enabled)


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

    async def resolve(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        vote_result: Literal["passed", "failed", "cancelled"],
        asf_fullname: str,
        resolution_body: str | None = None,
        expected_vote_seq: int | None = None,
        notify_when_finished: bool = False,
        additional_cc: list[str] | None = None,
        additional_bcc: list[str] | None = None,
        early_reason: str = "",
        *,
        unattended: bool = False,
    ) -> tuple[sql.Release, int | None, str, str | None]:
        """Resolve a candidate vote from the unified official tally (BallotPaper + confirmed VoteInterpretation)."""
        security_override = isinstance(self, SecurityTeam)
        release = await self.__resolution_release(project_key, version_key)
        if security_override:
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                version_key=release.version,
                revision_number=release.latest_revision_number,
                vote_seq=release.current_vote_seq,
                vote_result=vote_result,
                security_override_requested=True,
            )
        pre_vote_task = await interaction.release_current_vote_task(release, self.__data)
        if security_override:
            _require_announced(pre_vote_task)
        additional_cc, additional_bcc = self.__resolution_recipients(release, additional_cc, additional_bcc)
        _check_resolution_seq(release, expected_vote_seq)
        # The archive URL and templates belong to the round observed before locking
        pre_vote_state = _resolution_state(release, pre_vote_task)
        second_round_body_template, finish_vote_template = await _resolution_templates(
            release, vote_result, resolution_body
        )
        archive_url = None if release.expedited else await self.__resolved_vote_thread_url(pre_vote_task)
        thread_id = archive_url.split("/")[-1] if (archive_url is not None) else None
        await self.__data.rollback()
        await self.__data.begin_immediate()
        try:
            self.__data.expire_all()
            release = await self.__resolution_release(project_key, version_key)
            vote_seq = release.current_vote_seq
            if vote_seq is None:
                raise storage.AccessError("Vote serial is missing, please refresh and try again", status=409)
            _check_resolution_seq(release, expected_vote_seq)
            committee = _release_committee(release)
            voting_round = interaction.vote_round_for_release(release)
            current_vote_task = await interaction.release_current_vote_task(release, self.__data)
            if _resolution_state(release, current_vote_task) != pre_vote_state:
                raise storage.AccessError("The resolve form is stale, please refresh and try again", status=409)
            resolution_bypass = security_override or interaction.vote_resolution_bypass(release, self.__asf_uid)
            completed_early_reason = await self.__resolution_early_reason(
                committee, voting_round, vote_result, current_vote_task, resolution_bypass, early_reason
            )
            counted_details, summary = await interaction.counted_vote_details_and_summary(
                release, vote_seq, self.__data
            )
            binding_label, non_binding_label = user.binding_terminology(voting_round)
            _check_passing_tally(release, vote_result, summary, binding_label, resolution_bypass, unattended)
            tally_block = tabulate.vote_tally_block(
                summary,
                binding_label,
                non_binding_label,
                thread_id=thread_id,
                podling_thread_id=release.podling_thread_id,
                ballots=counted_details,
            )
            rendered_body = _render_resolution_body(
                release,
                committee,
                vote_result,
                resolution_body,
                finish_vote_template,
                tally_block,
                self.__asf_uid,
                asf_fullname,
            )
            success_message, second_round_vote_seq = await self.__transition_vote(
                release,
                voting_round,
                vote_result,
                current_vote_task,
                asf_fullname,
                archive_url,
                thread_id,
                second_round_body_template,
                notify_when_finished,
            )
            await self.__data.commit()
            await self.__data.refresh(release, attribute_names=_RESOLVED_RELEASE_ATTRIBUTES)
        except Exception:
            await self.__data.rollback()
            raise
        error_message = await self.__resolution_notice(
            release,
            project_key,
            voting_round,
            vote_result,
            rendered_body,
            current_vote_task,
            additional_cc,
            additional_bcc,
            completed_early_reason,
        )
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            release_key=release.key,
            project_key=str(project_key),
            version_key=release.version,
            revision_number=release.latest_revision_number,
            vote_result=vote_result,
            voting_round=voting_round,
            vote_seq=vote_seq,
            second_round_vote_seq=second_round_vote_seq,
            early_reason=completed_early_reason,
            security_override=security_override,
            binding_yes=summary.binding_votes_yes,
            binding_no=summary.binding_votes_no,
            binding_abstain=summary.binding_votes_abstain,
            non_binding_yes=summary.non_binding_votes_yes,
            non_binding_no=summary.non_binding_votes_no,
            non_binding_abstain=summary.non_binding_votes_abstain,
        )
        return release, voting_round, success_message, error_message

    async def start(
        self,
        email_to: str,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        vote_duration_choice: int,
        subject: str,
        body_data: str,
        asf_fullname: str,
        promote: bool = True,
        permitted_recipients: list[str] | None = None,
        email_cc: list[str] | None = None,
        email_bcc: list[str] | None = None,
        second_round_email_to: str | None = None,
        expected_revision: safe.RevisionNumber | None = None,
        notify_when_finished: bool = False,
        automatic_publish_when_resolved: bool = False,
        automatic_publish_asf_uid: str | None = None,
        download_path_suffix: safe.RelPath | None = None,
        acknowledged_concerns: frozenset[str] = frozenset(),
    ) -> sql.Task:
        """Promote a release draft (or start podling round 2) and queue VOTE_INITIATE."""
        if promote:
            await self.__data.begin_immediate()
        try:
            release, vote_seq, revision_number = await self.__start_release(
                project_key, version_key, expected_revision, promote, acknowledged_concerns
            )
            committee = await self._committee_for_release(release)
            if committee is None:
                raise storage.AccessError(errors.RELEASE_NO_COMMITTEE, status=500)
            if release.expedited:
                self.__check_expedited_start(committee, automatic_publish_when_resolved)
                email_to = f"private@{committee.key}.apache.org"
                email_cc = []
                email_bcc = []
                second_round_email_to = None
                vote_duration_choice = 0
                permitted_recipients = [email_to]
                notify_when_finished = False
                download_path_suffix = None
            else:
                automatic_publish_asf_uid = self.__start_publish(
                    release,
                    committee,
                    promote,
                    automatic_publish_when_resolved,
                    automatic_publish_asf_uid,
                    download_path_suffix,
                )
            email_cc = email_cc or []
            email_bcc = email_bcc or []
            self.__check_start_recipients(
                release,
                committee,
                [email_to, *email_cc, *email_bcc],
                permitted_recipients,
                second_round_email_to,
            )
            task = sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.VOTE_INITIATE,
                task_args=args.Initiate(
                    release_key=release.key,
                    email_to=email_to,
                    email_cc=email_cc,
                    email_bcc=email_bcc,
                    second_round_email_to=second_round_email_to,
                    vote_duration=vote_duration_choice,
                    initiator_id=self.__asf_uid,
                    initiator_fullname=asf_fullname,
                    subject=subject,
                    body=body_data,
                    vote_seq=vote_seq,
                    notify_when_finished=notify_when_finished,
                    automatic_publish_when_resolved=automatic_publish_when_resolved,
                    automatic_publish_asf_uid=automatic_publish_asf_uid,
                    download_path_suffix=download_path_suffix,
                ).model_dump(),
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                version_key=str(version_key),
            )
            self.__data.add(task)
            if not promote:
                return task
            await self.__data.commit()
        except Exception:
            if promote:
                await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid, release_key=release.key, revision_number=str(revision_number), vote_seq=vote_seq
        )
        return task

    async def start_second_round(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        asf_fullname: str,
        expected_vote_seq: int,
    ) -> sql.Task:
        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _project=True,
            _committee=True,
            _project_release_policy=True,
        ).demand(storage.AccessError("Release not found", status=404))
        if (release.committee is None) or (not release.committee.is_podling) or (release.podling_thread_id is not None):
            raise storage.AccessError("Only a podling first round vote can start a second round vote", status=409)
        if release.current_vote_seq != expected_vote_seq:
            raise storage.AccessError("The resolve form is stale, please refresh and try again", status=409)
        latest_vote_task = await interaction.release_current_vote_task(release, self.__data)
        archive_url = await self.__resolved_vote_thread_url(latest_vote_task)
        if (latest_vote_task is None) or (archive_url is None):
            raise storage.AccessError("The first round vote thread is not archived yet, please try later", status=409)
        body_template = (await construct.resolve_template(release.project, "start_vote")).body
        await self.__data.commit()
        await self.__data.begin_immediate()
        try:
            if await interaction.release_early_second_round_task(release, self.__data) is not None:
                raise storage.AccessError("The second round vote has already been started", status=409)
            task = await self.__queue_second_round(
                release, latest_vote_task, asf_fullname, archive_url.split("/")[-1], body_template
            )
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            release_key=release.key,
            vote_seq=task.task_args["vote_seq"],
        )
        return task

    async def _cancel_pending_vote_followups(self, release: sql.Release) -> None:
        via = sql.validate_instrumented_attribute
        stmt = (
            sqlmodel.update(sql.Task)
            .where(
                via(sql.Task.task_type) == sql.TaskType.VOTE_END_NOTIFY,
                via(sql.Task.status) == sql.TaskStatus.QUEUED,
                via(sql.Task.project_key) == release.project.key,
                via(sql.Task.version_key) == release.version,
            )
            .values(
                status=sql.TaskStatus.FAILED,
                completed=datetime.datetime.now(datetime.UTC),
                error="Vote resolved before reminder fired",
            )
        )
        await self.__data.execute(stmt)

    async def _committee_for_release(self, release: sql.Release) -> sql.Committee | None:
        project = await self.__data.project(key=release.project_key, _committee=True).get()
        if project is None:
            return None
        return project.committee

    async def _confirmation_thread_id(self, task: sql.Task | None) -> str:
        archive_url = await self.__resolved_vote_thread_url(task)
        if archive_url is None:
            raise storage.AccessError("The vote thread is not archived yet", status=409)
        return archive_url.split("/")[-1]

    async def _resolve_transition(
        self,
        release: sql.Release,
        *,
        expected_phase: sql.ReleasePhase,
        expected_podling_thread_id: str | None,
        new_phase: sql.ReleasePhase,
        new_vote_resolved: datetime.datetime | None,
        new_podling_thread_id: str | None,
        new_vote_thread_url: str | None = None,
        new_current_vote_seq: int | None = None,
    ) -> None:
        via = sql.validate_instrumented_attribute
        stmt = sqlmodel.update(sql.Release).where(
            via(sql.Release.key) == release.key,
            via(sql.Release.phase) == expected_phase,
            via(sql.Release.current_vote_seq).is_not_distinct_from(release.current_vote_seq),
        )
        if expected_podling_thread_id is None:
            stmt = stmt.where(via(sql.Release.podling_thread_id).is_(None))
        else:
            stmt = stmt.where(via(sql.Release.podling_thread_id) == expected_podling_thread_id)
        now = datetime.datetime.now(datetime.UTC)
        release_activity_at = via(sql.Release.activity_at)
        activity_at_value = sqlalchemy.case((release_activity_at > now, release_activity_at), else_=now)
        notice_key_value = sqlalchemy.case(
            (release_activity_at > now, via(sql.Release.inactivity_notice_key)),
            else_=None,
        )
        if new_phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            stmt = stmt.values(voted_revision_number=None)
        if new_current_vote_seq is not None:
            stmt = stmt.values(current_vote_seq=new_current_vote_seq)
        result = await self.__data.execute(
            stmt.values(
                phase=new_phase,
                vote_resolved=new_vote_resolved,
                podling_thread_id=new_podling_thread_id,
                vote_thread_url=new_vote_thread_url,
                activity_at=activity_at_value,
                inactivity_notice_key=notice_key_value,
            )
        )
        if getattr(result, "rowcount", 0) != 1:
            await self.__data.rollback()
            raise storage.AccessError("The release state has changed, please refresh and try again", status=409)
        await self._cancel_pending_vote_followups(release)
        if new_phase == sql.ReleasePhase.RELEASE_PREVIEW:
            await self.__write_as.distributions.expect_for_release_no_commit(release.safe_key)

    def __check_expedited_start(self, committee: sql.Committee, automatic_publish_when_resolved: bool) -> None:
        if not self.__write.authorisation.is_member_of(committee.key):
            raise storage.AccessError("Expedited releases require a committee member initiator", status=403)
        if automatic_publish_when_resolved:
            raise storage.AccessError("Automatic SVN publish is not available for expedited releases", status=403)

    def __check_start_recipients(
        self,
        release: sql.Release,
        committee: sql.Committee,
        recipients: list[str],
        permitted: list[str] | None,
        second_round_email_to: str | None,
    ) -> None:
        if permitted is None:
            permitted = util.permitted_podling_first_round_recipients(
                self.__asf_uid, committee.key, is_podling=committee.is_podling, project=release.project
            )
        if not set(recipients).issubset(permitted):
            raise storage.AccessError("Invalid mailing list choice", status=403)
        if (second_round_email_to is not None) and (
            second_round_email_to not in util.permitted_podling_second_round_recipients(self.__asf_uid)
        ):
            raise storage.AccessError("Second round mailing list choice is not permitted", status=403)

    async def __early_completion_reason(
        self,
        committee: sql.Committee,
        voting_round: int | None,
        vote_result: Literal["passed", "failed", "cancelled"],
        vote_end: datetime.datetime | None,
        early_reason: str,
    ) -> str:
        reason = early_reason.strip()
        if (vote_result != "passed") or (vote_end is None):
            raise storage.AccessError(
                "The vote cannot be resolved before the voting period has ended unless it is cancelled.",
                status=409,
            )
        if not reason:
            raise storage.AccessError(
                "A reason is required to complete a vote before the voting period has ended", status=409
            )
        if len(reason) > constants.EARLY_VOTE_REASON_MAX_LENGTH:
            raise storage.AccessError(
                f"The early completion reason must be at most {constants.EARLY_VOTE_REASON_MAX_LENGTH} characters",
                status=400,
            )
        is_binding, _binding_committee = await user.is_binding_for_release(
            committee, self.__asf_uid, voting_round, caller_data=self.__data
        )
        if not is_binding:
            raise storage.AccessError("Only binding voters can complete this vote early", status=403)
        return reason

    async def __enqueue_automatic_svn_publish(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        revision_number: safe.RevisionNumber,
        vote_task_with_publish_options: sql.Task | None,
        release: sql.Release,
    ) -> str | None:
        security_override = isinstance(self, SecurityTeam)
        if vote_task_with_publish_options is None:
            return None
        if (not security_override) and (
            not bool(vote_task_with_publish_options.task_args.get("automatic_publish_when_resolved", False))
        ):
            return None
        try:
            download_path_suffix_raw = vote_task_with_publish_options.task_args.get("download_path_suffix")
            download_path_suffix = (
                safe.RelPath(download_path_suffix_raw) if isinstance(download_path_suffix_raw, str) else None
            )
            if security_override:
                download_path_suffix = construct.effective_download_path_suffix(release)
            publisher_asf_uid = vote_task_with_publish_options.task_args.get("automatic_publish_asf_uid")
            if not isinstance(publisher_asf_uid, str):
                publisher_asf_uid = vote_task_with_publish_options.task_args.get("initiator_id")
            if not isinstance(publisher_asf_uid, str):
                publisher_asf_uid = self.__asf_uid
            await self.__write_as.release.publish_to_svn(
                project_key,
                version_key,
                revision_number,
                download_path_suffix,
                publisher_asf_uid=publisher_asf_uid,
            )
        except Exception as exc:
            log.warning(f"Automatic SVN publish enqueue skipped for {project_key!s} {version_key!s}: {exc}")
            await self.__data.refresh(release, attribute_names=["download_path_suffix"])
            await self.__data.refresh(vote_task_with_publish_options)
            return f"Automatic SVN publish was not queued: {exc}"
        return None

    async def __pass_first_round(
        self,
        release: sql.Release,
        latest_vote_task: sql.Task,
        asf_fullname: str,
        thread_id: str,
        second_round_body_template: str | None,
        *,
        notify_when_finished: bool,
    ) -> sql.Task:
        early_second_round_task = await interaction.release_early_second_round_task(release, self.__data)
        await self._resolve_transition(
            release,
            expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            expected_podling_thread_id=None,
            new_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            new_vote_resolved=None,
            new_podling_thread_id=thread_id,
            new_current_vote_seq=(
                early_second_round_task.task_args["vote_seq"] if (early_second_round_task is not None) else None
            ),
        )
        await self.__data.refresh(release, attribute_names=_RESOLVED_RELEASE_ATTRIBUTES)
        if early_second_round_task is not None:
            return early_second_round_task
        return await self.__queue_second_round(
            release,
            latest_vote_task,
            asf_fullname,
            thread_id,
            second_round_body_template,
            notify_when_finished=notify_when_finished,
        )

    async def __queue_second_round(
        self,
        release: sql.Release,
        latest_vote_task: sql.Task,
        asf_fullname: str,
        thread_id: str,
        second_round_body_template: str | None,
        *,
        notify_when_finished: bool = False,
    ) -> sql.Task:
        security_override = isinstance(self, SecurityTeam)
        incubator_vote_address = (
            latest_vote_task.task_args.get("second_round_email_to") or util.INCUBATOR_GENERAL_ADDRESS
        )
        if not release.project.committee:
            raise storage.AccessError("Project has no committee - Invalid state", status=500)
        if release.latest_revision_number is None:
            raise storage.AccessError("Release has no revision number - Invalid state", status=500)
        vote_duration = latest_vote_task.task_args["vote_duration"]
        subject_template = await construct.start_vote_subject_default(release.safe_project_key)
        if second_round_body_template is None:
            raise storage.AccessError("Second round vote template was not resolved", status=500)
        options = construct.StartVoteOptions(
            asfuid=self.__asf_uid,
            fullname=asf_fullname,
            project_key=release.safe_project_key,
            version_key=release.safe_version_key,
            revision_number=release.safe_latest_revision_number,
            vote_duration=vote_duration,
            first_round_thread_id=thread_id,
        )
        subject_data, body_data = await construct.start_vote_subject_and_body(
            subject_template, second_round_body_template, options
        )
        carried_publish_when_resolved = (not security_override) and bool(
            latest_vote_task.task_args.get("automatic_publish_when_resolved", False)
        )
        carried_publish_asf_uid = _carried_publish_uid(latest_vote_task, self.__asf_uid)
        carried_download_path_suffix_raw = latest_vote_task.task_args.get("download_path_suffix")
        carried_download_path_suffix = (
            safe.RelPath(carried_download_path_suffix_raw)
            if isinstance(carried_download_path_suffix_raw, str)
            else None
        )
        return await self.start(
            email_to=incubator_vote_address,
            permitted_recipients=[incubator_vote_address],
            project_key=release.safe_project_key,
            version_key=release.safe_version_key,
            asf_fullname=asf_fullname,
            vote_duration_choice=vote_duration,
            subject=subject_data,
            body_data=body_data,
            promote=False,
            expected_revision=release.safe_latest_revision_number,
            notify_when_finished=notify_when_finished,
            automatic_publish_when_resolved=carried_publish_when_resolved,
            automatic_publish_asf_uid=carried_publish_asf_uid,
            download_path_suffix=carried_download_path_suffix,
        )

    def __queue_send(
        self,
        release: sql.Release,
        *,
        email_to: str,
        subject: str,
        body: str,
        in_reply_to: str,
        email_cc: list[str] | None = None,
        email_bcc: list[str] | None = None,
    ) -> sql.Task:
        return sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.MESSAGE_SEND,
            task_args=args.Send(
                email_sender=f"{self.__asf_uid}@apache.org",
                email_to=email_to,
                subject=subject,
                body=body,
                in_reply_to=in_reply_to,
                email_cc=email_cc or [],
                email_bcc=email_bcc or [],
                footer_category=mail.MailFooterCategory.USER,
            ).as_task_args(),
            asf_uid=self.__asf_uid,
            project_key=release.project.key,
            version_key=release.version,
        )

    async def __resolution_early_reason(
        self,
        committee: sql.Committee,
        voting_round: int | None,
        vote_result: Literal["passed", "failed", "cancelled"],
        current_vote_task: sql.Task | None,
        resolution_bypass: bool,
        early_reason: str,
    ) -> str | None:
        vote_end = interaction.vote_end_get(current_vote_task)
        if (
            (vote_result != "cancelled")
            and (vote_end is not None)
            and (not interaction.vote_pass_fail_allowed(current_vote_task))
            and (not resolution_bypass)
        ):
            return await self.__early_completion_reason(committee, voting_round, vote_result, vote_end, early_reason)
        return None

    async def __resolution_notice(
        self,
        release: sql.Release,
        project_key: safe.ProjectKey,
        voting_round: int | None,
        vote_result: str,
        rendered_body: str,
        current_vote_task: sql.Task | None,
        additional_cc: list[str] | None,
        additional_bcc: list[str] | None,
        early_reason: str | None,
    ) -> str | None:
        publish_enqueue_error = None
        extra_destination = None
        if (vote_result == "passed") and (voting_round != 1):
            publish_enqueue_error = await self.__enqueue_automatic_svn_publish(
                project_key, release.safe_version_key, release.safe_latest_revision_number, current_vote_task, release
            )
            if (voting_round == 2) and (release.podling_thread_id is not None):
                extra_destination = await util.email_mid_from_thread_id(release.podling_thread_id)
        if current_vote_task is None:
            error_message = "No vote task found, unable to send resolution message."
        else:
            error_message = await self.__send_resolution(
                release,
                vote_result,
                rendered_body,
                current_vote_task,
                extra_destination=extra_destination,
                additional_cc=additional_cc,
                additional_bcc=additional_bcc,
                early_reason=early_reason,
            )
        if error_message and publish_enqueue_error:
            return f"{error_message}; {publish_enqueue_error}"
        return error_message or publish_enqueue_error

    def __resolution_recipients(
        self, release: sql.Release, cc: list[str] | None, bcc: list[str] | None
    ) -> tuple[list[str], list[str]]:
        if release.expedited:
            return [], []
        cc = cc or []
        bcc = bcc or []
        if cc or bcc:
            permitted = util.permitted_voting_recipients(
                self.__asf_uid, _release_committee(release).key, project=release.project
            )
            for address in [*cc, *bcc]:
                if address not in permitted:
                    raise storage.AccessError(f"Invalid recipient selection: {address}", status=400)
        return cc, bcc

    async def __resolution_release(self, project_key: safe.ProjectKey, version_key: safe.VersionKey) -> sql.Release:
        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _project=True,
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(storage.AccessError("Release not found", status=404))
        storage.ensure_project_active(release.project)
        if not isinstance(self, SecurityTeam):
            self.__write.ensure_release_writable(release)
        return release

    async def __resolved_vote_thread_url(self, vote_task: sql.Task | None) -> str | None:
        if vote_task is None:
            return None
        task_mid = interaction.task_mid_get(vote_task)
        task_recipient = interaction.task_recipient_get(vote_task)
        return await self.__write_as.cache.get_message_archive_url(task_mid, task_recipient)

    async def __send_resolution(
        self,
        release: sql.Release,
        resolution: str,
        body: str,
        latest_vote_task: sql.Task,
        extra_destination: tuple[str, str] | None = None,
        additional_cc: list[str] | None = None,
        additional_bcc: list[str] | None = None,
        early_reason: str | None = None,
    ) -> str | None:
        storage.ensure_project_active(release.project)
        vote_thread_mid = interaction.task_mid_get(latest_vote_task)
        if vote_thread_mid is None:
            return "No vote thread found, unable to send resolution message."
        if early_reason is not None:
            body = (
                f"{body.rstrip()}\n\nThis vote was completed before the end of its voting period,"
                f" for the following reason:\n\n{early_reason}\n"
            )
        email_to: str = latest_vote_task.task_args["email_to"]
        email_cc: list[str] = latest_vote_task.task_args.get("email_cc", [])
        email_bcc: list[str] = latest_vote_task.task_args.get("email_bcc", [])
        seen = {addr.lower() for addr in [email_to, *email_cc, *email_bcc]}
        email_cc = _extend_unique(email_cc, additional_cc, seen)
        email_bcc = _extend_unique(email_bcc, additional_bcc, seen)
        subject = tabulate.vote_result_subject(release, resolution)
        tasks_to_add = [
            self.__queue_send(
                release,
                email_to=email_to,
                subject=subject,
                body=body,
                in_reply_to=vote_thread_mid,
                email_cc=email_cc,
                email_bcc=email_bcc,
            )
        ]
        if extra_destination is not None:
            tasks_to_add.append(
                self.__queue_send(
                    release,
                    email_to=extra_destination[0],
                    subject=subject,
                    body=body,
                    in_reply_to=extra_destination[1],
                )
            )
        self.__data.add_all(tasks_to_add)
        await self.__data.flush()
        await self.__data.commit()
        return None

    def __start_publish(
        self,
        release: sql.Release,
        committee: sql.Committee,
        promote: bool,
        automatic_publish_when_resolved: bool,
        publisher: str | None,
        download_path_suffix: safe.RelPath | None,
    ) -> str | None:
        if not automatic_publish_when_resolved:
            return publisher
        if promote and (not self.__write.authorisation.is_member_of(committee.key)):
            raise storage.AccessError("Automatic SVN publish requires a committee member initiator", status=403)
        release.download_path_suffix = str(download_path_suffix) if (download_path_suffix is not None) else ""
        self.__data.add(release)
        return publisher or self.__asf_uid

    async def __start_release(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        expected_revision: safe.RevisionNumber | None,
        promote: bool,
        acknowledged_concerns: frozenset[str],
    ) -> tuple[sql.Release, int, safe.RevisionNumber]:
        if promote:
            self.__data.expire_all()
        release = await self.__data.release(
            project_key=str(project_key), version=str(version_key), _project=True, _committee=True
        ).demand(storage.AccessError("Release not found", status=404))
        storage.ensure_project_active(release.project)
        if release.expedited and (release.committee is not None) and release.committee.is_podling:
            raise storage.AccessError("Expedited releases are not available for podling projects", status=400)
        if promote:
            return await self.__write_as.release.start_vote_no_commit(
                safe.ReleaseKey(release.key),
                expected_revision,
                promote=True,
                acknowledged_concerns=acknowledged_concerns,
            )
        return await self.__write_as.release.start_vote_no_commit(
            release.safe_key, expected_revision, promote=False, expected_podling_thread_id=release.podling_thread_id
        )

    async def __transition_vote(
        self,
        release: sql.Release,
        voting_round: int | None,
        vote_result: str,
        current_vote_task: sql.Task | None,
        asf_fullname: str,
        archive_url: str | None,
        thread_id: str | None,
        second_round_body_template: str | None,
        notify_when_finished: bool,
    ) -> tuple[str, int | None]:
        if (voting_round == 1) and (vote_result == "passed"):
            if current_vote_task is None:
                raise storage.AccessError("No vote task found, unable to start the Incubator vote.", status=404)
            if thread_id is None:
                raise storage.AccessError("No archive URL found for podling vote", status=404)
            task = await self.__pass_first_round(
                release,
                current_vote_task,
                asf_fullname,
                thread_id,
                second_round_body_template,
                notify_when_finished=notify_when_finished,
            )
            vote_seq = task.task_args["vote_seq"]
            if not isinstance(vote_seq, int):
                raise storage.AccessError("Second round vote sequence is invalid", status=500)
            destination = task.task_args["email_to"]
            return f"First round vote marked as passed, and second round vote started (sent to {destination})", vote_seq
        if vote_result == "passed":
            await self._resolve_transition(
                release,
                expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                expected_podling_thread_id=release.podling_thread_id,
                new_phase=sql.ReleasePhase.RELEASE_PREVIEW,
                new_vote_resolved=datetime.datetime.now(datetime.UTC),
                new_podling_thread_id=release.podling_thread_id,
                new_vote_thread_url=archive_url,
            )
        else:
            await self._resolve_transition(
                release,
                expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                expected_podling_thread_id=release.podling_thread_id,
                new_phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                new_vote_resolved=None,
                new_podling_thread_id=None,
            )
        return f"Vote marked as {vote_result}", None


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

    async def confirm_email_vote(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        asf_eid: str,
        choice: sql.VoteChoice | None,
        expected_vote_seq: int | None = None,
    ) -> None:
        """Confirm or unconfirm a heuristic mailing-list email vote in VoteInterpretation."""
        if choice is None:
            await self.__data.begin_immediate()
            try:
                self.__data.expire_all()
                release, vote_seq, _ = await self.__confirmation_release(project_key, version_key, expected_vote_seq)
                via = sql.validate_instrumented_attribute
                await self.__data.execute(
                    sqlmodel.delete(sql.VoteInterpretation).where(
                        via(sql.VoteInterpretation.release_key) == release.key,
                        via(sql.VoteInterpretation.vote_seq) == vote_seq,
                        via(sql.VoteInterpretation.asf_eid) == asf_eid,
                    )
                )
                await self.__data.commit()
            except Exception:
                await self.__data.rollback()
                raise
        else:
            release, vote_seq, committee_key = await self.__confirmation_release(
                project_key, version_key, expected_vote_seq
            )
            current_vote_task = await interaction.release_current_vote_task(release, self.__data)
            task_id = current_vote_task.id if (current_vote_task is not None) else None
            if task_id is None:
                raise storage.AccessError("No current vote task found", status=409)
            thread_id = await self._confirmation_thread_id(current_vote_task)
            receipt_ids = await interaction.ballot_receipt_message_ids(release.key, vote_seq, caller_data=self.__data)
            await self.__data.rollback()
            confirmable = await tabulate.email_for_confirmation(thread_id, asf_eid, excluded_message_ids=receipt_ids)
            await self.__confirm_archived_vote(
                project_key, version_key, vote_seq, task_id, committee_key, confirmable, choice
            )
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version_key=str(version_key),
            vote_seq=vote_seq,
            asf_eid=asf_eid,
            choice=choice,
        )

    async def __confirm_archived_vote(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        pinned_vote_seq: int,
        pinned_task_id: int,
        pinned_committee_key: str,
        confirmable: tabulate.ConfirmableEmail,
        choice: sql.VoteChoice,
    ) -> None:
        await self.__data.begin_immediate()
        try:
            self.__data.expire_all()
            release, vote_seq, committee_key = await self.__confirmation_release(
                project_key, version_key, pinned_vote_seq, rechecking=True
            )
            task = await interaction.release_current_vote_task(release, self.__data)
            if (task is None) or (task.id != pinned_task_id) or (committee_key != pinned_committee_key):
                raise storage.AccessError("The vote has changed, please refresh and try again.", status=409)
            receipt_ids = await interaction.ballot_receipt_message_ids(release.key, vote_seq, caller_data=self.__data)
            if _normalized_message_id(confirmable.asf_eid) in {_normalized_message_id(mid) for mid in receipt_ids}:
                raise storage.AccessError("An ATR ballot receipt cannot be confirmed as an email vote.", status=409)
            committee = release.committee
            if committee_key == "incubator":
                committee = await self.__data.committee(key="incubator").get()
            is_binding = (confirmable.voter_asf_uid is not None) and user.is_committee_member(
                committee, confirmable.voter_asf_uid
            )
            await self.__upsert_interpretation(
                sql.VoteInterpretation(
                    release_key=release.key,
                    vote_seq=vote_seq,
                    asf_eid=confirmable.asf_eid,
                    vote_round=interaction.vote_round_for_release(release),
                    voter_asf_uid=confirmable.voter_asf_uid,
                    voter_email=confirmable.voter_email,
                    voter_fullname=confirmable.voter_fullname,
                    choice=choice,
                    is_binding_at_confirm=is_binding,
                    email_timestamp=confirmable.email_timestamp,
                    confirmed_by=self.__asf_uid,
                )
            )
            await self.__data.commit()
        except Exception:
            await self.__data.rollback()
            raise

    async def __confirmation_release(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        expected_vote_seq: int | None,
        *,
        rechecking: bool = False,
    ) -> tuple[sql.Release, int, str]:
        release = await self.__data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _project=True,
            _committee=True,
        ).get()
        if release is None:
            if rechecking:
                raise storage.AccessError("Vote is no longer open.", status=409)
            raise storage.AccessError("Release not found", status=404)
        storage.ensure_project_active(release.project)
        self.__write.ensure_release_writable(release)
        if release.expedited:
            raise storage.AccessError("Expedited releases do not accept email vote confirmations.", status=409)
        if (not _vote_is_open(release)) or (release.current_vote_seq is None):
            raise storage.AccessError("Vote is not currently open.", status=409)
        if (expected_vote_seq is not None) and (release.current_vote_seq != expected_vote_seq):
            raise storage.AccessError("The vote form is stale, please refresh and try again.", status=409)
        committee_key = _active_voting_committee_key(release)
        if committee_key != self.__committee_key:
            raise storage.AccessError("The voting committee has changed, please refresh and try again.", status=409)
        return release, release.current_vote_seq, committee_key

    async def __upsert_interpretation(self, snapshot: sql.VoteInterpretation) -> None:
        query = sqlmodel.select(sql.VoteInterpretation).where(
            sql.VoteInterpretation.release_key == snapshot.release_key,
            sql.VoteInterpretation.vote_seq == snapshot.vote_seq,
            sql.VoteInterpretation.asf_eid == snapshot.asf_eid,
        )
        existing = (await self.__data.execute(query)).scalar_one_or_none()
        if existing is None:
            self.__data.add(snapshot)
        else:
            existing.sqlmodel_update(snapshot)


class SecurityTeam(ReleaseManager):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsReleaseManager,
        data: db.Session,
        committee_key: str,
    ):
        if not write.authorisation.is_member_of(constants.SECURITY_COMMITTEE_KEY):
            raise storage.AccessError("Not a member of the ASF Security Team", status=403)
        super().__init__(write, write_as, data, committee_key)


def format_vote_email_body(
    vote: str,
    asf_uid: str,
    fullname: str,
    is_binding: bool,
    comment: str = "",
    potency_label: str | None = None,
) -> str:
    """Format the body of a vote email."""
    # audit_guidance all email is sent through `atr.mail` which handles validation
    if potency_label is not None:
        body = [f"{vote} ({potency_label}) ({asf_uid}) {fullname}"]
    elif is_binding:
        body = [f"{vote} (binding) ({asf_uid}) {fullname}"]
    else:
        body = [f"{vote} ({asf_uid}) {fullname}"]
    if comment:
        body.append(f"{comment}")
        body.append(f"-- \n{fullname} ({asf_uid})")
    return "\n\n".join(body)


def _active_voting_committee_key(release: sql.Release) -> str:
    committee = _release_committee(release)
    if committee.is_podling and (release.podling_thread_id is not None):
        return "incubator"
    return committee.key


def _carried_publish_uid(latest_vote_task: sql.Task, fallback_uid: str) -> str:
    carried_publish_asf_uid = latest_vote_task.task_args.get("automatic_publish_asf_uid")
    if isinstance(carried_publish_asf_uid, str):
        return carried_publish_asf_uid
    initiator_id = latest_vote_task.task_args.get("initiator_id")
    if isinstance(initiator_id, str):
        return initiator_id
    return fallback_uid


def _check_passing_tally(
    release: sql.Release,
    vote_result: str,
    summary: interaction.CountedVoteSummary,
    binding_label: str,
    resolution_bypass: bool,
    unattended: bool,
) -> None:
    if vote_result != "passed":
        return
    if unattended and (not release.expedited) and (not tabulate.all_ballots_yes(summary)):
        raise storage.AccessError("Somebody voted 0 or -1, so the release manager must resolve this vote", status=409)
    if (not resolution_bypass) and (
        not tabulate.binding_vote_passes(summary.binding_votes_yes, summary.binding_votes_no)
    ):
        binding_label_inline = user.binding_label_inline(binding_label)
        raise storage.AccessError(
            f"The ATR ballot record does not have enough {binding_label_inline} +1 votes to pass.", status=409
        )


def _check_resolution_seq(release: sql.Release, expected_vote_seq: int | None) -> None:
    if (
        (expected_vote_seq is not None)
        and (release.current_vote_seq is not None)
        and (release.current_vote_seq != expected_vote_seq)
    ):
        raise storage.AccessError("The resolve form is stale, please refresh and try again", status=409)


def _extend_unique(addresses: list[str], additions: list[str] | None, seen: set[str]) -> list[str]:
    result = list(addresses)
    for address in additions or []:
        if address.lower() not in seen:
            result.append(address)
            seen.add(address.lower())
    return result


def _normalized_message_id(message_id: str) -> str:
    return message_id.strip().removeprefix("<").removesuffix(">")


def _open_vote_seq(release: sql.Release, expected_vote_seq: int | None) -> int:
    if not _vote_is_open(release):
        raise storage.AccessError("Vote is not currently open.", status=409)
    if (release.current_vote_seq is None) or (release.current_vote_seq < 1):
        raise storage.AccessError("Vote serial is missing, please refresh and try again.", status=409)
    if (expected_vote_seq is not None) and (release.current_vote_seq != expected_vote_seq):
        raise storage.AccessError("The vote form is stale, please refresh and try again.", status=409)
    return release.current_vote_seq


def _release_committee(release: sql.Release) -> sql.Committee:
    if release.committee is None:
        raise storage.AccessError("The committee for this release was not found.", status=500)
    return release.committee


def _render_resolution_body(
    release: sql.Release,
    committee: sql.Committee,
    vote_result: str,
    resolution_body: str | None,
    finish_vote_template: str | None,
    tally_block: str,
    asf_uid: str,
    asf_fullname: str,
) -> str:
    if resolution_body is not None:
        return resolution_body.replace("{{ATR_TALLY}}", tally_block)
    if finish_vote_template is None:
        raise storage.AccessError("The vote resolution template was not resolved", status=500)
    return construct.finish_vote_body(
        finish_vote_template,
        {
            "ATR_TALLY": tally_block,
            "COMMITTEE": committee.display_name,
            "MAJOR_VERSION": construct.major_version(release.version),
            "OUTCOME": "was cancelled" if (vote_result == "cancelled") else vote_result,
            "PROJECT_NAME": release.project.short_display_name,
            "VERSION": release.version,
            "YOUR_ASF_ID": asf_uid,
            "YOUR_FULL_NAME": asf_fullname,
        },
    )


def _require_announced(task: sql.Task | None) -> None:
    if (task is None) or (interaction.task_mid_get(task) is None):
        raise storage.AccessError("The vote has not been announced, so it cannot be forced", status=409)


def _resolution_state(
    release: sql.Release, task: sql.Task | None
) -> tuple[int | None, str | None, bool, str | None, int | None]:
    return (
        release.current_vote_seq,
        release.podling_thread_id,
        release.expedited,
        release.project.committee_key,
        task.id if (task is not None) else None,
    )


async def _resolution_templates(
    release: sql.Release, vote_result: str, resolution_body: str | None
) -> tuple[str | None, str | None]:
    second_round_body_template = None
    if (vote_result == "passed") and (interaction.vote_round_for_release(release) == 1):
        second_round_body_template = (await construct.resolve_template(release.project, "start_vote")).body
    finish_vote_template = None
    if resolution_body is None:
        finish_vote_template = (await construct.resolve_template(release.project, "finish_vote")).body
    return second_round_body_template, finish_vote_template


def _vote_is_open(release: sql.Release) -> bool:
    return (release.vote_started is not None) and (release.vote_resolved is None)


def _vote_potency_label(release: sql.Release, is_binding: bool) -> str | None:
    committee = release.committee
    if (committee is not None) and committee.is_podling and (release.podling_thread_id is None):
        return "PPMC-binding" if is_binding else "non-binding"
    if is_binding:
        return "binding"
    return None
