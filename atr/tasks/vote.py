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

import atr.config as config
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
        if release.effective_vote_mode != sql.VoteMode.TRUSTED:
            log.info(f"Vote end notify skipped for {task_args.release_key}: not in trusted mode")
            return _end_notify_skipped("not_trusted_mode")
        review_url = f"https://{config.get().APP_HOST}/vote/{release.project.key}/{release.version}"

    sender_recipient = f"{task_args.recipient_id}@apache.org"
    subject = f"[ATR] Vote ready to resolve: {task_args.release_key}"
    body = (
        f"The vote for {task_args.release_key} reached its scheduled end at {task_args.vote_end}.\n\n"
        f"Please review the recorded ballots and resolve the vote in ATR:\n{review_url}"
    )
    message = mail.Message(
        email_sender=sender_recipient,
        email_to=sender_recipient,
        subject=subject,
        body=body,
    )

    async with storage.write(task_args.recipient_id) as write:
        wafc = write.as_foundation_committer()
        mid, mail_errors = await wafc.mail.send(message, mail.MailFooterCategory.AUTO)

    if mail_errors:
        log.warning(f"Vote end notify mail to {sender_recipient} produced warnings: {mail_errors}")
    else:
        log.info(f"Vote end notify mail sent to {sender_recipient}")

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


async def _initiate_core_logic(task_args: args.Initiate) -> results.Results | None:  # noqa: C901
    """Get arguments, create an email, and then send it to the recipient."""
    log.info("Starting initiate_core")
    safe.ReleaseKey(task_args.release_key)

    # Validate arguments
    all_addrs = [task_args.email_to, *task_args.email_cc, *task_args.email_bcc]
    for addr in all_addrs:
        if not (addr.endswith("@apache.org") or addr.endswith(".apache.org")):
            log.error(f"Invalid destination email address: {addr}")
            raise VoteInitiationError(f"Invalid destination email address: {addr}")

    async with db.session() as data:
        release = await data.release(key=task_args.release_key, _project=True, _committee=True).demand(
            VoteInitiationError(f"Release {task_args.release_key!s} not found")
        )
        latest_revision_number = release.latest_revision_number
        if latest_revision_number is None:
            raise VoteInitiationError(f"No revisions found for release {task_args.release_key!s}")
        if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE:
            raise VoteInitiationError(f"Vote task is stale for release {task_args.release_key!s}")
        if task_args.vote_seq is None:
            if release.current_vote_seq is not None:
                raise VoteInitiationError(f"Vote task is stale for release {task_args.release_key!s}")
        elif release.current_vote_seq != task_args.vote_seq:
            raise VoteInitiationError(f"Vote task is stale for release {task_args.release_key!s}")

        ongoing_tasks = await interaction.tasks_ongoing(
            release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
        )
        if ongoing_tasks > 0:
            raise VoteInitiationError(
                f"Cannot start vote for {task_args.release_key!s} as {ongoing_tasks} are not complete"
            )

    # Calculate vote end date
    vote_duration_hours = task_args.vote_duration
    vote_start = datetime.datetime.now(datetime.UTC)
    vote_end = vote_start + datetime.timedelta(hours=vote_duration_hours)

    # Format dates for email
    vote_end_str = vote_end.strftime("%Y-%m-%d %H:%M:%S UTC")

    # # Load and set DKIM key
    # try:
    #     await mail.set_secret_key_default()
    # except Exception as e:
    #     error_msg = f"Failed to load DKIM key: {e}"
    #     log.error(error_msg)
    #     raise VoteInitiationError(error_msg)

    # Get PMC and project details
    if release.committee is None:
        error_msg = "Release has no associated committee"
        log.error(error_msg)
        raise VoteInitiationError(error_msg)

    # The subject and body have already been substituted by the route handler
    subject = task_args.subject
    body = task_args.body

    is_podling_round_two = release.committee.is_podling and (release.podling_thread_id is not None)
    # A second round vote may be started by a different user, so the permitted recipients will be different
    # Since we only have to validate before task boundaries, we can skip this for second rounds
    if not is_podling_round_two:
        permitted_recipients = util.permitted_podling_first_round_recipients(
            task_args.initiator_id,
            release.committee.key,
            is_podling=release.committee.is_podling,
        )
        for addr in all_addrs:
            if addr not in permitted_recipients:
                log.error(f"Invalid mailing list choice: {addr} not in {permitted_recipients}")
                raise VoteInitiationError("Invalid mailing list choice")

    # Create mail message
    log.info(f"Creating mail message for {task_args.email_to}")
    message = mail.Message(
        email_sender=f"{task_args.initiator_id}@apache.org",
        email_to=task_args.email_to,
        subject=subject,
        body=body,
        email_cc=task_args.email_cc,
        email_bcc=task_args.email_bcc,
    )

    async with storage.write(task_args.initiator_id) as write:
        wafc = write.as_foundation_committer()
        mid, mail_errors = await wafc.mail.send(message, mail.MailFooterCategory.USER)

    # Original success message structure
    all_destinations = [task_args.email_to, *task_args.email_cc, *task_args.email_bcc]
    result = results.VoteInitiate(
        kind="vote_initiate",
        message="Vote announcement email sent successfully",
        email_to=task_args.email_to,
        vote_end=vote_end_str,
        subject=subject,
        mid=mid,
        mail_send_warnings=mail_errors,
    )

    if mail_errors:
        log.warning(f"Start vote for {task_args.release_key}: sending to {all_destinations} gave errors: {mail_errors}")
    else:
        log.info(f"Vote email sent successfully to {all_destinations}")

    if (
        task_args.notify_when_finished
        and (task_args.vote_seq is not None)
        and (vote_duration_hours > 0)
        and (release.effective_vote_mode == sql.VoteMode.TRUSTED)
    ):
        try:
            await _schedule_end_notify(
                task_args=task_args,
                project_key=release.project.key,
                version_key=release.version,
                vote_end=vote_end,
                vote_end_str=vote_end_str,
            )
        except Exception as schedule_error:
            # TODO: Should we do anything else here?
            # This is effectively a silent failure, from the user's perspective
            log.exception(f"Vote end notify could not be scheduled for {task_args.release_key}: {schedule_error}")

    return result


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
