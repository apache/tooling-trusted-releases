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

import atr.constants as constants
import atr.db as db
import atr.log as log
import atr.models.args as args
import atr.models.cap
import atr.models.results as results
import atr.models.sql as sql
import atr.storage as storage
import atr.tasks as tasks
import atr.tasks.checks as checks
import atr.util as util


async def notify(
    asf_uid: str,
    message: str,
    level: sql.NotificationLevel,
    link: str | None = None,
    link_text: str | None = None,
) -> None:
    if asf_uid == constants.SYSTEM_SERVICE_UID:
        return
    try:
        async with storage.write_as_user_service(asf_uid) as waus:
            await waus.notifications_create(message, level, link=link, link_text=link_text)
    except Exception:
        log.exception("Failed to record CAP notification")


@checks.with_model(args.CapApprovalResolveArgs)
async def resolve(task_args: args.CapApprovalResolveArgs, *, task_id: int | None = None) -> results.Results | None:
    result = results.CapApprovalResolve(kind="cap_approval_resolve")
    async with db.session() as data:
        row = await data.approval_request(id=task_args.approval_request_id).get()
    if row is None:
        log.error(f"CAP approval request {task_args.approval_request_id} not found")
        return result
    if row.status != sql.ApprovalStatus.PENDING:
        return result

    try:
        resolution = await _resolve_question(row.cap_question_id)
        if (resolution is not None) and (resolution.outcome is not None):
            await _apply_outcome(task_args.approval_request_id, row, resolution)
            return result
        if resolution is not None:
            log.warning(f"CAP resolution for question {row.cap_question_id} returned no outcome")
    except Exception:
        log.exception(f"CAP approval resolve failed for request {task_args.approval_request_id}")

    await _reschedule_or_fail(task_args, row)
    return result


async def _apply_outcome(request_id: int, row: sql.ApprovalRequest, resolution: atr.models.cap.Resolution) -> None:
    vote_outcome = resolution.outcome
    permalink = resolution.permalink
    if vote_outcome == "approved":
        if await _record_outcome(request_id, sql.ApprovalStatus.APPROVED, vote_outcome, permalink):
            await notify(
                row.requested_by,
                f"The CAP approval vote to {_describe_request(row)} passed. You can now complete this in ATR.",
                sql.NotificationLevel.INFO,
                link=_completion_link(row),
                link_text="Complete it here",
            )
        return
    if await _record_outcome(request_id, sql.ApprovalStatus.REJECTED, vote_outcome, permalink):
        await notify(
            row.requested_by,
            f"The CAP approval vote to {_describe_request(row)} did not pass (outcome: {vote_outcome}).",
            sql.NotificationLevel.WARNING,
        )


def _completion_link(row: sql.ApprovalRequest) -> str:
    # The page carrying the button that completes this approval
    if (row.action == sql.ApprovalAction.ARCHIVE_RELEASE) and (row.release_version is not None):
        return f"/file/{row.project_key}/{row.release_version}"
    return "/projects"


def _describe_request(row: sql.ApprovalRequest) -> str:
    if row.action == sql.ApprovalAction.ARCHIVE_RELEASE:
        return f"archive release {row.project_key} {row.release_version}"
    return f"{row.action.value} project {row.project_key}"


async def _record_outcome(
    request_id: int,
    status: sql.ApprovalStatus,
    vote_outcome: str | None,
    permalink: str | None,
    *,
    error: str | None = None,
) -> bool:
    async with storage.write_as_system(storage.WriteAsCapResolveService) as wacrs:
        return await wacrs.project_record_approval_outcome(request_id, status, vote_outcome, permalink, error=error)


async def _reschedule_or_fail(task_args: args.CapApprovalResolveArgs, row: sql.ApprovalRequest) -> None:
    next_attempt = task_args.attempt + 1
    if next_attempt <= len(constants.CAP_RESOLVE_RETRY_DELAYS):
        schedule = datetime.datetime.now(datetime.UTC) + constants.CAP_RESOLVE_RETRY_DELAYS[next_attempt - 1]
        await tasks.cap_approval_resolve(task_args.approval_request_id, attempt=next_attempt, schedule=schedule)
        return
    attempts = len(constants.CAP_RESOLVE_RETRY_DELAYS) + 1
    error = f"CAP could not be resolved after {attempts} attempts"
    if await _record_outcome(task_args.approval_request_id, sql.ApprovalStatus.FAILED, None, None, error=error):
        await notify(
            row.requested_by,
            f"ATR could not resolve the CAP approval vote to {_describe_request(row)}: {error}.",
            sql.NotificationLevel.ERROR,
        )


async def _resolve_question(question_id: int) -> atr.models.cap.Resolution | None:
    try:
        token = await util.cap_mint_token()
        return await util.cap_resolve_question(token, question_id)
    except util.FetchError as error:
        log.warning(f"CAP resolve unavailable for question {question_id}: {error}")
        return None
