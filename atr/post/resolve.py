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
    submit_form: shared.resolve.SubmitForm,
) -> web.WerkzeugResponse:
    """
    URL: /resolve/<project_key>/<version_key>
    """
    email_body = submit_form.email_body
    vote_result = submit_form.vote_result

    match vote_result:
        case "Passed":
            writer_result = "passed"
        case "Failed":
            writer_result = "failed"
        case "Cancelled":
            writer_result = "cancelled"

    async with storage.write_as_project_committee_member(project_key) as wacm:
        _release, voting_round, success_message, error_message = await wacm.vote.resolve(
            project_key,
            version_key,
            writer_result,
            session.fullname,
            email_body,
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
