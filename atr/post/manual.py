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

import atr.blueprints.post as post
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


@post.typed
async def resolve_selected(
    session: web.Committer,
    _manual_resolve: Literal["manual/resolve"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    resolve_vote_form: shared.manual.ResolveVoteForm,
) -> web.WerkzeugResponse | str:
    """
    URL: /manual/resolve/<project_name>/<version_name>
    Post the manual vote resolution page.
    """
    release = await session.release(
        project_name,
        version_name,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    if not release.vote_manual:
        raise RuntimeError("This page is for manual votes only")

    try:
        await _committees_check(resolve_vote_form.vote_thread_url, resolve_vote_form.vote_result_url)
    except RuntimeError as e:
        return await session.redirect(
            get.manual.resolve_selected,
            project_name=str(project_name),
            version_name=str(version_name),
            error=str(e),
        )

    match resolve_vote_form.vote_result:
        case "Passed":
            vote_result = "passed"
            destination = get.finish.selected
        case "Failed":
            vote_result = "failed"
            destination = get.compose.selected

    async with storage.write_as_project_committee_member(project_name) as wacm:
        success_message = await wacm.vote.resolve_manually(project_name, version_name, vote_result)

    return await session.redirect(
        destination,
        project_name=str(project_name),
        version_name=str(version_name),
        success=success_message,
    )


@post.typed
async def start_selected_revision(
    session: web.Committer,
    _manual_start: Literal["manual/start"],
    project_name: safe.ProjectKey,
    version_name: safe.VersionKey,
    revision: safe.RevisionNumber,
    _form: form.Empty,
) -> web.WerkzeugResponse | str:
    """
    URL: /manual/start/<project_name>/<version_name>/<revision>
    """

    async with db.session() as data:
        match await interaction.release_ready_for_vote(
            session, project_name, version_name, revision, data, manual_vote=True
        ):
            case str() as error:
                return await session.redirect(
                    get.vote.selected,
                    error=error,
                    project_name=str(project_name),
                    version_name=str(version_name),
                )
            case (release, _committee):
                pass

        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(release.safe_project_key)
            error = await wacp.release.promote_to_candidate(release.safe_key, revision, vote_manual=True)

        if error:
            return await session.redirect(
                get.vote.selected,
                error=error,
                project_name=str(project_name),
                version_name=str(version_name),
            )

        return await session.redirect(
            get.vote.selected,
            success="The manual vote process has been started.",
            project_name=str(project_name),
            version_name=str(version_name),
        )


async def _committee_label(thread_id: str) -> str | None:
    async for _mid, msg in util.thread_messages(thread_id):
        if "list_raw" in msg:
            list_raw = msg["list_raw"]
            return list_raw.split(".apache.org", 1)[0].split(".", 1)[-1]
    return None


async def _committees_check(vote_thread_url: str, vote_result_url: str) -> None:
    # The two arguments to this function are guaranteed to begin with this prefix
    # Validation was performed by the Pydantic field validator ResolveVoteForm.validate_urls
    guaranteed_prefix = "https://lists.apache.org/thread/"
    vote_thread_id = vote_thread_url.removeprefix(guaranteed_prefix)
    try:
        vote_committee_label = await _committee_label(vote_thread_id)
    except util.FetchError as e:
        raise RuntimeError(f"Failed to fetch vote thread metadata from URL {e.url}: {e!s}")
    if vote_committee_label is None:
        raise RuntimeError("Vote committee not found")

    result_thread_id = vote_result_url.removeprefix(guaranteed_prefix)
    try:
        result_committee_label = await _committee_label(result_thread_id)
    except util.FetchError as e:
        raise RuntimeError(f"Failed to fetch vote thread metadata from URL {e.url}: {e!s}")
    if result_committee_label is None:
        raise RuntimeError("Result committee not found")

    if vote_committee_label != result_committee_label:
        raise RuntimeError("Vote committee and result committee do not match")
