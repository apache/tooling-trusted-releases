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

import htpy

import atr.blueprints.get as get
import atr.db.interaction as interaction
import atr.form
import atr.htm as htm
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.shared as shared
import atr.storage as storage
import atr.tabulate as tabulate
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected(  # noqa: C901
    session: web.Committer,
    _resolve: Literal["resolve"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /resolve/<project_key>/<version_key>
    """
    asf_uid = session.uid
    full_name = session.fullname

    release = await session.release(
        project_key,
        version_key,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    if release.vote_manual:
        raise RuntimeError("This page is for tabulated votes only")

    details = None
    committee = None
    thread_id = None
    archive_url = None
    fetch_error = None

    latest_vote_task = await interaction.release_latest_vote_task(release)
    if latest_vote_task is not None:
        task_mid = interaction.task_mid_get(latest_vote_task)
        task_recipient = interaction.task_recipient_get(latest_vote_task)
        if task_mid:
            async with storage.write(session) as write:
                wagp = write.as_general_public()
                try:
                    archive_url = await wagp.cache.get_message_archive_url(task_mid, task_recipient, strict=True)
                except util.FetchError as e:
                    log.warning(f"Vote thread lookup unavailable for {project_key}/{version_key}: {e}")
                    fetch_error = _archive_lookup_error()

    if archive_url:
        thread_id = archive_url.split("/")[-1]
        if thread_id:
            try:
                committee = await tabulate.vote_committee(thread_id, release)
                details = await tabulate.vote_details(committee, thread_id, release)
            except (util.FetchError, ValueError) as e:
                log.warning(f"Automatic vote tabulation unavailable for {project_key}/{version_key}: {e}")
                fetch_error = _tabulation_error(e)
        else:
            fetch_error = "The vote thread could not yet be found."
    elif fetch_error is None:
        fetch_error = "The vote thread could not yet be found."

    pass_fail_allowed = interaction.vote_pass_fail_allowed(latest_vote_task)
    bypass_active = interaction.vote_duration_bypass()
    vote_end = interaction.vote_end_get(latest_vote_task)

    defaults = {}
    if (committee is not None) and (details is not None) and (thread_id is not None):
        defaults["email_body"] = tabulate.vote_resolution(
            committee,
            release,
            details.votes,
            details.summary,
            details.passed,
            details.outcome,
            full_name,
            asf_uid,
            thread_id,
        )
        defaults["vote_result"] = "Passed" if details.passed else "Failed"

    binding_sufficient = (
        (details is not None)
        and (details.summary["binding_votes_yes"] >= 3)
        and (details.summary["binding_votes_yes"] > details.summary["binding_votes_no"])
    )

    submit_label = "Resolve vote"
    if pass_fail_allowed or bypass_active:
        form_cls = shared.resolve.SubmitForm
    else:
        form_cls = shared.resolve.CancelSubmitForm

    pre_submit: htm.Element | None = None
    if (not binding_sufficient) and (pass_fail_allowed or bypass_active):
        icon = htpy.i(class_="bi bi-exclamation-triangle me-1")
        if details is not None:
            message = (
                "The automated tabulation did not find sufficient binding +1 votes to"
                " pass (at least 3 binding +1 votes are required, with more +1 than -1)."
                " Note that the tabulation is heuristic and may not have parsed all votes"
                " correctly."
            )
        else:
            message = (
                "The vote thread could not be tabulated, so binding vote requirements"
                " could not be verified automatically."
            )
        pre_submit = htm.div(".border.rounded.bg-warning-subtle.p-3.mb-3")[icon, message]

    resolve_form = await atr.form.render(
        model_cls=form_cls,
        action=util.as_url(post.resolve.selected, project_key=release.project.key, version_key=release.version),
        submit_label=submit_label,
        textarea_rows=24,
        defaults=defaults,
        pre_submit=pre_submit,
    )

    return await template.render(
        "resolve-tabulated.html",
        release=release,
        tabulated_votes=details.votes if (details is not None) else {},
        summary=details.summary if (details is not None) else {},
        outcome=details.outcome if (details is not None) else "",
        resolve_form=resolve_form,
        fetch_error=fetch_error,
        archive_url=archive_url,
        vote_end=vote_end,
        pass_fail_allowed=pass_fail_allowed,
        bypass_active=bypass_active,
    )


def _archive_lookup_error() -> str:
    return (
        "ATR could not look up the archived vote thread on lists.apache.org. "
        "Please review the vote manually and continue below."
    )


def _tabulation_error(error: util.FetchError | ValueError) -> str:
    if isinstance(error, util.FetchError):
        return (
            "ATR could not retrieve the archived vote thread from lists.apache.org, "
            "so automatic vote tabulation is unavailable. Please review the vote manually "
            "and continue below."
        )
    return (
        "ATR could not tabulate the archived vote thread automatically. "
        "Please review the vote manually and continue below."
    )
