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
import atr.storage as storage
import atr.tasks.checks as checks
import atr.util as util


class VoteInitiationError(Exception):
    pass


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


async def _initiate_core_logic(task_args: args.Initiate) -> results.Results | None:
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
    return result
