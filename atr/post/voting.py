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
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


class BodyPreviewForm(form.Form):
    vote_duration: form.Int = form.label("Vote duration")


@post.typed
async def body_preview(
    session: web.Committer,
    _voting_body_preview: Literal["voting/body/preview"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    revision_number: str,
    preview_form: BodyPreviewForm,
) -> web.QuartResponse:
    """
    URL: /voting/body/preview/<project_name>/<version_name>/<revision_number>
    """

    default_subject_template = await construct.start_vote_subject_default(project_name)
    default_body_template = await construct.start_vote_default(project_name)

    options = construct.StartVoteOptions(
        asfuid=session.uid,
        fullname=session.fullname,
        project_name=project_name,
        version_name=version_name,
        revision_number=revision_number,
        vote_duration=preview_form.vote_duration,
    )
    _, body = await construct.start_vote_subject_and_body(default_subject_template, default_body_template, options)

    return web.TextResponse(body)


@post.typed
async def selected_revision(
    session: web.Committer,
    _voting: Literal["voting"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    revision: str,
    start_voting_form: shared.voting.StartVotingForm,
) -> web.WerkzeugResponse | str:
    """
    URL: /voting/<project_name>/<version_name>/<revision>
    """

    async with db.session() as data:
        match await interaction.release_ready_for_vote(
            session, project_name, version_name, revision, data, manual_vote=False
        ):
            case str() as error:
                return await session.redirect(
                    get.compose.selected,
                    error=error,
                    project_name=str(project_name),
                    version_name=str(version_name),
                    revision=revision,
                )
            case (release, committee):
                pass

        permitted_recipients = util.permitted_voting_recipients(session.uid, committee.name)
        if start_voting_form.mailing_list not in permitted_recipients:
            return await session.form_error(
                "mailing_list",
                f"Invalid mailing list selection: {start_voting_form.mailing_list}",
            )

        subject_template = await construct.start_vote_subject_default(project_name)
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
            project_name=project_name,
            version_name=version_name,
            revision_number=revision,
            vote_duration=start_voting_form.vote_duration,
        )
        subject, _ = await construct.start_vote_subject_and_body(subject_template, "", options)

        async with storage.write_as_committee_participant(committee.name) as wacp:
            _task = await wacp.vote.start(
                start_voting_form.mailing_list,
                project_name,
                version_name,
                revision,
                start_voting_form.vote_duration,
                subject,
                start_voting_form.body,
                session.uid,
                session.fullname,
                release=release,
                promote=True,
                permitted_recipients=permitted_recipients,
            )

        log.info(f"Vote email will be sent to: {start_voting_form.mailing_list}")
        return await session.redirect(
            get.vote.selected,
            success=f"The vote announcement email will soon be sent to {start_voting_form.mailing_list}.",
            project_name=str(project_name),
            version_name=str(version_name),
        )
