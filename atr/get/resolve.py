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
import atr.user as user
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
    if release.effective_vote_mode == sql.VoteMode.MANUAL:
        raise RuntimeError("This page is for tabulated votes only")

    details = None
    committee = None
    thread_id = None
    archive_url = None
    fetch_error = None

    latest_vote_task = await interaction.release_current_vote_task(release)
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
                excluded_message_ids = None
                if (release.effective_vote_mode == sql.VoteMode.TRUSTED) and (release.current_vote_seq is not None):
                    excluded_message_ids = await interaction.ballot_receipt_message_ids(
                        release.key, release.current_vote_seq
                    )
                details = await tabulate.vote_details(
                    committee, thread_id, release, excluded_message_ids=excluded_message_ids
                )
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
    is_trusted_mode = release.effective_vote_mode == sql.VoteMode.TRUSTED
    trusted_ballot_count = 0
    trusted_summary = None
    trusted_passed = False
    if is_trusted_mode and (release.current_vote_seq is not None):
        trusted_ballots = await interaction.ballots_for_resolution(release.key, release.current_vote_seq)
        trusted_ballot_count = len(trusted_ballots)
        trusted_summary = await interaction.trusted_ballot_summary(release, trusted_ballots)
        trusted_passed = tabulate.binding_vote_passes(
            trusted_summary.binding_votes_yes, trusted_summary.binding_votes_no
        )

    defaults: dict[str, object] = {
        "vote_mode": release.effective_vote_mode,
        "vote_seq": release.current_vote_seq,
    }
    if trusted_summary is not None:
        vote_round: int | None = None
        if (release.committee is not None) and release.committee.is_podling:
            vote_round = 2 if (release.podling_thread_id is not None) else 1
        binding_label, non_binding_label = user.binding_terminology(vote_round)
        defaults["email_body"] = tabulate.trusted_vote_resolution(
            release,
            trusted_summary,
            trusted_passed,
            full_name,
            asf_uid,
            thread_id,
            binding_label,
            non_binding_label,
        )
    elif (committee is not None) and (details is not None) and (thread_id is not None):
        vote_round = None
        if (release.committee is not None) and release.committee.is_podling:
            vote_round = 2 if (release.podling_thread_id is not None) else 1
        binding_label, non_binding_label = user.binding_terminology(vote_round)
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
            binding_label,
            non_binding_label,
        )
        defaults["vote_result"] = "Passed" if details.passed else "Failed"

    if is_trusted_mode:
        binding_sufficient = trusted_passed
    else:
        binding_sufficient = (details is not None) and tabulate.binding_vote_passes(
            details.summary["binding_votes_yes"], details.summary["binding_votes_no"]
        )

    vote_round = None
    if (release.committee is not None) and release.committee.is_podling:
        vote_round = 2 if (release.podling_thread_id is not None) else 1
    binding_label, non_binding_label = user.binding_terminology(vote_round)

    submit_label = "Resolve vote"
    trusted_duration_blocks_result = is_trusted_mode and (vote_end is not None) and (not pass_fail_allowed)
    if trusted_duration_blocks_result and (not bypass_active):
        form_cls = shared.resolve.CancelSubmitForm
    elif is_trusted_mode:
        form_cls = shared.resolve.SubmitForm
        vote_result_choices = [("Failed", "Failed"), ("Cancelled", "Cancelled")]
        if binding_sufficient or bypass_active:
            vote_result_choices.insert(0, ("Passed", "Passed"))
        defaults["vote_result"] = vote_result_choices
    elif pass_fail_allowed or bypass_active:
        form_cls = shared.resolve.SubmitForm
    else:
        form_cls = shared.resolve.CancelSubmitForm

    pre_submit: htm.Element | None = None
    if (not binding_sufficient) and (pass_fail_allowed or bypass_active):
        icon = htpy.i(class_="bi bi-exclamation-triangle me-1")
        if is_trusted_mode:
            message = (
                f"The trusted ballot record does not contain sufficient {binding_label.lower()} +1 votes to"
                f" pass (at least 3 {binding_label.lower()} +1 votes are required, with more +1 than -1)."
            )
        elif details is not None:
            message = (
                f"The automated tabulation did not find sufficient {binding_label.lower()} +1 votes to"
                f" pass (at least 3 {binding_label.lower()} +1 votes are required, with more +1 than -1)."
                " Note that the tabulation is heuristic and may not have parsed all votes"
                " correctly."
            )
        else:
            message = (
                f"The vote thread could not be tabulated, so {binding_label.lower()} vote requirements"
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
        trusted_ballot_count=trusted_ballot_count,
        trusted_summary=trusted_summary,
        trusted_mode=is_trusted_mode,
        vote_end=vote_end,
        pass_fail_allowed=pass_fail_allowed,
        bypass_active=bypass_active,
        binding_label=binding_label,
        non_binding_label=non_binding_label,
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
