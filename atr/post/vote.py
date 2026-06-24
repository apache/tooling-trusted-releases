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
import atr.user as user
import atr.web as web


@post.typed
async def selected_post(  # noqa: C901
    session: web.Committer,
    _vote: Literal["vote"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    cast_vote_form: shared.vote.CastVoteForm,
) -> web.WerkzeugResponse:
    """
    URL: /vote/<project_key>/<version_key>
    """

    release = await session.release(
        project_key,
        version_key,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        with_project_release_policy=True,
    )

    if release.committee is None:
        raise ValueError("Release has no committee")

    if release.effective_vote_mode != cast_vote_form.vote_mode:
        await quart.flash("The vote form is stale, please refresh and try again.", "error")
        return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))

    vote = cast_vote_form.decision
    comment = cast_vote_form.comment

    if release.effective_vote_mode == sql.VoteMode.MANUAL:
        await quart.flash("Voting through this form is not available for manual votes.", "error")
        return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))

    if release.effective_vote_mode == sql.VoteMode.TRUSTED:
        if cast_vote_form.vote_seq is None:
            await quart.flash("Vote serial is missing, please refresh and try again.", "error")
            return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))
        async with storage.write(session) as write:
            wafc = write.as_foundation_committer()
            email_to, error_message = await wafc.vote.cast_trusted(
                project_key,
                version_key,
                sql.VoteChoice(vote),
                comment,
                session.fullname,
                expected_vote_seq=cast_vote_form.vote_seq,
                expected_vote_mode=cast_vote_form.vote_mode,
            )
        if error_message:
            await quart.flash(error_message, "error")
            return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))
        if email_to:
            success_message = f"Your vote has been recorded and a receipt is being sent to {email_to[0]}."
        else:
            success_message = "Your vote has been recorded and a receipt is being sent."
        await quart.flash(success_message, "success")
        return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))

    if release.current_vote_seq != cast_vote_form.vote_seq:
        await quart.flash("The vote form is stale, please refresh and try again.", "error")
        return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))

    vote_round = None
    if release.committee.is_podling:
        vote_round = 2 if (release.podling_thread_id is not None) else 1
    is_binding, _binding_committee = await user.is_binding_for_release(release.committee, session.uid, vote_round)

    async with storage.write_as_committee_participant(release.committee.key) as wacp:
        email_to, error_message = await wacp.vote.send_user_vote(release, vote, comment, session.fullname, is_binding)

    if error_message:
        await quart.flash(error_message, "error")
        return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))

    success_message = f"Sending your vote to {email_to}."
    await quart.flash(success_message, "success")
    return await session.redirect(get.vote.selected, project_key=str(project_key), version_key=str(version_key))
