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

import datetime

import atr.db as db
import atr.db.interaction as interaction
import atr.log as log
import atr.mail as mail
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.tasks.checks as checks
import atr.util as util


class VoteInitiationError(Exception):
    pass


@checks.with_model(args.VoteEndNotify)
async def end_notify(task_args: args.VoteEndNotify) -> results.Results | None:
    """Send a self addressed reminder when a vote ends, if it has not been resolved."""
    async with db.session() as data:
        release = await data.release(key=task_args.release_key, _project=True, _committee=True).get()
        if release is None:
            log.info(f"Vote end notify skipped: release {task_args.release_key} not found")
            return _end_notify_skipped("release_not_found")
        if release.vote_resolved is not None:
            log.info(f"Vote end notify skipped for {task_args.release_key}: already resolved")
            return _end_notify_skipped("already_resolved")
        if release.current_vote_seq != task_args.vote_seq:
            log.info(
                f"Vote end notify skipped for {task_args.release_key}: vote_seq changed "
                f"({release.current_vote_seq} != {task_args.vote_seq})"
            )
            return _end_notify_skipped("vote_seq_changed")
        if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE:
            log.info(f"Vote end notify skipped for {task_args.release_key}: not in candidate phase")
            return _end_notify_skipped("not_in_candidate_phase")
        if release.expedited:
            log.info(f"Vote end notify skipped for {task_args.release_key}: expedited release")
            return _end_notify_skipped("expedited_release")
        review_url = util.absolute_url(f"/vote/{release.project.key}/{release.version}")

    subject = f"[ATR] Vote ended for {task_args.release_key}"
    body = (
        f"The vote for {task_args.release_key} has ended. "
        f"Please review the recorded ballots and resolve the vote in ATR at {review_url}"
    )
    mid, mail_errors = await _send_vote_end_notify_mail(task_args.recipient_id, subject, body)

    return results.VoteEndNotify(
        kind="vote_end_notify",
        sent=True,
        skip_reason=None,
        mid=mid,
        mail_send_warnings=mail_errors,
    )


@checks.with_model(args.Initiate)
async def initiate(task_args: args.Initiate) -> results.Results | None:
    """Initiate a vote for a release."""
    try:
        return await _initiate_core_logic(task_args)
    except VoteInitiationError as e:
        log.error(f"Vote initiation failed: {e}")
        raise
    except Exception as e:
        log.exception(f"Unexpected error during vote initiation: {e}")
        raise


def _end_notify_skipped(reason: str) -> results.VoteEndNotify:
    return results.VoteEndNotify(
        kind="vote_end_notify",
        sent=False,
        skip_reason=reason,
        mid=None,
        mail_send_warnings=[],
    )


async def _initiate_core_logic(task_args: args.Initiate) -> results.Results | None:
    """Validate task arguments, send the vote announcement email, and schedule VOTE_END_NOTIFY if opted in."""
    safe.ReleaseKey(task_args.release_key)
    _validate_initiate_addresses([task_args.email_to, *task_args.email_cc, *task_args.email_bcc])
    release = await _load_release_for_initiate(task_args)
    _validate_initiate_permissions(task_args, release)

    vote_start = datetime.datetime.now(datetime.UTC)
    vote_end = vote_start + datetime.timedelta(hours=task_args.vote_duration)
    vote_end_str = vote_end.strftime("%Y-%m-%d %H:%M:%S UTC")

    mid, mail_errors = await _send_initiate_email(task_args)
    result = results.VoteInitiate(
        kind="vote_initiate",
        message="Vote announcement email sent successfully",
        email_to=task_args.email_to,
        vote_end=vote_end_str,
        subject=task_args.subject,
        mid=mid,
        mail_send_warnings=mail_errors,
    )
    if (
        task_args.notify_when_finished
        and (task_args.vote_seq is not None)
        and (task_args.vote_duration > 0)
        and (not release.expedited)
    ):
        await _schedule_end_notify_safe(
            task_args=task_args,
            project_key=release.project.key,
            version_key=release.version,
            vote_end=vote_end,
            vote_end_str=vote_end_str,
        )
    return result


async def _load_release_for_initiate(task_args: args.Initiate) -> sql.Release:
    async with db.session() as data:
        release = await data.release(
            key=task_args.release_key, _project=True, _committee=True, _project_release_policy=True
        ).demand(VoteInitiationError(f"Release {task_args.release_key!s} not found"))
        if release.latest_revision_number is None:
            raise VoteInitiationError(f"No revisions found for release {task_args.release_key!s}")
        if (release.phase != sql.ReleasePhase.RELEASE_CANDIDATE) or _vote_seq_is_stale(task_args, release):
            raise VoteInitiationError(f"Vote task is stale for release {task_args.release_key!s}")
        ongoing_tasks = await interaction.tasks_ongoing(
            release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
        )
        if ongoing_tasks > 0:
            raise VoteInitiationError(
                f"Cannot start vote for {task_args.release_key!s} as {ongoing_tasks} are not complete"
            )
    return release


