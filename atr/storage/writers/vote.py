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
from typing import Literal

import sqlmodel

import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
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

    async def cast_trusted(  # noqa: C901
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        choice: sql.VoteChoice,
        comment: str,
        fullname: str,
        expected_vote_seq: int | None = None,
        expected_vote_mode: sql.VoteMode | None = None,
    ) -> tuple[list[str], str]:
        await self.__data.begin_immediate()
        try:
            release = await self.__data.release(
                project_key=str(project_key),
                version=str(version_key),
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                _project=True,
                _committee=True,
            ).get()
            if release is None:
                await self.__data.rollback()
                return [], "Vote is no longer open."
            if (expected_vote_mode is not None) and (expected_vote_mode != sql.VoteMode.TRUSTED):
                await self.__data.rollback()
                return [], "The vote form is stale, please refresh and try again."
            if release.effective_vote_mode != sql.VoteMode.TRUSTED:
                await self.__data.rollback()
                return [], "This release is not accepting trusted votes."
            if release.current_vote_seq is None:
                await self.__data.rollback()
                return [], "Vote serial is missing, please refresh and try again."
            if (expected_vote_seq is not None) and (release.current_vote_seq != expected_vote_seq):
                await self.__data.rollback()
                return [], "The vote form is stale, please refresh and try again."
            if release.committee is None:
                await self.__data.rollback()
                return [], "The committee for this release was not found."
            if release.latest_revision_number is None:
                await self.__data.rollback()
                return [], "No revision found for this release."

            vote_round = None
            if release.committee.is_podling:
                vote_round = 2 if (release.podling_thread_id is not None) else 1
            is_binding, _binding_committee = await user.is_binding_for_release(
                release.committee,
                self.__asf_uid,
                vote_round,
                caller_data=self.__data,
            )
            binding_word, non_binding_word = user.binding_terminology(vote_round)
            potency_label = binding_word.lower() if is_binding else None
            if (vote_round == 1) and (not is_binding):
                potency_label = non_binding_word.lower()

            start_task = await interaction.release_current_vote_task(release, self.__data)
            if start_task is None:
                await self.__data.rollback()
                return [], "No vote task found."
            vote_result = start_task.result
            if vote_result is None:
                await self.__data.rollback()
                return [], "Vote task has not completed yet."
            if not isinstance(vote_result, results.VoteInitiate):
                await self.__data.rollback()
                return [], "Vote task result is not a VoteInitiate result."
            start_mid = interaction.task_mid_get(start_task)
            if start_mid is None:
                await self.__data.rollback()
                return [], "No vote thread found."
            email_to: str = start_task.task_args["email_to"]
            email_cc: list[str] = start_task.task_args.get("email_cc", [])
            email_bcc: list[str] = start_task.task_args.get("email_bcc", [])
            email_sender = f"{self.__asf_uid}@apache.org"
            subject = f"Re: {vote_result.subject}"
            body_text = format_vote_email_body(
                vote=choice.value,
                asf_uid=self.__asf_uid,
                fullname=fullname,
                is_binding=is_binding,
                comment=comment,
                potency_label=potency_label,
            )

            previous_ballot_query = (
                sqlmodel.select(sql.BallotPaper)
                .where(sql.BallotPaper.release_key == release.key)
                .where(sql.BallotPaper.vote_seq == release.current_vote_seq)
                .where(sql.BallotPaper.voter_asf_uid == self.__asf_uid)
                .order_by(sql.validate_instrumented_attribute(sql.BallotPaper.id).desc())
                .limit(1)
            )
            previous_ballot = (await self.__data.execute(previous_ballot_query)).scalar_one_or_none()

            receipt_message_id = mail.message_id_create()
            task = sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.MESSAGE_SEND,
                task_args=args.Send(
                    email_sender=email_sender,
                    email_to=email_to,
                    subject=subject,
                    body=body_text,
                    in_reply_to=start_mid,
                    email_cc=email_cc,
                    email_bcc=email_bcc,
                    message_id=receipt_message_id,
                    footer_category=mail.MailFooterCategory.USER,
                ).as_task_args(),
                asf_uid=self.__asf_uid,
                project_key=release.project.key,
                version_key=release.version,
            )
            ballot = sql.BallotPaper(
                release_key=release.key,
                vote_seq=release.current_vote_seq,
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
            vote_seq=release.current_vote_seq,
            vote_round=vote_round,
            voter_asf_uid=self.__asf_uid,
            choice=choice.value,
            is_binding_at_cast=is_binding,
            receipt_message_id=receipt_message_id,
            replaced_ballot_id=previous_ballot.id if (previous_ballot is not None) else None,
        )
        return [email_to], ""


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

    async def send_user_vote(
        self,
        release: sql.Release,
        vote: str,
        comment: str,
        fullname: str,
        is_binding: bool = False,
    ) -> tuple[list[str], str]:
        # Get the email thread
        latest_vote_task = await interaction.release_current_vote_task(release)
        if latest_vote_task is None:
            return [], "No vote task found."
        vote_thread_mid = interaction.task_mid_get(latest_vote_task)
        if vote_thread_mid is None:
            return [], "No vote thread found."

        # Construct the reply email
        vote_result = latest_vote_task.result
        if vote_result is None:
            return [], "Vote task has not completed yet."
        if not isinstance(vote_result, results.VoteInitiate):
            return [], "Vote task result is not a VoteInitiate result."
        original_subject = vote_result.subject

        # Arguments for the task to cast a vote
        email_to: str = latest_vote_task.task_args["email_to"]
        email_cc: list[str] = latest_vote_task.task_args.get("email_cc", [])
        email_bcc: list[str] = latest_vote_task.task_args.get("email_bcc", [])
        email_sender = f"{self.__asf_uid}@apache.org"
        subject = f"Re: {original_subject}"
        body_text = format_vote_email_body(
            vote=vote,
            asf_uid=self.__asf_uid,
            fullname=fullname,
            is_binding=is_binding,
            comment=comment,
            potency_label=_vote_potency_label(release, is_binding),
        )
        in_reply_to = vote_thread_mid

        task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.MESSAGE_SEND,
            task_args=args.Send(
                email_sender=email_sender,
                email_to=email_to,
                subject=subject,
                body=body_text,
                in_reply_to=in_reply_to,
                email_cc=email_cc,
                email_bcc=email_bcc,
                footer_category=mail.MailFooterCategory.USER,
            ).as_task_args(),
            asf_uid=self.__asf_uid,
            project_key=release.project.key,
            version_key=release.version,
        )
        self.__data.add(task)
        await self.__data.flush()
        await self.__data.commit()

        return [email_to], ""

    async def start(  # noqa: C901
        self,
        email_to: str,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        selected_revision_number: safe.RevisionNumber,
        vote_duration_choice: int,
        subject: str,
        body_data: str,
        asf_fullname: str,
        release: sql.Release | None = None,
        promote: bool = True,
        permitted_recipients: list[str] | None = None,
        email_cc: list[str] | None = None,
        email_bcc: list[str] | None = None,
        second_round_email_to: str | None = None,
        expected_vote_mode: sql.VoteMode | None = None,
        notify_when_finished: bool = False,
        automatic_resolve_when_finished: bool = False,
    ) -> sql.Task:
        if promote:
            await self.__data.begin_immediate()
        try:
            release_key = sql.release_key(project_key, version_key)
            if promote is True:
                # This verifies the state and sets the phase to RELEASE_CANDIDATE
                allowed_vote_modes = (
                    frozenset({expected_vote_mode})
                    if (expected_vote_mode is not None)
                    else frozenset({sql.VoteMode.EMAIL, sql.VoteMode.TRUSTED})
                )
                release, vote_seq, vote_mode = await self.__write_as.release._start_vote_no_commit(
                    release_key,
                    selected_revision_number,
                    allowed_vote_modes=allowed_vote_modes,
                    promote=True,
                )
            else:
                if release is None:
                    release = await self.__data.release(
                        project_key=str(project_key),
                        version=str(version_key),
                        _project=True,
                        _committee=True,
                    ).demand(storage.AccessError("Release not found", status=404))
                release, vote_seq, vote_mode = await self.__write_as.release._start_vote_no_commit(
                    release.safe_key,
                    selected_revision_number,
                    allowed_vote_modes=frozenset({sql.VoteMode.EMAIL, sql.VoteMode.TRUSTED}),
                    promote=False,
                    expected_podling_thread_id=release.podling_thread_id,
                )
            committee = await self._committee_for_release(release)
            if committee is None:
                raise storage.AccessError("Release has no committee", status=500)
            if notify_when_finished and (vote_mode != sql.VoteMode.TRUSTED):
                raise storage.AccessError("Vote end reminders are only available in Trusted Vote mode", status=403)
            if automatic_resolve_when_finished and (vote_mode != sql.VoteMode.TRUSTED):
                raise storage.AccessError(
                    "Automatic vote resolution is only available in Trusted Vote mode", status=403
                )
            if automatic_resolve_when_finished and committee.is_podling:
                raise storage.AccessError(
                    "Automatic vote resolution is not yet available for podling votes", status=403
                )
            if automatic_resolve_when_finished and (self.__asf_uid not in committee.committee_members):
                # TODO: Maybe we should modularise all of this?
                # Then we could use the relevant permissions class
                raise storage.AccessError("Automatic vote resolution requires a committee member initiator", status=403)
            if permitted_recipients is None:
                permitted_recipients = util.permitted_podling_first_round_recipients(
                    self.__asf_uid,
                    committee.key,
                    is_podling=committee.is_podling,
                )
            all_addrs = [email_to] + (email_cc or []) + (email_bcc or [])
            for addr in all_addrs:
                if addr not in permitted_recipients:
                    # This will be checked again by tasks/vote.py for extra safety
                    log.info(f"Invalid mailing list choice: {addr} not in {permitted_recipients}")
                    raise storage.AccessError("Invalid mailing list choice", status=403)

            if second_round_email_to is not None:
                second_round_permitted = util.permitted_podling_second_round_recipients(self.__asf_uid)
                if second_round_email_to not in second_round_permitted:
                    log.info(
                        "Invalid second round mailing list choice: "
                        f"{second_round_email_to} not in {second_round_permitted}"
                    )
                    raise storage.AccessError("Second round mailing list choice is not permitted", status=403)

            # TODO: We also need to store the duration of the vote
            # We can't allow resolution of the vote until the duration has elapsed
            # But we allow the user to specify in the form
            # And yet we also have ReleasePolicy.min_hours
            # Presumably this sets the default, and the form takes precedence?
            # ReleasePolicy.min_hours can also be 0, though

            # Create a task for vote initiation
            task = sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.VOTE_INITIATE,
                task_args=args.Initiate(
                    release_key=release.key,
                    email_to=email_to,
                    vote_duration=vote_duration_choice,
                    initiator_id=self.__asf_uid,
                    initiator_fullname=asf_fullname,
                    subject=subject,
                    body=body_data,
                    vote_seq=vote_seq,
                    email_cc=email_cc or [],
                    email_bcc=email_bcc or [],
                    second_round_email_to=second_round_email_to,
                    notify_when_finished=notify_when_finished,
                    automatic_resolve_when_finished=automatic_resolve_when_finished,
                ).model_dump(),
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                version_key=str(version_key),
            )
            self.__data.add(task)
            if promote:
                await self.__data.commit()
        except Exception:
            if promote:
                await self.__data.rollback()
            raise

        if promote:
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                release_key=release.key,
                selected_revision_number=str(selected_revision_number),
                vote_seq=vote_seq,
                vote_mode=vote_mode.value,
            )

        # TODO: We should log all outgoing email and the session so that users can confirm
        # And can be warned if there was a failure
        # (The message should be shown on the vote resolution page)
        return task

    async def _committee_for_release(self, release: sql.Release) -> sql.Committee | None:
        project = await self.__data.project(key=release.project_key, _committee=True).get()
        if project is None:
            return None
        return project.committee


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

    async def resolve(  # noqa: C901
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        vote_result: Literal["passed", "failed", "cancelled"],
        asf_fullname: str,
        resolution_body: str,
        expected_vote_seq: int | None = None,
        expected_vote_mode: sql.VoteMode | None = None,
    ) -> tuple[sql.Release, int | None, str, str | None]:
        release = await self.__data.release(
            key=sql.release_key(str(project_key), str(version_key)),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _project=True,
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(storage.AccessError("Release not found", status=404))
        if (expected_vote_mode is not None) and (release.effective_vote_mode != expected_vote_mode):
            raise storage.AccessError("The resolve form is stale, please refresh and try again", status=409)
        if release.effective_vote_mode == sql.VoteMode.MANUAL:
            raise storage.AccessError("Release is configured for manual voting", status=409)
        podling_round_one_thread_id = None
        if release.effective_vote_mode == sql.VoteMode.TRUSTED:
            if (
                (vote_result == "passed")
                and (release.committee is not None)
                and release.committee.is_podling
                and (release.podling_thread_id is None)
            ):
                latest_vote_task = await interaction.release_current_vote_task(release, self.__data)
                if latest_vote_task is not None:
                    task_mid = interaction.task_mid_get(latest_vote_task)
                    task_recipient = interaction.task_recipient_get(latest_vote_task)
                    archive_url = await self.__write_as.cache.get_message_archive_url(task_mid, task_recipient)
                    if archive_url is not None:
                        podling_round_one_thread_id = archive_url.split("/")[-1]
            await self.__data.rollback()
            return await self._resolve_trusted(
                project_key,
                version_key,
                vote_result,
                asf_fullname,
                resolution_body,
                expected_vote_seq,
                expected_vote_mode,
                podling_round_one_thread_id,
            )
        if (
            (expected_vote_seq is not None)
            and (release.current_vote_seq is not None)
            and (release.current_vote_seq != expected_vote_seq)
        ):
            raise storage.AccessError("The resolve form is stale, please refresh and try again", status=409)

        is_podling = False
        if release.project.committee is not None:
            is_podling = release.project.committee.is_podling
        podling_thread_id = release.podling_thread_id

        latest_vote_task = await interaction.release_current_vote_task(release)
        if latest_vote_task is None:
            raise storage.AccessError("No vote task found, unable to send resolution message.", status=404)

        if (
            (vote_result != "cancelled")
            and (not interaction.vote_pass_fail_allowed(latest_vote_task))
            and (not interaction.vote_duration_bypass())
        ):
            raise storage.AccessError(
                "The vote cannot be resolved before the voting period has ended unless it is cancelled.",
                status=409,
            )

        voting_round = None
        if is_podling is True:
            voting_round = 1 if (podling_thread_id is None) else 2
        if release.committee is None:
            raise storage.AccessError("Project has no committee - Invalid state", status=500)

        return await self.resolve_release(
            project_key,
            release,
            voting_round,
            vote_result,
            latest_vote_task,
            asf_fullname,
            resolution_body,
        )

    async def resolve_manually(
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        vote_result: Literal["passed", "failed", "cancelled"],
    ) -> str:
        release = await self.__data.release(
            key=sql.release_key(str(project_key), str(version_key)),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _project=True,
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(storage.AccessError("Release not found", status=404))

        if release.effective_vote_mode != sql.VoteMode.MANUAL:
            raise storage.AccessError("Release is not configured for manual voting", status=409)

        if release.vote_started is None:
            raise storage.AccessError("Vote has not been started", status=409)

        if (release.project.committee is not None) and release.project.committee.is_podling:
            raise storage.AccessError("Podling releases require the standard two round vote process", status=409)

        match vote_result:
            case "passed":
                await self._resolve_transition(
                    release,
                    expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    expected_podling_thread_id=None,
                    new_phase=sql.ReleasePhase.RELEASE_PREVIEW,
                    new_vote_mode=release.effective_vote_mode,
                    new_vote_resolved=datetime.datetime.now(datetime.UTC),
                    new_podling_thread_id=None,
                )
                await self.__data.commit()
                await self.__data.refresh(release)
                success_message = "Vote marked as passed"

                description = "Create a preview revision from the last candidate draft"
                await self.__write_as.revision.create_revision_with_quarantine(
                    project_key,
                    release.safe_version_key,
                    self.__asf_uid,
                    allowed_phases=frozenset({sql.ReleasePhase.RELEASE_PREVIEW}),
                    description=description,
                )
            case "failed" | "cancelled":
                # The vote_resolved property refers to when the vote succeeded only
                await self._resolve_transition(
                    release,
                    expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    expected_podling_thread_id=None,
                    new_phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                    new_vote_mode=None,
                    new_vote_resolved=None,
                    new_podling_thread_id=None,
                )
                await self.__data.commit()
                await self.__data.refresh(release)
                success_message = f"Vote marked as {vote_result}"

        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=str(project_key),
            version_key=str(version_key),
            vote_result=vote_result,
        )
        return success_message

    async def resolve_release(  # noqa: C901
        self,
        project_key: safe.ProjectKey,
        release: sql.Release,
        voting_round: int | None,
        vote_result: Literal["passed", "failed", "cancelled"],
        latest_vote_task: sql.Task,
        asf_fullname: str,
        resolution_body: str,
    ) -> tuple[sql.Release, int | None, str, str | None]:
        if (voting_round == 1) and (vote_result == "passed"):
            await self.__data.commit()
            await self.__data.begin_immediate()
        # Attach the existing release to the session
        release = await self.__data.merge(release)
        # Update the release phase based on vote result
        extra_destination = None
        second_round_vote_seq = None
        second_round_vote_mode = None
        if (voting_round == 1) and (vote_result == "passed"):
            try:
                # This is the first podling vote, by the PPMC and not the Incubator PMC
                # In this branch, we do not move to RELEASE_PREVIEW but keep everything the same
                # We only set the podling_thread_id to the thread_id of the vote thread
                # Then we automatically start the Incubator PMC vote
                # TODO: Note on the resolve vote page that resolving the Project PPMC vote starts the Incubator PMC vote
                task_mid = interaction.task_mid_get(latest_vote_task)
                task_recipient = interaction.task_recipient_get(latest_vote_task)
                archive_url = await self.__write_as.cache.get_message_archive_url(task_mid, task_recipient)
                if archive_url is None:
                    raise storage.AccessError("No archive URL found for podling vote", status=404)
                thread_id = archive_url.split("/")[-1]
                await self._resolve_transition(
                    release,
                    expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    expected_podling_thread_id=None,
                    new_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    new_vote_mode=release.effective_vote_mode,
                    new_vote_resolved=None,
                    new_podling_thread_id=thread_id,
                )
                await self.__data.refresh(release)
                incubator_vote_address = (
                    latest_vote_task.task_args.get("second_round_email_to") or util.INCUBATOR_GENERAL_ADDRESS
                )
                if not release.project.committee:
                    raise storage.AccessError("Project has no committee - Invalid state", status=500)
                revision_number = release.latest_revision_number
                if revision_number is None:
                    raise storage.AccessError("Release has no revision number - Invalid state", status=500)
                vote_duration = latest_vote_task.task_args["vote_duration"]
                subject_template = await construct.start_vote_subject_default(release.safe_project_key)
                body_template = await construct.start_vote_default(release.safe_project_key)
                options = construct.StartVoteOptions(
                    asfuid=self.__asf_uid,
                    fullname=asf_fullname,
                    project_key=release.safe_project_key,
                    version_key=release.safe_version_key,
                    revision_number=release.safe_latest_revision_number,
                    vote_duration=vote_duration,
                )
                subject_data, body_data = await construct.start_vote_subject_and_body(
                    subject_template, body_template, options
                )
                second_round_task = await self.start(
                    email_to=incubator_vote_address,
                    permitted_recipients=[incubator_vote_address],
                    project_key=release.safe_project_key,
                    version_key=release.safe_version_key,
                    selected_revision_number=release.safe_latest_revision_number,
                    asf_fullname=asf_fullname,
                    vote_duration_choice=vote_duration,
                    subject=subject_data,
                    body_data=body_data,
                    release=release,
                    promote=False,
                )
                second_round_vote_seq = second_round_task.task_args["vote_seq"]
                if not isinstance(second_round_vote_seq, int):
                    raise storage.AccessError("Second round vote sequence is invalid", status=500)
                second_round_vote_mode = release.effective_vote_mode
                await self.__data.commit()
                await self.__data.refresh(release)
            except Exception:
                await self.__data.rollback()
                raise
            success_message = (
                f"First round vote marked as passed, and second round vote automatically started"
                f" (sent to {incubator_vote_address})"
            )
        elif vote_result == "passed":
            await self._resolve_transition(
                release,
                expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                expected_podling_thread_id=release.podling_thread_id,
                new_phase=sql.ReleasePhase.RELEASE_PREVIEW,
                new_vote_mode=release.effective_vote_mode,
                new_vote_resolved=datetime.datetime.now(datetime.UTC),
                new_podling_thread_id=release.podling_thread_id,
            )
            await self.__data.commit()
            await self.__data.refresh(release)
            success_message = "Vote marked as passed"

            description = "Create a preview revision from the last candidate draft"
            await self.__write_as.revision.create_revision_with_quarantine(
                project_key,
                release.safe_version_key,
                self.__asf_uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_PREVIEW}),
                description=description,
            )
            if (voting_round == 2) and (release.podling_thread_id is not None):
                round_one_email_address, round_one_message_id = await util.email_mid_from_thread_id(
                    release.podling_thread_id
                )
                extra_destination = (round_one_email_address, round_one_message_id)
        else:
            # The vote_resolved property refers to when the vote succeeded only
            await self._resolve_transition(
                release,
                expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                expected_podling_thread_id=release.podling_thread_id,
                new_phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                new_vote_mode=None,
                new_vote_resolved=None,
                new_podling_thread_id=None,
            )
            await self.__data.commit()
            await self.__data.refresh(release)
            success_message = f"Vote marked as {vote_result}"

        error_message = await self.send_resolution(
            release,
            vote_result,
            resolution_body,
            asf_fullname,
            latest_vote_task,
            extra_destination=extra_destination,
        )
        # TODO: Could move this up before send_resolution
        if (second_round_vote_seq is not None) and (second_round_vote_mode is not None):
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                release_key=release.key,
                project_key=str(project_key),
                version_key=release.version,
                selected_revision_number=str(release.safe_latest_revision_number),
                vote_seq=second_round_vote_seq,
                vote_mode=second_round_vote_mode.value,
                vote_result=vote_result,
                voting_round=voting_round,
                second_round_vote_seq=second_round_vote_seq,
            )
        else:
            self.__write_as.append_to_audit_log(
                asf_uid=self.__asf_uid,
                project_key=str(project_key),
                version_key=release.version,
                vote_result=vote_result,
                voting_round=voting_round,
            )
        return release, voting_round, success_message, error_message

    async def send_resolution(
        self,
        release: sql.Release,
        resolution: str,
        body: str,
        asf_fullname: str,
        latest_vote_task: sql.Task,
        extra_destination: tuple[str, str] | None = None,
    ) -> str | None:
        # Get the email thread
        vote_thread_mid = interaction.task_mid_get(latest_vote_task)
        if vote_thread_mid is None:
            return "No vote thread found, unable to send resolution message."

        # Construct the reply email
        # original_subject = latest_vote_task.task_args["subject"]

        # Arguments for the task to cast a vote
        email_to: str = latest_vote_task.task_args["email_to"]
        email_cc: list[str] = latest_vote_task.task_args.get("email_cc", [])
        email_bcc: list[str] = latest_vote_task.task_args.get("email_bcc", [])
        email_sender = f"{self.__asf_uid}@apache.org"
        subject = f"[VOTE] [RESULT] Release {release.project.display_name} {release.version} {resolution.upper()}"
        # TODO: This duplicates atr/tabulate.py code
        # There are arguments for using this code instead:
        # - It enforces a consistent style
        # - It can't be edited by the user
        # - It could be made conditional based on user input
        # But users might not know whether to use a signature or not
        # And they may not use a standard format that can be detected
        # Therefore we don't add a signature here
        # signature = f"-- \n{asf_fullname} ({asf_uid})"
        # if asf_fullname == asf_uid:
        #     signature = f"-- \n{asf_fullname}"
        # body = f"{body}\n\n{signature}"
        in_reply_to = vote_thread_mid

        task = sql.Task(
            status=sql.TaskStatus.QUEUED,
            task_type=sql.TaskType.MESSAGE_SEND,
            task_args=args.Send(
                email_sender=email_sender,
                email_to=email_to,
                subject=subject,
                body=body,
                in_reply_to=in_reply_to,
                email_cc=email_cc,
                email_bcc=email_bcc,
                footer_category=mail.MailFooterCategory.USER,
            ).as_task_args(),
            asf_uid=self.__asf_uid,
            project_key=release.project.key,
            version_key=release.version,
        )
        tasks = [task]
        if extra_destination is not None:
            task = sql.Task(
                status=sql.TaskStatus.QUEUED,
                task_type=sql.TaskType.MESSAGE_SEND,
                task_args=args.Send(
                    email_sender=email_sender,
                    email_to=extra_destination[0],
                    subject=subject,
                    body=body,
                    in_reply_to=extra_destination[1],
                    footer_category=mail.MailFooterCategory.USER,
                ).as_task_args(),
                asf_uid=self.__asf_uid,
                project_key=release.project.key,
                version_key=release.version,
            )
            tasks.append(task)
        self.__data.add_all(tasks)
        await self.__data.flush()
        await self.__data.commit()
        return None

    async def _resolve_trusted(  # noqa: C901
        self,
        project_key: safe.ProjectKey,
        version_key: safe.VersionKey,
        vote_result: Literal["passed", "failed", "cancelled"],
        asf_fullname: str,
        resolution_body: str,
        expected_vote_seq: int | None,
        expected_vote_mode: sql.VoteMode | None,
        podling_round_one_thread_id: str | None,
    ) -> tuple[sql.Release, int | None, str, str | None]:
        latest_vote_task: sql.Task | None = None
        second_round_vote_mode: sql.VoteMode | None = None
        second_round_vote_seq: int | None = None
        await self.__data.begin_immediate()
        try:
            release = await self.__data.release(
                key=sql.release_key(str(project_key), str(version_key)),
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                _project=True,
                _committee=True,
                _release_policy=True,
                _project_release_policy=True,
            ).demand(storage.AccessError("Release not found", status=404))
            if (expected_vote_mode is not None) and (expected_vote_mode != sql.VoteMode.TRUSTED):
                raise storage.AccessError("The resolve form is stale, please refresh and try again", status=409)
            if release.effective_vote_mode != sql.VoteMode.TRUSTED:
                raise storage.AccessError("Release is not configured for trusted voting", status=409)
            if release.current_vote_seq is None:
                raise storage.AccessError("Vote serial is missing, please refresh and try again", status=409)
            if (expected_vote_seq is not None) and (release.current_vote_seq != expected_vote_seq):
                raise storage.AccessError("The resolve form is stale, please refresh and try again", status=409)
            if release.committee is None:
                raise storage.AccessError("Project has no committee - Invalid state", status=500)

            vote_seq = release.current_vote_seq
            voting_round = interaction.trusted_vote_round(release)

            latest_vote_task = await interaction.release_current_vote_task(release, self.__data)
            vote_end = interaction.vote_end_get(latest_vote_task)
            resolution_bypass = interaction.vote_duration_bypass()
            if (
                (vote_result != "cancelled")
                and (vote_end is not None)
                and (not interaction.vote_pass_fail_allowed(latest_vote_task))
                and (not resolution_bypass)
            ):
                raise storage.AccessError(
                    "The vote cannot be resolved before the voting period has ended unless it is cancelled.",
                    status=409,
                )

            ballots = await interaction.effective_trusted_ballots(release, vote_seq, self.__data)
            summary = await interaction.trusted_ballot_summary(release, ballots, self.__data)
            if (
                (vote_result == "passed")
                and (not tabulate.binding_vote_passes(summary.binding_votes_yes, summary.binding_votes_no))
                and (not resolution_bypass)
            ):
                binding_label, _non_binding_label = user.binding_terminology(voting_round)
                raise storage.AccessError(
                    f"The trusted ballot record does not have enough {binding_label.lower()} +1 votes to pass.",
                    status=409,
                )

            if (voting_round == 1) and (vote_result == "passed"):
                if latest_vote_task is None:
                    raise storage.AccessError("No vote task found, unable to start the Incubator vote.", status=404)
                if podling_round_one_thread_id is None:
                    raise storage.AccessError("No archive URL found for podling vote", status=404)
                await self._resolve_transition(
                    release,
                    expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    expected_podling_thread_id=None,
                    new_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    new_vote_mode=release.effective_vote_mode,
                    new_vote_resolved=None,
                    new_podling_thread_id=podling_round_one_thread_id,
                )
                await self.__data.refresh(release)
                incubator_vote_address = (
                    latest_vote_task.task_args.get("second_round_email_to") or util.INCUBATOR_GENERAL_ADDRESS
                )
                revision_number = release.latest_revision_number
                if revision_number is None:
                    raise storage.AccessError("Release has no revision number - Invalid state", status=500)
                vote_duration = latest_vote_task.task_args["vote_duration"]
                subject_template = await construct.start_vote_subject_default(release.safe_project_key)
                body_template = await construct.start_vote_default(release.safe_project_key)
                options = construct.StartVoteOptions(
                    asfuid=self.__asf_uid,
                    fullname=asf_fullname,
                    project_key=release.safe_project_key,
                    version_key=release.safe_version_key,
                    revision_number=release.safe_latest_revision_number,
                    vote_duration=vote_duration,
                )
                subject_data, body_data = await construct.start_vote_subject_and_body(
                    subject_template, body_template, options
                )
                second_round_task = await self.start(
                    email_to=incubator_vote_address,
                    permitted_recipients=[incubator_vote_address],
                    project_key=release.safe_project_key,
                    version_key=release.safe_version_key,
                    selected_revision_number=release.safe_latest_revision_number,
                    asf_fullname=asf_fullname,
                    vote_duration_choice=vote_duration,
                    subject=subject_data,
                    body_data=body_data,
                    release=release,
                    promote=False,
                )
                second_round_vote_seq = second_round_task.task_args["vote_seq"]
                if not isinstance(second_round_vote_seq, int):
                    raise storage.AccessError("Second round vote sequence is invalid", status=500)
                second_round_vote_mode = release.effective_vote_mode
                await self.__data.commit()
                await self.__data.refresh(release)
                success_message = (
                    f"First round vote marked as passed, and second round vote automatically started"
                    f" (sent to {incubator_vote_address})"
                )
            elif vote_result == "passed":
                await self._resolve_transition(
                    release,
                    expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    expected_podling_thread_id=release.podling_thread_id,
                    new_phase=sql.ReleasePhase.RELEASE_PREVIEW,
                    new_vote_mode=release.effective_vote_mode,
                    new_vote_resolved=datetime.datetime.now(datetime.UTC),
                    new_podling_thread_id=release.podling_thread_id,
                )
                await self.__data.commit()
                await self.__data.refresh(release)
                success_message = "Vote marked as passed"
            else:
                await self._resolve_transition(
                    release,
                    expected_phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                    expected_podling_thread_id=release.podling_thread_id,
                    new_phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                    new_vote_mode=None,
                    new_vote_resolved=None,
                    new_podling_thread_id=None,
                )
                await self.__data.commit()
                await self.__data.refresh(release)
                success_message = f"Vote marked as {vote_result}"
        except Exception:
            await self.__data.rollback()
            raise

        extra_destination = None
        if (vote_result == "passed") and (voting_round != 1):
            description = "Create a preview revision from the last candidate draft"
            await self.__write_as.revision.create_revision_with_quarantine(
                project_key,
                release.safe_version_key,
                self.__asf_uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_PREVIEW}),
                description=description,
            )
            if (voting_round == 2) and (release.podling_thread_id is not None):
                round_one_email_address, round_one_message_id = await util.email_mid_from_thread_id(
                    release.podling_thread_id
                )
                extra_destination = (round_one_email_address, round_one_message_id)

        if latest_vote_task is None:
            error_message = "No vote task found, unable to send resolution message."
        else:
            error_message = await self.send_resolution(
                release,
                vote_result,
                resolution_body,
                asf_fullname,
                latest_vote_task,
                extra_destination=extra_destination,
            )

        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            release_key=release.key,
            project_key=str(project_key),
            version_key=release.version,
            selected_revision_number=release.latest_revision_number,
            vote_result=vote_result,
            voting_round=voting_round,
            vote_seq=vote_seq,
            vote_mode=release.effective_vote_mode.value,
            trusted_binding_yes=summary.binding_votes_yes,
            trusted_binding_no=summary.binding_votes_no,
            trusted_binding_abstain=summary.binding_votes_abstain,
            trusted_non_binding_yes=summary.non_binding_votes_yes,
            trusted_non_binding_no=summary.non_binding_votes_no,
            trusted_non_binding_abstain=summary.non_binding_votes_abstain,
            second_round_vote_seq=second_round_vote_seq,
            second_round_vote_mode=second_round_vote_mode.value if (second_round_vote_mode is not None) else None,
        )
        return release, voting_round, success_message, error_message

    async def _resolve_transition(
        self,
        release: sql.Release,
        *,
        expected_phase: sql.ReleasePhase,
        expected_podling_thread_id: str | None,
        new_phase: sql.ReleasePhase,
        new_vote_mode: sql.VoteMode | None,
        new_vote_resolved: datetime.datetime | None,
        new_podling_thread_id: str | None,
    ) -> None:
        via = sql.validate_instrumented_attribute
        stmt = sqlmodel.update(sql.Release).where(
            via(sql.Release.key) == release.key,
            via(sql.Release.phase) == expected_phase,
        )
        if expected_podling_thread_id is None:
            stmt = stmt.where(via(sql.Release.podling_thread_id).is_(None))
        else:
            stmt = stmt.where(via(sql.Release.podling_thread_id) == expected_podling_thread_id)
        result = await self.__data.execute(
            stmt.values(
                phase=new_phase,
                vote_mode=new_vote_mode,
                vote_resolved=new_vote_resolved,
                podling_thread_id=new_podling_thread_id,
            )
        )
        if getattr(result, "rowcount", 0) != 1:
            await self.__data.rollback()
            raise storage.AccessError("The release state has changed, please refresh and try again", status=409)
        await self._cancel_pending_vote_followups(release)

    async def _cancel_pending_vote_followups(self, release: sql.Release) -> None:
        # There is no CANCELLED status for tasks
        # The best alternatives are to either delete them, or mark them as FAILED
        # Deleting them is simpler, but then we have no record of the tasks
        # Therefore, although it's not exactly correct, we mark them as FAILED
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
        auto_resolve_stmt = (
            sqlmodel.update(sql.Task)
            .where(
                via(sql.Task.task_type) == sql.TaskType.VOTE_AUTO_RESOLVE,
                via(sql.Task.status) == sql.TaskStatus.QUEUED,
                via(sql.Task.project_key) == release.project.key,
                via(sql.Task.version_key) == release.version,
            )
            .values(
                status=sql.TaskStatus.FAILED,
                completed=datetime.datetime.now(datetime.UTC),
                error="Vote resolved before auto-resolution fired",
            )
        )
        await self.__data.execute(auto_resolve_stmt)

    # def __committee_member_or_admin(self, committee: sql.Committee, asf_uid: str) -> None:
    #     if not (user.is_committee_member(committee, asf_uid) or user.is_admin(asf_uid)):
    #         raise storage.AccessError("You do not have permission to perform this action")


