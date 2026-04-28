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

from __future__ import annotations

from typing import Literal

import atr.blueprints.post as post
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


class BodyPreviewForm(form.Form):
    vote_duration: form.Int = form.label("Vote duration")
    vote_mode: sql.VoteMode = form.label("Vote mode", widget=form.Widget.HIDDEN)


@post.typed
async def body_preview(
    session: web.Committer,
    _voting_body_preview: Literal["voting/body/preview"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    preview_form: BodyPreviewForm,
) -> web.QuartResponse:
    """
    URL: /voting/body/preview/<project_key>/<version_key>/<revision_number>
    """

    async with db.session() as data:
        release = await session.release(
            project_key,
            version_key,
            data=data,
            with_release_policy=True,
            with_project_release_policy=True,
        )
        vote_mode = release.effective_vote_mode
    if vote_mode != preview_form.vote_mode:
        return web.TextResponse(
            "The vote mode has changed since you loaded the form. Please reload and try again.", 409
        )

    default_subject_template = await construct.start_vote_subject_default(project_key)
    default_body_template = await construct.start_vote_default(project_key)

    options = construct.StartVoteOptions(
        asfuid=session.uid,
        fullname=session.fullname,
        project_key=project_key,
        version_key=version_key,
        revision_number=revision_number,
        vote_duration=preview_form.vote_duration,
    )
    _, body = await construct.start_vote_subject_and_body(default_subject_template, default_body_template, options)

    return web.TextResponse(body)


@post.typed
async def selected_revision(
    session: web.Committer,
    _voting: Literal["voting"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
    start_voting_form: shared.voting.StartVotingForm,
) -> web.WerkzeugResponse | str:
    """
    URL: /voting/<project_key>/<version_key>/<revision>
    """

    async with db.session() as data:
        match await interaction.release_ready_for_vote(
            session,
            project_key,
            version_key,
            revision,
            data,
            frozenset({sql.VoteMode.EMAIL, sql.VoteMode.TRUSTED}),
        ):
            case str() as error:
                return await session.redirect(
                    get.compose.selected,
                    error=error,
                    project_key=str(project_key),
                    version_key=str(version_key),
                    revision=str(revision),
                )
            case (release, committee):
                pass

        vote_mode = release.effective_vote_mode
        if vote_mode != start_voting_form.vote_mode:
            return await session.form_error(
                "vote_mode",
                "The vote mode has changed since you loaded the form. Please reload and try again.",
            )

        permitted_recipients = util.permitted_podling_first_round_recipients(
            session.uid,
            committee.key,
            is_podling=committee.is_podling,
        )
        all_addrs = [start_voting_form.email_to, *start_voting_form.email_cc, *start_voting_form.email_bcc]
        for addr in all_addrs:
            if addr not in permitted_recipients:
                return await session.form_error(
                    "email_to",
                    f"Invalid recipient selection: {addr}",
                )

        second_round_email_to: str | None = None
        if committee.is_podling and (start_voting_form.second_round_email_to is not None):
            second_round_permitted = util.permitted_podling_second_round_recipients(session.uid)
            if start_voting_form.second_round_email_to not in second_round_permitted:
                return await session.form_error(
                    "second_round_email_to",
                    f"Invalid second round recipient selection: {start_voting_form.second_round_email_to}",
                )
            second_round_email_to = start_voting_form.second_round_email_to

        subject_template = await construct.start_vote_subject_default(project_key)
        current_hash = construct.template_hash(subject_template)
        if current_hash != start_voting_form.subject_template_hash:
            return await session.form_error(
                "subject_template_hash",
                "The subject template has been modified since you loaded the form. Please reload and try again.",
            )

        # Substitute the subject template (must be done here, not in task, as it requires app context)
        options = construct.StartVoteOptions(
            asfuid=session.uid,
            fullname=session.fullname,
            project_key=project_key,
            version_key=version_key,
            revision_number=revision,
            vote_duration=start_voting_form.vote_duration,
        )
        subject, _ = await construct.start_vote_subject_and_body(subject_template, "", options)

        async with storage.write_as_committee_participant(committee.key) as wacp:
            _task = await wacp.vote.start(
                start_voting_form.email_to,
                project_key,
                version_key,
                revision,
                start_voting_form.vote_duration,
                subject,
                start_voting_form.body,
                session.fullname,
                release=release,
                promote=True,
                permitted_recipients=permitted_recipients,
                email_cc=start_voting_form.email_cc,
                email_bcc=start_voting_form.email_bcc,
                second_round_email_to=second_round_email_to,
                expected_vote_mode=start_voting_form.vote_mode,
            )

        log.info(f"Vote email will be sent to: {all_addrs}")
        return await session.redirect(
            get.vote.selected,
            success=f"The vote announcement email will soon be sent to {start_voting_form.email_to}.",
            project_key=str(project_key),
            version_key=str(version_key),
        )
