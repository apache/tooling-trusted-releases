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

import dataclasses
import datetime
from typing import Literal

import htpy

import atr.blueprints.get as get
import atr.construct as construct
import atr.db.interaction as interaction
import atr.form
import atr.htm as htm
import atr.log as log
import atr.models as models
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.storage as storage
import atr.tabulate as tabulate
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web


@dataclasses.dataclass(frozen=True)
class EmailContextRow:
    asf_uid_or_email: str
    link_url: str
    name: str
    quotation: str
    status_label: str
    vote: str


@dataclasses.dataclass(frozen=True, kw_only=True)
class TrustedBallotRow:
    cast_at: str
    choice: str
    is_binding: bool
    is_carried: bool = False
    receipt_message_id: str
    receipt_url: str | None
    status_label: str
    voter_asf_uid: str
    voter_fullname: str


@dataclasses.dataclass(frozen=True)
class VoteCountRow:
    abstain: int
    label: str
    no: int
    total: int
    yes: int


def format_utc(timestamp: datetime.datetime) -> str:
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.UTC)
    else:
        timestamp = timestamp.astimezone(datetime.UTC)
    return timestamp.strftime("%Y-%m-%d %H:%M UTC")


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
    vote_round = interaction.trusted_vote_round(release)
    binding_label, non_binding_label = user.binding_terminology(vote_round)
    vote_seq = release.current_vote_seq
    trusted_ballot_rows: list[TrustedBallotRow] = []
    trusted_has_vote_serial = vote_seq is not None
    trusted_outcome = ""
    trusted_summary = None
    trusted_passed = False
    vote_recipient = interaction.task_recipient_get(latest_vote_task) if (latest_vote_task is not None) else None
    if is_trusted_mode and (vote_seq is not None):
        ballots = await interaction.effective_trusted_ballots(release, vote_seq)
        trusted_ballot_details, trusted_summary = await interaction.trusted_ballot_details_from_ballots(
            release,
            ballots,
            vote_round,
        )
        round_one_recipient = await interaction.previous_round_one_recipient(release, vote_seq)
        trusted_ballot_rows = _trusted_ballot_rows(trusted_ballot_details, vote_recipient, round_one_recipient)
        trusted_passed = tabulate.binding_vote_passes(
            trusted_summary.binding_votes_yes, trusted_summary.binding_votes_no
        )
    email_context_votes = _email_context_rows(details.votes) if (details is not None) else []
    email_context_summary = _email_context_summary_rows(details.votes) if (details is not None) else []

    defaults: dict[str, object] = {
        "vote_mode": release.effective_vote_mode,
        "vote_seq": vote_seq,
    }
    if release.podling_thread_id:
        committee_name = "Incubator"
    elif release.committee is not None:
        committee_name = release.committee.display_name
    elif committee is not None:
        committee_name = committee.display_name
    else:
        committee_name = release.project.key

    # ATR_TALLY is final here - tabulation has been attempted. OUTCOME stays as
    # the variable since the user picks via the form; POST substitutes it
    atr_tally = ""
    outcome = "{{OUTCOME}}"
    if trusted_summary is not None:
        atr_tally = tabulate.trusted_tally_block(
            trusted_summary,
            binding_label,
            non_binding_label,
            thread_id=thread_id,
            podling_thread_id=release.podling_thread_id,
        )
        outcome = "passed" if trusted_passed else "failed"
    elif (not is_trusted_mode) and (details is not None) and (thread_id is not None):
        atr_tally = tabulate.email_tally_block(
            details.votes,
            details.summary,
            thread_id,
            binding_label,
            non_binding_label,
            podling_thread_id=release.podling_thread_id,
        )
        outcome = "passed" if details.passed else "failed"
        defaults["vote_result"] = "Passed" if details.passed else "Failed"

    defaults["email_body"] = construct.finish_vote_body(
        release.project.policy_finish_vote_template,
        {
            "ATR_TALLY": atr_tally,
            "COMMITTEE": committee_name,
            "OUTCOME": outcome,
            "PROJECT_NAME": release.project.short_display_name,
            "VERSION": release.version,
            "YOUR_ASF_ID": asf_uid,
            "YOUR_FULL_NAME": full_name,
        },
    )

    if is_trusted_mode:
        binding_sufficient = trusted_passed
    else:
        binding_sufficient = (details is not None) and tabulate.binding_vote_passes(
            details.summary["binding_votes_yes"], details.summary["binding_votes_no"]
        )

    if trusted_summary is not None:
        trusted_outcome = _trusted_outcome(trusted_summary, binding_label)

    cancel_only = False
    submit_classes = "btn-primary"
    submit_label = "Resolve vote"
    duration_blocks_result = (not pass_fail_allowed) and (not bypass_active)
    if duration_blocks_result:
        cancel_only = True
        defaults["vote_result"] = "Cancelled"
        form_cls = shared.resolve.CancelSubmitForm
        submit_classes = "btn-danger"
        submit_label = "Cancel vote"
    elif is_trusted_mode:
        form_cls = shared.resolve.SubmitForm
        vote_result_choices = [("Failed", "Failed"), ("Cancelled", "Cancelled")]
        if binding_sufficient or bypass_active:
            vote_result_choices.insert(0, ("Passed", "Passed"))
        defaults["vote_result"] = vote_result_choices
    else:
        form_cls = shared.resolve.SubmitForm

    is_podling_first_round = (
        is_trusted_mode
        and (release.committee is not None)
        and release.committee.is_podling
        and (release.podling_thread_id is None)
    )
    custom: dict[str, htm.Element | htm.VoidElement] = {}
    skip: list[str] = []
    if (form_cls is shared.resolve.SubmitForm) and is_podling_first_round:
        defaults["automatic_resolve_when_finished"] = True
        custom["automatic_resolve_when_finished"] = htm.div[
            htpy.input(
                type="checkbox",
                name="automatic_resolve_when_finished",
                id="automatic_resolve_when_finished",
                value="on",
                checked=True,
                class_="form-check-input",
            ),
            htm.div(".form-text.text-muted.mt-1")[
                "If enabled, ATR will resolve the second round vote automatically, "
                "using only ATR ballots, when its voting period ends.",
            ],
        ]
        custom["notify_when_finished"] = htm.div[
            htpy.input(
                type="checkbox",
                name="notify_when_finished",
                id="notify_when_finished",
                value="on",
                class_="form-check-input",
            ),
            htm.div(".form-text.text-muted.mt-1")[
                f"If enabled, ATR will send an email to {session.uid}@apache.org when the second round vote finishes.",
            ],
        ]
    else:
        skip.append("automatic_resolve_when_finished")
        skip.append("notify_when_finished")

    # Show the To for reference and offer a grid
    # to add further cc/bcc with inherited recipients locked.
    defaults["result_email_to"] = vote_recipient or "the original vote thread recipients"
    permitted_recipients = (
        util.permitted_voting_recipients(session.uid, release.committee.key, project=release.project)
        if (release.committee is not None)
        else []
    )
    original_cc = list(latest_vote_task.task_args.get("email_cc", [])) if (latest_vote_task is not None) else []
    original_bcc = list(latest_vote_task.task_args.get("email_bcc", [])) if (latest_vote_task is not None) else []
    # The To is shown separately and always included, so don't offer it as an
    # addable cc/bcc - that keeps the grid free of duplicate choices.
    grid_options = [address for address in permitted_recipients if (address != vote_recipient)]
    for address in [*original_cc, *original_bcc]:
        if address not in grid_options:
            grid_options.append(address)
    custom["email_cc"] = htm.div[
        render.html_recipients_cc_bcc_table(
            grid_options,
            locked_cc=set(original_cc),
            locked_bcc=set(original_bcc),
        ),
        htm.div(".form-text.text-muted.mt-1")[
            "The original recipients are included and cannot be removed. Tick others to add them.",
        ],
    ]
    skip.append("email_bcc")

    if form_cls is shared.resolve.SubmitForm:
        defaults["result_subject"] = tabulate.vote_result_subject(release, "{{OUTCOME}}")

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
        submit_classes=submit_classes,
        submit_label=submit_label,
        textarea_rows=24,
        defaults=defaults,
        custom=custom,
        skip=skip,
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
        cancel_only=cancel_only,
        email_context_summary=email_context_summary,
        email_context_votes=email_context_votes,
        trusted_ballots=trusted_ballot_rows,
        trusted_has_vote_serial=trusted_has_vote_serial,
        trusted_outcome=trusted_outcome,
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