def format_vote_email_body(
    vote: str,
    asf_uid: str,
    fullname: str,
    is_binding: bool,
    comment: str = "",
    potency_label: str | None = None,
) -> str:
    """Format the body of a vote email.

    Args:
        vote: The vote value (+1, 0, or -1)
        asf_uid: The ASF user ID of the voter
        fullname: The full name of the voter
        is_binding: Whether this is a binding vote (PMC member)
        comment: Optional comment to include

    Returns:
        The formatted email body text
    """
    # audit_guidance all email is sent through `atr.mail` which handles validation
    if potency_label is not None:
        body = [f"{vote} ({potency_label}) ({asf_uid}) {fullname}"]
    elif is_binding:
        body = [f"{vote} (binding) ({asf_uid}) {fullname}"]
    else:
        body = [f"{vote} ({asf_uid}) {fullname}"]
    if comment:
        body.append(f"{comment}")
        # Only include the signature if there is a comment
        body.append(f"-- \n{fullname} ({asf_uid})")
    return "\n\n".join(body)


def _vote_potency_label(release: sql.Release, is_binding: bool) -> str | None:
    vote_round = None
    if (release.committee is not None) and release.committee.is_podling:
        vote_round = 2 if (release.podling_thread_id is not None) else 1
    binding_word, non_binding_word = user.binding_terminology(vote_round)
    if is_binding:
        return binding_word.lower()
    if vote_round == 1:
        return non_binding_word.lower()
    return None
