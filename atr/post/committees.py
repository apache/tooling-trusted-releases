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
from typing import Literal

import atr.blueprints.post as post
import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.strings as strings
import atr.util as util
import atr.web as web


@post.typed
async def view(
    session: web.Committer,
    _committees: Literal["committees"],
    name: safe.CommitteeKey,
    committees_form: shared.committees.CommitteesForm,
) -> web.WerkzeugResponse:
    """
    URL: /committees/<name>
    Handle the release manager designation forms on the committee page.
    """
    if committees_form.committee_key != str(name):
        raise RuntimeError("Committee key mismatch")

    match committees_form:
        case shared.committees.AddReleaseManagerForm() as add_form:
            return await _release_manager_add(session, name, add_form)

        case shared.committees.RemoveReleaseManagerForm() as remove_form:
            return await _release_manager_remove(session, name, remove_form)

        case shared.committees.RequestVoteModeForm() as request_form:
            return await _vote_mode_request(session, name, request_form)


async def _release_manager_add(
    session: web.Committer, name: safe.CommitteeKey, add_form: shared.committees.AddReleaseManagerForm
) -> web.WerkzeugResponse:
    try:
        async with storage.write_as_committee_member(str(name), session) as wacm:
            added = await wacm.committee.release_manager_add(add_form.asf_uid)
    except storage.AccessError as e:
        return await session.redirect(get.committees.view, error=str(e), name=str(name))
    asf_uid = add_form.asf_uid.strip().lower()
    if added:
        message = f"Designated {asf_uid} as a release manager."
    else:
        message = f"{asf_uid} is already a release manager."
    return await session.redirect(get.committees.view, success=message, name=str(name))


async def _release_manager_remove(
    session: web.Committer, name: safe.CommitteeKey, remove_form: shared.committees.RemoveReleaseManagerForm
) -> web.WerkzeugResponse:
    try:
        async with storage.write_as_committee_member(str(name), session) as wacm:
            removed = await wacm.committee.release_manager_remove(remove_form.asf_uid)
    except storage.AccessError as e:
        return await session.redirect(get.committees.view, error=str(e), name=str(name))
    asf_uid = remove_form.asf_uid.strip().lower()
    if removed:
        return await session.redirect(
            get.committees.view, success=f"Removed {asf_uid} as a release manager.", name=str(name)
        )
    return await session.redirect(get.committees.view, error=f"{asf_uid} is not a release manager.", name=str(name))


async def _vote_mode_request(
    session: web.Committer, name: safe.CommitteeKey, request_form: shared.committees.RequestVoteModeForm
) -> web.WerkzeugResponse:
    if not config.get().CAP_ROLE_ACCOUNT_TOKEN:
        return await session.redirect(get.committees.view, error=strings.CAP_NOT_CONFIGURED, name=str(name))
    async with db.session() as data:
        committee = await data.committee(key=str(name)).demand(
            errors.ASFQuartException(f"Committee {name!s} not found", errorcode=404)
        )
        error = await interaction.committee_vote_mode_request_error(committee, data)
    if request_form.vote_mode != committee.effective_vote_mode:
        error = "Streamlined voting changed since you loaded the page. Please check it and try again."
    action = interaction.committee_vote_mode_request_action(committee)
    direction = "on" if (action == sql.ApprovalAction.TRUSTED_VOTING) else "off"

    committee_url = util.absolute_url(util.as_url(get.committees.view, name=str(name)))
    closes_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=constants.CAP_VOTE_DURATION_MINUTES)
    async with storage.write(session) as write:
        try:
            wacm = write.as_committee_member(str(name))
        except storage.AccessError as e:
            return await session.redirect(get.committees.view, error=str(e), name=str(name))
        if error is not None:
            return await session.redirect(get.committees.view, error=error, name=str(name))
        try:
            approval_id = await wacm.committee.vote_mode_request_reserve(action, closes_at)
        except storage.AccessError as e:
            return await session.redirect(get.committees.view, error=str(e), name=str(name))
        try:
            question = await util.cap_create_vote_mode_question(
                action,
                str(name),
                session.uid,
                closes_at,
                committee_url=committee_url,
                explanation=request_form.explanation,
            )
        except util.FetchError as e:
            await wacm.committee.vote_mode_request_abandon(approval_id, str(e))
            return await session.redirect(
                get.committees.view, error=f"Could not create CAP approval vote: {e}", name=str(name)
            )
        log.info(f"CAP question {question.question_id} created to turn streamlined voting {direction} in {name!s}")
        await wacm.committee.vote_mode_request_record(approval_id, question.question_id)

    return await session.redirect(
        get.committees.view,
        success=(
            f"Approval vote created for the {name!s} PMC (CAP #{question.question_id}, closes "
            f"{closes_at.strftime('%Y-%m-%d %H:%M UTC')}). ATR will turn streamlined voting {direction} if it passes."
        ),
        name=str(name),
    )