def _email_context_rows(tabulated_votes: dict[str, models.tabulate.VoteEmail]) -> list[EmailContextRow]:
    return [
        EmailContextRow(
            asf_uid_or_email=vote_detail.asf_uid_or_email,
            link_url=f"https://lists.apache.org/thread/{vote_detail.asf_eid}",
            name=vote_detail.name,
            quotation=vote_detail.quotation,
            status_label=_email_context_status_label(vote_detail.status),
            vote=_email_context_vote_label(vote_detail.vote),
        )
        for vote_detail in tabulated_votes.values()
    ]


def _email_context_status_label(status: models.tabulate.VoteStatus) -> str:
    match status:
        case models.tabulate.VoteStatus.BINDING:
            return "Email from PMC member"
        case models.tabulate.VoteStatus.COMMITTER:
            return "Email from committer"
        case models.tabulate.VoteStatus.CONTRIBUTOR:
            return "Email from contributor"
        case models.tabulate.VoteStatus.UNKNOWN:
            return "Unknown email"


def _email_context_summary_rows(tabulated_votes: dict[str, models.tabulate.VoteEmail]) -> list[VoteCountRow]:
    counts = {status: [0, 0, 0, 0] for status in models.tabulate.VoteStatus}
    for vote_detail in tabulated_votes.values():
        count = counts[vote_detail.status]
        count[3] += 1
        match vote_detail.vote:
            case models.tabulate.Vote.YES:
                count[0] += 1
            case models.tabulate.Vote.NO:
                count[1] += 1
            case models.tabulate.Vote.ABSTAIN:
                count[2] += 1
            case models.tabulate.Vote.UNKNOWN:
                pass
    return [
        VoteCountRow(
            abstain=count[2],
            label=_email_context_status_label(status),
            no=count[1],
            total=count[3],
            yes=count[0],
        )
        for status, count in counts.items()
        if count[3] > 0
    ]


