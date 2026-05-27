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
import atr.shared as shared
import atr.storage as storage
import atr.web as web


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
    email_body = submit_form.email_body.replace("{{OUTCOME}}", writer_result)

    automatic_resolve_when_finished = _read_auto_resolve_flag(submit_form)
    notify_when_finished = _read_notify_flag(submit_form)
    bcc_private_list = _read_bcc_private_flag(submit_form)

    try:
        async with storage.write_as_project_release_manager(project_key) as warm:
            _release, voting_round, success_message, error_message = await warm.vote.resolve(
                project_key,
                version_key,
                writer_result,
                session.fullname,
                email_body,
                expected_vote_seq=submit_form.vote_seq,
                expected_vote_mode=submit_form.vote_mode,
                automatic_resolve_when_finished=automatic_resolve_when_finished,
                notify_when_finished=notify_when_finished,
                bcc_private_list=bcc_private_list,
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


def _read_bcc_private_flag(submit_form: shared.resolve.ResolveForm) -> bool:
    if isinstance(submit_form, shared.resolve.SubmitForm):
        return bool(submit_form.bcc_private_list)
    return False


def _read_notify_flag(submit_form: shared.resolve.ResolveForm) -> bool:
    if isinstance(submit_form, shared.resolve.SubmitForm):
        return bool(submit_form.notify_when_finished)
    return False
