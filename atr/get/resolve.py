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
import atr.constants as constants
import atr.construct as construct
import atr.db.interaction as interaction
import atr.form as form
import atr.htm as htm
import atr.log as log
import atr.models as models
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.storage as storage
import atr.strings as strings
import atr.tabulate as tabulate
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web


@dataclasses.dataclass(frozen=True, kw_only=True)
class CountedBallotRow:
    asf_eid: str | None = None
    cast_at: str
    choice: str
    comment: str = ""
    confirmed_by: str | None = None
    is_binding: bool
    receipt_message_id: str | None = None
    receipt_url: str | None
    source: Literal["atr", "email"]
    status_label: str
    voter_asf_uid: str | None
    voter_email: str | None = None
    voter_fullname: str


@dataclasses.dataclass(frozen=True)
class EmailContextRow:
    asf_eid: str
    asf_uid_or_email: str
    confirmed: bool
    confirmed_by: str | None
    link_url: str
    name: str
    quotation: str
    status_label: str
    vote: str


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
async def selected(
    session: web.Committer,
    _resolve: Literal["resolve"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /resolve/<project_key>/<version_key>
    """
    release = await session.release(
        project_key,
        version_key,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    latest_vote_task = await interaction.release_current_vote_task(release)
    vote_recipient, vote_task_error, mail_warnings = _vote_task_mail(latest_vote_task)
    archive_url, thread_id, fetch_error = await _vote_thread(session, release, latest_vote_task, vote_recipient)
    committee = None
    details = None
    if thread_id:
        committee, details, fetch_error = await _tabulate_thread(release, thread_id)
    email_context_votes: list[EmailContextRow] = []
    email_context_summary: list[VoteCountRow] = []
    if details is not None:
        email_context_votes = _email_context_rows(details.votes)
        email_context_summary = _email_context_summary_rows(details.votes)

    vote_round = interaction.vote_round_for_release(release)
    binding_label, non_binding_label = user.binding_terminology(vote_round)
    binding_label_inline = user.binding_label_inline(binding_label)
    vote_seq = release.current_vote_seq
    counted_details: list[interaction.CountedVoteDetail] = []
    counted_summary = None
    counted_ballots: list[CountedBallotRow] = []
    counted_outcome = ""
    atr_tally = ""
    binding_sufficient = False
    if vote_seq is not None:
        counted_details, counted_summary = await interaction.counted_vote_details_and_summary(release, vote_seq)
        counted_ballots = _counted_ballot_rows(counted_details, vote_recipient, expedited=release.expedited)
        counted_outcome = _counted_outcome(counted_summary, binding_label_inline)
        binding_sufficient = tabulate.binding_vote_passes(
            counted_summary.binding_votes_yes, counted_summary.binding_votes_no
        )
        atr_tally = tabulate.vote_tally_block(
            counted_summary,
            binding_label,
            non_binding_label,
            thread_id=thread_id,
            podling_thread_id=release.podling_thread_id,
            ballots=counted_details,
        )

    resolved_template = await construct.resolve_template(release.project, "finish_vote")
    email_body = construct.finish_vote_body(
        resolved_template.body,
        {
            "ATR_TALLY": atr_tally,
            "COMMITTEE": _committee_name(release, committee),
            "MAJOR_VERSION": construct.major_version(release.version),
            "OUTCOME": "{{OUTCOME}}",
            "PROJECT_NAME": release.project.short_display_name,
            "VERSION": release.version,
            "YOUR_ASF_ID": session.uid,
            "YOUR_FULL_NAME": session.fullname,
        },
    )

    is_security_team = constants.SECURITY_COMMITTEE_KEY in session.member_committees
    bypass_active = interaction.vote_resolution_bypass(release, session.uid) or is_security_team
    is_binding, _binding_committee = (
        await user.is_binding_for_release(release.committee, session.uid, vote_round)
        if (release.committee is not None)
        else (False, "")
    )
    can_confirm = _can_confirm(session, release, vote_round, is_binding)
    can_resolve = _can_resolve(session, release, is_binding, is_security_team)
    second_round_startable = await _second_round_startable(release, can_resolve)
    pass_fail_allowed = interaction.vote_pass_fail_allowed(latest_vote_task)
    vote_end = interaction.vote_end_get(latest_vote_task)
    cancel_only, early_completion = _resolution_timing(
        pass_fail_allowed, bypass_active, binding_sufficient, vote_end, is_binding
    )
    resolve_form = None
    if can_resolve:
        pre_submit = _resolution_notices(
            binding_label_inline,
            bypass_active,
            binding_sufficient,
            pass_fail_allowed,
            auto_publish=_publishes_on_pass(latest_vote_task, vote_round, is_security_team, cancel_only),
        )
        resolve_form = await _resolution_form(
            session,
            release,
            latest_vote_task,
            vote_recipient,
            email_body,
            pre_submit,
            cancel_only=cancel_only,
            early_completion=early_completion,
            second_round_startable=second_round_startable,
            allow_pass=binding_sufficient or bypass_active,
            is_security_team=is_security_team,
        )

    commit_hash = render.commit_hash(release)
    return await template.render(
        "resolve-tabulated.html",
        release=release,
        commit_hash_html=str(commit_hash) if (commit_hash is not None) else "",
        vote_task_error=vote_task_error,
        mail_warnings=mail_warnings,
        template_warning=resolved_template.warning,
        has_vote_serial=(vote_seq is not None),
        vote_seq=vote_seq,
        counted_ballots=counted_ballots,
        counted_summary=counted_summary,
        counted_outcome=counted_outcome,
        binding_label=binding_label,
        binding_label_inline=binding_label_inline,
        non_binding_label=non_binding_label,
        archive_url=archive_url,
        fetch_error=fetch_error,
        email_context_votes=email_context_votes,
        email_context_summary=email_context_summary,
        can_confirm=can_confirm,
        can_resolve=can_resolve,
        second_round_startable=second_round_startable,
        resolve_form=resolve_form,
        cancel_only=cancel_only,
        early_completion=early_completion,
        pass_fail_allowed=pass_fail_allowed,
        bypass_active=bypass_active,
        vote_end=vote_end,
    )


def _archive_lookup_error() -> str:
    return (
        "ATR could not look up the archived vote thread on lists.apache.org. "
        "Please review the vote manually and continue below."
    )


def _can_confirm(session: web.Committer, release: sql.Release, vote_round: int | None, is_binding: bool) -> bool:
    voting_committee_key = (
        "incubator" if (vote_round == 2) else (release.committee.key if (release.committee is not None) else None)
    )
    is_voting_committee_member = is_binding or (
        (voting_committee_key is not None) and (voting_committee_key in session.member_committees)
    )
    return (not release.expedited) and (release.current_vote_seq is not None) and is_voting_committee_member


def _can_resolve(session: web.Committer, release: sql.Release, is_binding: bool, is_security_team: bool) -> bool:
    committee = release.committee
    is_committee_member = (committee is not None) and (
        (committee.key in session.member_committees) or user.is_committee_member(committee, session.uid)
    )
    is_release_manager = (
        (committee is not None) and (not release.expedited) and user.is_release_manager(committee, session.uid)
    )
    return is_committee_member or is_release_manager or is_binding or is_security_team


def _committee_name(release: sql.Release, committee: sql.Committee | None) -> str:
    if release.podling_thread_id:
        return "Incubator"
    if release.committee is not None:
        return release.committee.display_name
    if committee is not None:
        return committee.display_name
    return release.project.key


def _counted_ballot_rows(
    counted_details: list[interaction.CountedVoteDetail],
    vote_recipient: str | None,
    *,
    expedited: bool = False,
) -> list[CountedBallotRow]:
    rows: list[CountedBallotRow] = []
    for detail in counted_details:
        receipt_url = _receipt_or_email_url(detail, vote_recipient, expedited=expedited)
        rows.append(
            CountedBallotRow(
                asf_eid=detail.asf_eid,
                cast_at=format_utc(detail.cast_at),
                choice=detail.choice.value,
                comment=detail.comment,
                confirmed_by=detail.confirmed_by,
                is_binding=detail.is_binding,
                receipt_message_id=detail.receipt_message_id,
                receipt_url=receipt_url,
                source=detail.source,
                status_label=detail.status_label,
                voter_asf_uid=detail.voter_asf_uid,
                voter_email=detail.voter_email,
                voter_fullname=detail.voter_fullname,
            )
        )
    return rows


def _counted_outcome(summary: interaction.CountedVoteSummary, binding_label_inline: str) -> str:
    if tabulate.binding_vote_passes(summary.binding_votes_yes, summary.binding_votes_no):
        return f"The official tally satisfies the {binding_label_inline} vote threshold for passing."
    return f"The official tally does not satisfy the {binding_label_inline} vote threshold for passing."


def _email_context_rows(tabulated_votes: dict[str, models.tabulate.VoteEmail]) -> list[EmailContextRow]:
    return [
        EmailContextRow(
            asf_eid=vote_detail.asf_eid,
            asf_uid_or_email=vote_detail.asf_uid_or_email,
            confirmed=vote_detail.confirmed,
            confirmed_by=vote_detail.confirmed_by,
            link_url=f"https://lists.apache.org/thread/{vote_detail.asf_eid}",
            name=vote_detail.name,
            quotation=vote_detail.quotation,
            status_label=_email_context_status_label(vote_detail.status),
            vote=vote_detail.fraction or _email_context_vote_label(vote_detail.vote),
        )
        for vote_detail in tabulated_votes.values()
    ]


def _email_context_status_label(status: models.tabulate.VoteStatus) -> str:
    match status:
        case models.tabulate.VoteStatus.BINDING:
            return "Email from PMC member"
        case models.tabulate.VoteStatus.FRACTIONAL:
            return "Fractional email from PMC member"
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


def _publishes_on_pass(
    latest_vote_task: sql.Task | None, vote_round: int | None, is_security_team: bool, cancel_only: bool
) -> bool:
    return (
        (not cancel_only)
        and (vote_round != 1)
        and (
            is_security_team
            or (
                (latest_vote_task is not None)
                and bool(latest_vote_task.task_args.get("automatic_publish_when_resolved"))
            )
        )
    )


def _receipt_or_email_url(
    detail: interaction.CountedVoteDetail,
    vote_recipient: str | None,
    *,
    expedited: bool,
) -> str | None:
    if detail.source == "email":
        return f"https://lists.apache.org/thread/{detail.asf_eid}" if detail.asf_eid else None
    if (detail.receipt_message_id is not None) and (vote_recipient is not None) and (not expedited):
        return shared.vote.message_id_source_archive_url(detail.receipt_message_id, vote_recipient)
    return None


def _recipient_field(
    session: web.Committer, release: sql.Release, task: sql.Task | None, vote_recipient: str | None
) -> htm.Element:
    if release.expedited:
        return htm.div(".form-text.text-muted")[
            "This is an expedited release, so the result is sent only to the private list"
            " and additional recipients cannot be added."
        ]
    permitted = (
        util.permitted_voting_recipients(session.uid, release.committee.key, project=release.project)
        if (release.committee is not None)
        else []
    )
    task_args = task.task_args if (task is not None) else {}
    original_cc = task_args.get("email_cc", [])
    original_bcc = task_args.get("email_bcc", [])
    addresses = dict.fromkeys([*permitted, *original_cc, *original_bcc])
    options = [address for address in addresses if (address != vote_recipient)]
    return htm.div[
        render.html_recipients_cc_bcc_table(options, locked_cc=set(original_cc), locked_bcc=set(original_bcc)),
        htm.div(".form-text.text-muted.mt-1")[
            "The original recipients are included and cannot be removed. Tick others to add them."
        ],
    ]


async def _resolution_form(
    session: web.Committer,
    release: sql.Release,
    latest_vote_task: sql.Task | None,
    vote_recipient: str | None,
    email_body: str,
    pre_submit: htm.Element | None,
    *,
    cancel_only: bool,
    early_completion: bool,
    second_round_startable: bool,
    allow_pass: bool,
    is_security_team: bool,
) -> htm.Element:
    defaults: dict[str, object] = {
        "vote_seq": release.current_vote_seq,
        "result_email_to": vote_recipient or "the original vote thread recipients",
    }
    custom: dict[str, htm.Element | htm.VoidElement] = {
        "email_cc": _recipient_field(session, release, latest_vote_task, vote_recipient)
    }
    skip = ["email_bcc"]
    if cancel_only:
        form_cls = shared.resolve.CancelSubmitForm
        submit_label = "Cancel vote"
        submit_classes = "btn-danger"
        defaults["email_body"] = email_body.replace("{{OUTCOME}}", "was cancelled")
        defaults["vote_result"] = "Cancelled"
    else:
        form_cls = shared.resolve.SubmitForm
        submit_label = strings.RESOLVE_VOTE_BUTTON
        submit_classes = "btn-primary"
        defaults["email_body"] = email_body
        defaults["result_subject"] = tabulate.vote_result_subject(release, "{{OUTCOME}}")
        defaults["vote_result"] = _result_choices(early_completion, allow_pass)
        if second_round_startable:
            custom["notify_when_finished"] = htm.div[
                htpy.input(
                    type="checkbox",
                    name="notify_when_finished",
                    id="notify_when_finished",
                    value="on",
                    class_="form-check-input",
                ),
                htm.div(".form-text.text-muted.mt-1")[
                    f"If enabled, ATR will email {session.uid}@apache.org when the second round vote finishes."
                ],
            ]
        else:
            skip.append("notify_when_finished")
        if not early_completion:
            skip.append("early_reason")
        if not is_security_team:
            skip.append("security_override")
    return await form.render(
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


def _resolution_notices(
    binding_label_inline: str,
    bypass_active: bool,
    binding_sufficient: bool,
    pass_fail_allowed: bool,
    *,
    auto_publish: bool,
) -> htm.Element | None:
    notices: list[htm.Element] = []
    if (not binding_sufficient) and (pass_fail_allowed or bypass_active):
        message = (
            f"The official tally does not contain sufficient {binding_label_inline} +1 votes to pass "
            f"(at least 3 {binding_label_inline} +1 votes are required, with more +1 than -1)."
        )
        if bypass_active:
            message += " The vote resolution bypass allows this vote to be marked as passed anyway."
        notices.append(
            htm.div(".border.rounded.bg-warning-subtle.p-3.mb-3")[htm.i(".bi.bi-exclamation-triangle.me-1"), message]
        )
    if auto_publish:
        notices.append(
            htm.div(".border.rounded.bg-info-subtle.p-3.mb-3")[
                htm.i(".bi.bi-info-circle.me-1"),
                "If this vote is marked as passed, the release files will be published to SVN automatically.",
            ]
        )
    return htm.div[notices] if notices else None


def _resolution_timing(
    pass_fail_allowed: bool,
    bypass_active: bool,
    binding_sufficient: bool,
    vote_end: datetime.datetime | None,
    is_binding: bool,
) -> tuple[bool, bool]:
    duration_blocks_result = (not pass_fail_allowed) and (not bypass_active)
    early_completion = duration_blocks_result and binding_sufficient and (vote_end is not None) and is_binding
    cancel_only = duration_blocks_result and (not early_completion)
    return cancel_only, early_completion


def _result_choices(early_completion: bool, allow_pass: bool) -> list[tuple[str, str]]:
    choices = []
    if allow_pass:
        choices.append(("Passed", "Passed"))
    if not early_completion:
        choices.append(("Failed", "Failed"))
    choices.append(("Cancelled", "Cancelled"))
    return choices


async def _second_round_startable(release: sql.Release, can_resolve: bool) -> bool:
    return (
        can_resolve
        and (release.committee is not None)
        and release.committee.is_podling
        and (release.podling_thread_id is None)
        and (release.current_vote_seq is not None)
        and (await interaction.release_early_second_round_task(release) is None)
    )


async def _tabulate_thread(
    release: sql.Release, thread_id: str
) -> tuple[sql.Committee | None, models.tabulate.VoteDetails | None, str | None]:
    committee = None
    try:
        committee = await tabulate.vote_committee(thread_id, release)
        excluded_message_ids = (
            await interaction.ballot_receipt_message_ids(release.key, release.current_vote_seq)
            if (release.current_vote_seq is not None)
            else None
        )
        details = await tabulate.vote_details(committee, thread_id, release, excluded_message_ids=excluded_message_ids)
    except (util.FetchError, ValueError) as e:
        log.warning(f"Automatic vote tabulation unavailable for {release.key}: {e}")
        return committee, None, _tabulation_error(e)
    return committee, details, None


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


def _vote_task_mail(latest_vote_task: sql.Task | None) -> tuple[str | None, str | None, list[str]]:
    if latest_vote_task is None:
        return None, None, []
    vote_task_error = None
    if latest_vote_task.status == sql.TaskStatus.FAILED:
        vote_task_error = latest_vote_task.error or "unknown error"
    result = latest_vote_task.result
    mail_warnings = result.mail_send_warnings if isinstance(result, results.VoteInitiate) else []
    return interaction.task_recipient_get(latest_vote_task), vote_task_error, mail_warnings


async def _vote_thread(
    session: web.Committer, release: sql.Release, latest_vote_task: sql.Task | None, task_recipient: str | None
) -> tuple[str | None, str | None, str | None]:
    if release.expedited:
        return None, None, None
    archive_url = None
    if (latest_vote_task is not None) and (task_mid := interaction.task_mid_get(latest_vote_task)):
        async with storage.write(session) as write:
            wagp = write.as_general_public()
            try:
                archive_url = await wagp.cache.get_message_archive_url(task_mid, task_recipient, strict=True)
            except util.FetchError as e:
                log.warning(f"Vote thread lookup unavailable for {release.key}: {e}")
                return None, None, _archive_lookup_error()
    thread_id = archive_url.split("/")[-1] if archive_url else None
    return archive_url, thread_id, None if thread_id else "The vote thread could not yet be found."