def _email_context_vote_label(vote: models.tabulate.Vote) -> str:
    match vote:
        case models.tabulate.Vote.YES:
            return "+1"
        case models.tabulate.Vote.NO:
            return "-1"
        case models.tabulate.Vote.ABSTAIN:
            return "0"
        case models.tabulate.Vote.UNKNOWN:
            return "?"


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


def _trusted_ballot_rows(
    trusted_ballot_details: list[interaction.TrustedBallotDetail],
    vote_recipient: str | None,
    round_one_recipient: str | None = None,
) -> list[TrustedBallotRow]:
    rows: list[TrustedBallotRow] = []
    for detail in trusted_ballot_details:
        receipt_url = None
        recipient_for_url = round_one_recipient if detail.is_carried else vote_recipient
        if recipient_for_url is not None:
            receipt_url = shared.vote.message_id_source_archive_url(detail.receipt_message_id, recipient_for_url)
        rows.append(
            TrustedBallotRow(
                cast_at=format_utc(detail.cast_at),
                choice=detail.choice.value,
                is_binding=detail.is_binding,
                is_carried=detail.is_carried,
                receipt_message_id=detail.receipt_message_id,
                receipt_url=receipt_url,
                status_label=detail.status_label,
                voter_asf_uid=detail.voter_asf_uid,
                voter_fullname=detail.voter_fullname,
            )
        )
    return rows


def _trusted_outcome(summary: interaction.TrustedVoteSummary, binding_label: str) -> str:
    if tabulate.binding_vote_passes(summary.binding_votes_yes, summary.binding_votes_no):
        return f"The ATR ballot record satisfies the {binding_label.lower()} vote threshold for passing."
    return f"The ATR ballot record does not satisfy the {binding_label.lower()} vote threshold for passing."
