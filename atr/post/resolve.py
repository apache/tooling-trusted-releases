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

from typing import Literal

import quart

import atr.blueprints.post as post
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@post.typed
async def interpret(
    session: web.Committer,
    _resolve_interpret: Literal["resolve/interpret"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    interpret_form: shared.resolve.InterpretForm,
) -> web.WerkzeugResponse:
    choice = sql.VoteChoice(interpret_form.choice) if interpret_form.choice else None
    try:
        async with storage.write() as write:
            warm = await write.as_project_release_manager(project_key)
            await warm.vote.interpret(project_key, version_key, interpret_form.asf_eid, choice)
    except storage.AccessError as e:
        return await session.redirect(
            get.resolve.selected,
            error=str(e),
            project_key=str(project_key),
            version_key=str(version_key),
        )
    return await session.redirect(
        get.resolve.selected,
        project_key=str(project_key),
        version_key=str(version_key),
        success=f"Vote interpreted as {choice.value}" if choice else "Vote interpretation reset",
    )


@post.typed
async def selected(
    session: web.Committer,
    _resolve: Literal["resolve"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    submit_form: shared.resolve.ResolveForm,
) -> web.WerkzeugResponse:
    """
    URL: /resolve/<project_key>/<version_key>
    """
    vote_result = submit_form.vote_result

    match vote_result:
        case "Passed":
            writer_result = "passed"
        case "Failed":
            writer_result = "failed"
        case "Cancelled":
            writer_result = "cancelled"

    # GET defers {{OUTCOME}} until now because it depends on the user's selection
    outcome = "was cancelled" if (writer_result == "cancelled") else writer_result
    email_body = submit_form.email_body.replace("{{OUTCOME}}", outcome)

    automatic_resolve_when_finished = _read_auto_resolve_flag(submit_form)
    notify_when_finished = _read_notify_flag(submit_form)
    security_override = isinstance(submit_form, shared.resolve.SubmitForm) and submit_form.security_override

    try:
        async with storage.write() as write:
            resolve = (
                (await write.as_project_security_team(project_key)).vote_resolve
                if security_override
                else (await write.as_project_release_manager(project_key)).vote.resolve
            )
            _release, voting_round, success_message, error_message = await resolve(
                project_key,
                version_key,
                writer_result,
                session.fullname,
                email_body,
                expected_vote_seq=submit_form.vote_seq,
                expected_vote_mode=submit_form.vote_mode,
                automatic_resolve_when_finished=automatic_resolve_when_finished,
                notify_when_finished=notify_when_finished,
                additional_cc=submit_form.email_cc,
                additional_bcc=submit_form.email_bcc,
                early_reason=submit_form.early_reason if isinstance(submit_form, shared.resolve.SubmitForm) else "",
            )
    except storage.AccessError as e:
        return await session.redirect(
            get.resolve.selected,
            error=str(e),
            project_key=str(project_key),
            version_key=str(version_key),
        )

    if error_message is not None:
        await quart.flash(error_message, "error")

    match (vote_result, voting_round):
        case "Passed", 1:
            destination = get.vote.selected
        case "Passed", _:
            destination = get.finish.selected
        case "Failed", _:
            destination = get.compose.selected
        case "Cancelled", _:
            destination = get.compose.selected

    return await session.redirect(
        destination, project_key=str(project_key), version_key=str(version_key), success=success_message
    )


def _read_auto_resolve_flag(submit_form: shared.resolve.ResolveForm) -> bool:
    if isinstance(submit_form, shared.resolve.SubmitForm):
        return bool(submit_form.automatic_resolve_when_finished)
    return False


def _read_notify_flag(submit_form: shared.resolve.ResolveForm) -> bool:
    if isinstance(submit_form, shared.resolve.SubmitForm):
        return bool(submit_form.notify_when_finished)
    return False