async def _schedule_end_notify(
    *,
    task_args: args.Initiate,
    project_key: str,
    version_key: str,
    vote_end: datetime.datetime,
    vote_end_str: str,
) -> None:
    if task_args.vote_seq is None:
        return
    recipient_id = task_args.initiator_id
    notify_args = args.VoteEndNotify(
        release_key=task_args.release_key,
        vote_seq=task_args.vote_seq,
        recipient_id=recipient_id,
        vote_end=vote_end_str,
    )
    notify_task = sql.Task(
        status=sql.TaskStatus.QUEUED,
        task_type=sql.TaskType.VOTE_END_NOTIFY,
        task_args=notify_args.model_dump(),
        asf_uid=recipient_id,
        project_key=project_key,
        version_key=version_key,
    )
    notify_task.scheduled = vote_end
    async with db.session() as data:
        data.add(notify_task)
        await data.commit()
    log.info(f"Vote end notify scheduled for {task_args.release_key} at {vote_end_str} (recipient={recipient_id})")


async def _schedule_end_notify_safe(
    *,
    task_args: args.Initiate,
    project_key: str,
    version_key: str,
    vote_end: datetime.datetime,
    vote_end_str: str,
) -> None:
    try:
        await _schedule_end_notify(
            task_args=task_args,
            project_key=project_key,
            version_key=version_key,
            vote_end=vote_end,
            vote_end_str=vote_end_str,
        )
    except Exception as schedule_error:
        log.exception(f"Vote end notify could not be scheduled for {task_args.release_key}: {schedule_error}")


async def _send_initiate_email(task_args: args.Initiate) -> tuple[str | None, list[str]]:
    all_destinations = [task_args.email_to, *task_args.email_cc, *task_args.email_bcc]
    message = mail.Message(
        email_sender=f"{task_args.initiator_id}@apache.org",
        email_to=task_args.email_to,
        subject=task_args.subject,
        body=task_args.body,
        email_cc=task_args.email_cc,
        email_bcc=task_args.email_bcc,
    )
    async with storage.write(task_args.initiator_id) as write:
        wafc = write.as_foundation_committer()
        mid, mail_errors = await wafc.mail.send(message, mail.MailFooterCategory.USER)
    if mail_errors and (len(mail_errors) >= len(all_destinations)):
        raise VoteInitiationError(f"Failed to send the vote email to any recipient: {'; '.join(mail_errors)}")
    if mail_errors:
        log.warning(f"Start vote for {task_args.release_key}: sending to {all_destinations} gave errors: {mail_errors}")
    else:
        log.info(f"Vote email sent successfully to {all_destinations}")
    return mid, mail_errors


async def _send_vote_end_notify_mail(
    recipient_id: str,
    subject: str,
    body: str,
) -> tuple[str | None, list[str]]:
    sender_recipient = f"{recipient_id}@apache.org"
    message = mail.Message(
        email_sender=sender_recipient,
        email_to=sender_recipient,
        subject=subject,
        body=body,
    )
    async with storage.write(recipient_id) as write:
        wafc = write.as_foundation_committer()
        mid, mail_errors = await wafc.mail.send(message, mail.MailFooterCategory.AUTO)
    if mail_errors:
        log.warning(f"Vote end notify mail to {sender_recipient} produced warnings: {mail_errors}")
    else:
        log.info(f"Vote end notify mail sent to {sender_recipient}")
    return mid, mail_errors


def _validate_initiate_addresses(addresses: list[str]) -> None:
    for addr in addresses:
        if not (addr.endswith("@apache.org") or addr.endswith(".apache.org")):
            log.error(f"Invalid destination email address: {addr}")
            raise VoteInitiationError(f"Invalid destination email address: {addr}")


def _validate_initiate_permissions(task_args: args.Initiate, release: sql.Release) -> None:
    if release.committee is None:
        log.error("Release has no associated committee")
        raise VoteInitiationError("Release has no associated committee")
    is_podling_round_two = release.committee.is_podling and (
        (release.podling_thread_id is not None) or (task_args.vote_seq != release.current_vote_seq)
    )
    if is_podling_round_two:
        return
    permitted_recipients = util.permitted_podling_first_round_recipients(
        task_args.initiator_id,
        release.committee.key,
        is_podling=release.committee.is_podling,
        project=release.project,
    )
    for addr in (task_args.email_to, *task_args.email_cc, *task_args.email_bcc):
        if addr not in permitted_recipients:
            log.error(f"Invalid mailing list choice: {addr} not in {permitted_recipients}")
            raise VoteInitiationError("Invalid mailing list choice")


def _vote_seq_is_stale(task_args: args.Initiate, release: sql.Release) -> bool:
    if task_args.vote_seq is None:
        return release.current_vote_seq is not None
    return (release.current_vote_seq is None) or (release.current_vote_seq > task_args.vote_seq)
