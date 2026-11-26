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

import datetime
import enum
import urllib.parse
from typing import TYPE_CHECKING, Any, Literal

import htpy

import atr.attestable as attestable
import atr.blueprints.get as get
import atr.config as config
import atr.constants as constants
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.form as form
import atr.get.checklist as checklist
import atr.get.download as download
import atr.get.keys as keys
import atr.get.resolve as resolve
import atr.get.root as root
import atr.htm as htm
import atr.mapping as mapping
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.storage as storage
import atr.strings as strings
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    import atr.get.checks as checks


class UserCategory(enum.StrEnum):
    COMMITTER = "Committer"
    COMMITTER_RM = "Committer (Release Manager)"
    PMC_MEMBER = "PMC Member"
    PMC_MEMBER_RM = "PMC Member (Release Manager)"
    UNAUTHENTICATED = "Unauthenticated"


async def category_and_release(
    session: web.Committer | None, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> tuple[UserCategory, sql.Release, sql.Task | None]:
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).demand(errors.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404))

        if release.committee is None:
            raise ValueError(errors.RELEASE_NO_COMMITTEE)

        latest_vote_task = await interaction.release_current_vote_task(release, data)

    if session is None:
        return UserCategory.UNAUTHENTICATED, release, latest_vote_task

    # This keeps the member and release manager checks separate because the categories below need both
    is_pmc_member = user.is_committee_member(release.committee, session.uid)
    is_release_manager = (not release.expedited) and user.is_release_manager(release.committee, session.uid)

    if is_pmc_member and is_release_manager:
        user_category = UserCategory.PMC_MEMBER_RM
    elif is_pmc_member:
        user_category = UserCategory.PMC_MEMBER
    elif is_release_manager:
        user_category = UserCategory.COMMITTER_RM
    else:
        user_category = UserCategory.COMMITTER

    return user_category, release, latest_vote_task


async def render_options_page(
    session: web.Committer | None,
    release: sql.Release,
    user_category: UserCategory,
    latest_vote_task: sql.Task | None,
) -> str:
    """Render the vote options page for a release candidate."""
    import atr.get.checks as checks

    show_resolve_section = user_category in (
        UserCategory.UNAUTHENTICATED,
        UserCategory.COMMITTER_RM,
        UserCategory.PMC_MEMBER,
        UserCategory.PMC_MEMBER_RM,
    )
    if (session is not None) and (constants.SECURITY_COMMITTEE_KEY in session.member_committees):
        show_resolve_section = True

    file_totals = await checks.get_file_totals(release, session)
    archive_url = await _get_archive_url(release, session, latest_vote_task)
    early_task = await _early_second_round_task(release)
    overlap_rounds = await _overlap_vote_rounds(release, session, early_task)

    page = htm.Block()
    workflow_upload_note = await _workflow_upload_note(release, signed_in=session is not None)
    _render_header(
        page, release, show_resolve_section, latest_vote_task, workflow_upload_note, early_task, overlap_rounds
    )
    if session is not None:
        _render_vote_task_warnings(page, latest_vote_task)
    _render_section_download(page, release, session, user_category)
    _render_section_checks(page, release, file_totals)
    await _render_section_vote(
        page, release, session, user_category, archive_url, latest_vote_task, early_task, overlap_rounds
    )
    if show_resolve_section:
        _render_section_resolve(page, release, user_category, 5 if (len(overlap_rounds) == 2) else 4)

    return await template.blank(
        f"Vote on {release.project.short_display_name} {release.version}",
        content=page.collect(),
        javascripts=["clipboard-copy"],
    )


async def render_vote_closed_page(release: sql.Release) -> str:
    """Explain that the vote is not open."""
    page = htm.Block()

    page.h1[
        "Vote closed for ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]

    phase_messages = {
        sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT: (
            "This release is still being composed and voting has not yet started."
        ),
        sql.ReleasePhase.RELEASE_PREVIEW: ("Voting has concluded and the release is now being finalised."),
        sql.ReleasePhase.RELEASE: ("This release has been completed and is now available for distribution."),
    }

    message = phase_messages.get(release.phase, "The vote for this release is no longer open.")

    page.div(".alert.alert-info.d-flex.align-items-center", role="alert")[
        htpy.i(".bi.bi-info-circle.me-2"),
        htm.div[message],
    ]

    if (commit_hash := render.commit_hash(release)) is not None:
        page.append(commit_hash)

    page.p["If you are an ASF committer, you can log in to view the current status of this release."]

    redirect_url = util.as_url(selected, project_key=release.project.key, version_key=release.version)
    login_url = f"/auth?login={urllib.parse.quote(redirect_url, safe='')}"
    page.div(".mb-3")[
        htpy.a(".btn.btn-outline-primary", href=login_url)[
            htpy.i(".bi.bi-box-arrow-in-right.me-1"),
            "Log in",
        ],
        htpy.a(".btn.btn-outline-secondary.ms-2", href=util.as_url(root.index))["Return to Home",],
    ]

    return await template.blank(
        f"Vote closed for {release.project.short_display_name} {release.version}",
        content=page.collect(),
    )


@get.typed
async def selected(
    session: web.Public,
    _vote: Literal["vote"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse | str:
    """
    URL: /vote/<project_key>/<version_key>
    Show voting options for a release candidate.
    """
    user_category, release, latest_vote_task = await category_and_release(session, project_key, version_key)

    if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE:
        if session is None:
            return await render_vote_closed_page(release)
        return await mapping.release_as_redirect(session, release)

    return await render_options_page(session, release, user_category, latest_vote_task)


async def _append_cast_vote_form(
    page: htm.Block,
    release: sql.Release,
    potency: str,
    *,
    submit_disabled: bool = False,
    submit_label: str = "Submit vote",
    pre_submit: htm.Element | None = None,
    skip: list[str] | None = None,
    vote_seq: int | None = None,
    id_prefix: str = "",
    flash_error_data: dict[str, Any] | None = None,
) -> None:
    defaults: dict[str, object] = {
        "vote_seq": release.current_vote_seq if (vote_seq is None) else vote_seq,
        "vote_mode": release.effective_vote_mode,
    }
    if (skip is None) or ("comment" not in skip):
        page.p[
            "Fractional votes such as +0.5 are not binding, and count as 0. "
            "To cast one, vote 0 and give the fraction in your comment."
        ]
        comment_template = await construct.resolve_template(release.project, "vote_comment")
        defaults["comment"] = comment_template.body
        if comment_template.warning is not None:
            page.div(".alert.alert-warning", role="alert")[comment_template.warning]

    vote_action_url = util.as_url(
        post.vote.selected_post,
        project_key=release.project.key,
        version_key=release.version,
    )
    # The decision buttons are a custom widget, so a rejected submission has to be put back by hand
    if flash_error_data is None:
        flash_error_data = await form.flash_error_data_pop()
    saved_vote_seq = flash_error_data.get("!vote_seq") or flash_error_data.get("vote_seq")
    if (saved_vote_seq is not None) and (saved_vote_seq["original"] != str(defaults["vote_seq"])):
        flash_error_data = {}
    vote_widget = _vote_decision_widget(
        potency,
        disabled=submit_disabled,
        selected=_flashed_decision(flash_error_data),
        id_prefix=id_prefix,
    )
    cast_vote_form = await form.render(
        model_cls=shared.vote.CastVoteForm,
        action=vote_action_url,
        submit_label=submit_label,
        form_classes=".atr-canary.py-4.px-5.mb-4.border.rounded",
        custom={"decision": vote_widget},
        defaults=defaults,
        submit_disabled=submit_disabled,
        pre_submit=pre_submit,
        skip=skip,
        flash_error_data=flash_error_data,
        id_prefix=id_prefix,
    )
    page.append(cast_vote_form)


def _download_browse(release: sql.Release) -> htm.Element:
    browse_url = util.as_url(
        download.path_empty,
        project_key=release.project.key,
        version_key=release.version,
    )
    return htm.div(".d-flex.align-items-center.gap-2")[
        htpy.a(".btn.btn-outline-primary", href=browse_url)[
            htpy.i(".bi.bi-folder2-open.me-1"),
            "→ Browse files",
        ],
    ]


def _download_curl(release: sql.Release) -> htm.Element:
    script_url = util.as_url(
        download.sh_selected,
        project_key=release.project.key,
        version_key=release.version,
    )
    curl_command = f"curl -fsS {util.absolute_url(script_url)} | sh"

    return htm.div(".mb-3")[
        htm.div(".mb-2")[htm.strong["Use curl:"]],
        htm.div(".input-group")[
            htpy.span(
                "#curl-command.form-control.font-monospace.bg-light",
            )[curl_command],
            htpy.button(
                ".btn.btn-outline-secondary.atr-copy-btn",
                type="button",
                data_clipboard_target="#curl-command",
            )[htpy.i(".bi.bi-clipboard"), " Copy"],
        ],
        htm.div(".form-text.text-muted")["This command downloads all release files to the current directory.",],
    ]


def _download_rsync(release: sql.Release, session: web.Committer) -> htm.Element:
    server_domain = config.get().APP_HOST.split(":", 1)[0]
    if not session.uid.isalnum():
        raise ValueError("Invalid UID")

    rsync_command = (
        f"rsync -av -e 'ssh -p 2222' {session.uid}@{server_domain}:/{release.project.key}/{release.version}/ ./"
    )

    return htm.div(".mb-3")[
        htm.div(".mb-2")[htm.strong["Use rsync:"]],
        htm.div(".input-group")[
            htpy.span(
                "#rsync-command.form-control.font-monospace.bg-light",
            )[rsync_command],
            htpy.button(
                ".btn.btn-outline-secondary.atr-copy-btn",
                type="button",
                data_clipboard_target="#rsync-command",
            )[htpy.i(".bi.bi-clipboard"), " Copy"],
        ],
        htm.div(".form-text.text-muted")[
            "Requires SSH key configuration. ",
            htpy.a(href=util.as_url(keys.keys))["Manage your SSH keys"],
            ".",
        ],
    ]


def _download_zip(release: sql.Release) -> htm.Element:
    zip_url = util.as_url(
        download.zip_selected,
        project_key=release.project.key,
        version_key=release.version,
    )
    return htm.div(".d-flex.align-items-center.gap-2")[
        htpy.a(".btn.btn-primary", href=zip_url)[
            htpy.i(".bi.bi-file-earmark-zip.me-1"),
            "Download all (ZIP)",
        ],
    ]


def _flashed_decision(flash_error_data: dict[str, Any]) -> str | None:
    # A field is flashed under its own name when it failed validation, and under !name otherwise
    flashed = flash_error_data.get("decision") or flash_error_data.get("!decision")
    if flashed is None:
        return None
    original = flashed.get("original")
    return original if isinstance(original, str) else None


def _format_vote_end(vote_end: datetime.datetime, vote: str = "vote") -> str:
    now = datetime.datetime.now(datetime.UTC)
    timestamp = vote_end.strftime("%a %Y-%m-%d at %H:%M UTC")

    remaining = vote_end - now
    total_seconds = int(remaining.total_seconds())
    if total_seconds <= 0:
        return f"The {vote} ended on {timestamp}."

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if (days > 0) or (hours > 0):
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")

    return f"The {vote} ends in {' '.join(parts)}, on {timestamp}."


async def _early_second_round_task(release: sql.Release) -> sql.Task | None:
    if (release.committee is None) or (not release.committee.is_podling) or (release.podling_thread_id is not None):
        return None
    early_task = await interaction.release_early_second_round_task(release)
    if (early_task is None) or (interaction.task_mid_get(early_task) is None):
        return None
    return early_task


async def _get_archive_url(
    release: sql.Release, session: web.Committer | None, latest_vote_task: sql.Task | None
) -> str | None:
    if latest_vote_task is None:
        return None

    task_mid = interaction.task_mid_get(latest_vote_task)
    if task_mid is None:
        return None

    task_recipient = interaction.task_recipient_get(latest_vote_task)
    async with storage.write(session) as write:
        wagp = write.as_general_public()
        return await wagp.cache.get_message_archive_url(task_mid, task_recipient)


def _is_podling_round_two(release: sql.Release) -> bool:
    # TODO: This logic is repeated elsewhere
    # We should deduplicate this
    committee = release.committee
    return (committee is not None) and committee.is_podling and (release.podling_thread_id is not None)


async def _overlap_vote_rounds(
    release: sql.Release, session: web.Committer | None, early_task: sql.Task | None
) -> list[int]:
    if (early_task is None) or (session is None) or (release.committee is None):
        return []
    is_ppmc_binding, _podling = await user.is_binding_for_release(release.committee, session.uid, 1)
    is_ipmc_binding, _incubator = await user.is_binding_for_release(release.committee, session.uid, 2)
    if is_ipmc_binding and (not is_ppmc_binding):
        return [2]
    return [1, 2]


async def _render_binding_status(
    page: htm.Block, committee: sql.Committee, asf_uid: str, vote_round: int | None
) -> str:
    is_binding, binding_committee = await user.is_binding_for_release(committee, asf_uid, vote_round)
    committee_membership = _voting_committee_membership(binding_committee, vote_round)
    article = "an" if committee_membership[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
    if vote_round == 1:
        return _render_binding_status_round_one(page, f"{article} {committee_membership}", is_binding)
    binding_word, non_binding_word = user.binding_terminology(vote_round)
    if is_binding:
        page.p[
            f"As {article} {committee_membership}, your vote is ",
            htpy.strong[binding_word.lower()],
            ".",
        ]
    else:
        page.p[
            f"You are not {article} {committee_membership}. ",
            "Your vote will be recorded as ",
            htpy.strong[non_binding_word.lower()],
            " but is still valued by the community.",
        ]
    return binding_word if is_binding else non_binding_word


def _render_binding_status_round_one(page: htm.Block, ppmc_membership: str, is_ppmc_member: bool) -> str:
    if is_ppmc_member:
        page.p[f"As {ppmc_membership}, your vote is ", htpy.strong["PPMC-binding"], " for podling approval."]
        return "PPMC-binding"
    page.p[f"You are not {ppmc_membership}, so your vote is ", htpy.strong["non-binding"], " for podling approval."]
    return "Non-binding"


def _render_checklist_card(page: htm.Block, release: sql.Release) -> None:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")["Release checklist"]

    body = htm.Block(htm.div, classes=".card-body")
    body.p(".mb-3")["The release manager has provided a checklist of steps to verify this release candidate."]
    checklist_url = util.as_url(checklist.selected, project_key=release.project.key, version_key=release.version)
    body.div[
        htpy.a(
            ".btn.btn-outline-primary",
            href=checklist_url,
        )[
            htpy.i(".bi.bi-list-check.me-2"),
            "→ View release checklist",
        ],
    ]

    card.append(body.collect())
    page.append(card.collect())


async def _render_early_second_round_vote(
    page: htm.Block,
    release: sql.Release,
    session: web.Committer,
    early_task: sql.Task,
    flash_error_data: dict[str, Any] | None = None,
    id_prefix: str = "",
) -> None:
    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)
    potency = await _render_binding_status(page, release.committee, session.uid, 2)
    vote_recipient = _vote_recipient(release, early_task)
    submit_label = await _render_email_vote_status(
        page, release, session.uid, early_task.task_args["vote_seq"], vote_recipient
    )
    archive_url = await _get_archive_url(release, session, early_task)
    _render_vote_delivery(page, archive_url, release, vote_recipient, trusted_vote=False)
    await _append_cast_vote_form(
        page,
        release,
        potency,
        submit_label=submit_label,
        vote_seq=early_task.task_args["vote_seq"],
        id_prefix=id_prefix,
        flash_error_data=flash_error_data,
    )


async def _render_email_vote_status(
    page: htm.Block, release: sql.Release, asf_uid: str, vote_seq: int | None, vote_recipient: str
) -> str:
    if vote_seq is None:
        return "Submit vote"
    ballot = await interaction.latest_ballot_for_voter(release.key, vote_seq, asf_uid, vote_mode=sql.VoteMode.EMAIL)
    if ballot is None:
        return "Submit vote"
    send_task = await interaction.message_send_task(release.project.key, release.version, ballot.receipt_message_id)
    send_status = send_task.status if (send_task is not None) else None
    body = htm.Block(htm.div, classes=".card-body")
    body.p(".mb-2")["Choice: ", htpy.strong[ballot.choice.value]]
    if _reply_failed(send_task):
        header = "Your last vote through ATR was not sent"
        body.p(".mb-0.text-danger")["The email to the list failed, so this vote does not count. Please vote again."]
    elif send_status == sql.TaskStatus.COMPLETED:
        header = "You already voted through ATR"
        reply_url = shared.vote.message_id_source_archive_url(ballot.receipt_message_id, vote_recipient)
        body.p(".mb-2")["Sent: ", resolve.format_utc(ballot.created)]
        body.p(".mb-0")[htpy.a(href=reply_url, target="_blank", rel="noopener")["View your reply in the list archive"]]
    else:
        header = "You already voted through ATR"
        body.p(".mb-0")["Being sent, since ", resolve.format_utc(ballot.created)]
    page.div(".card.mb-3")[htm.div(".card-header.bg-light")[header], body.collect()]
    page.p["Voting again sends a new reply to the vote thread, and your latest reply is the one that counts."]
    return "Vote again"


def _reply_failed(send_task: sql.Task | None) -> bool:
    if send_task is None:
        return False
    if send_task.status in (sql.TaskStatus.FAILED, sql.TaskStatus.BROKEN):
        return True
    if not isinstance(send_task.result, results.MessageSend):
        return False
    list_failure = f"failed to send to {send_task.task_args['email_to'].lower()}:"
    return any(warning.lower().startswith(list_failure) for warning in send_task.result.mail_send_warnings)


def _render_header(
    page: htm.Block,
    release: sql.Release,
    show_resolve_section: bool,
    latest_vote_task: sql.Task | None,
    workflow_upload_note: htm.Element | None,
    early_task: sql.Task | None,
    overlap_rounds: list[int],
) -> None:
    render.html_nav(
        page,
        back_url=util.as_url(root.index),
        back_anchor="Select a release",
        phase="VOTE",
    )

    page.h1[
        "Vote on ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]

    if (badge := render.embargoed_badge(release)) is not None:
        page.append(htm.p[badge])

    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)

    voting_committee_name = _vote_committee_name(release)
    committees = [htm.strong[voting_committee_name], " committee is"]
    if early_task is not None:
        committees = [htm.strong[voting_committee_name], " and ", htm.strong["Incubator"], " committees are"]
    commit_link = render.commit_link(release)
    page.p[
        "The ",
        committees,
        f" currently voting on the release candidate for {release.project.display_name} {release.version}.",
        [" This release was cut from SHA ", commit_link, "."] if (commit_link is not None) else "",
    ]
    if workflow_upload_note is not None:
        page.append(workflow_upload_note)

    vote_end = interaction.vote_end_get(latest_vote_task)
    if vote_end is not None:
        page.p[_format_vote_end(vote_end, "vote" if (early_task is None) else "PPMC vote")]
    if (early_task is not None) and ((ipmc_vote_end := interaction.vote_end_get(early_task)) is not None):
        page.p[_format_vote_end(ipmc_vote_end, "IPMC vote")]

    page.p["To participate in this vote, please select your next step:"]

    steps = htm.Block(htpy.ol, classes=".atr-steps")
    steps.li[htpy.a(".atr-step-link", href="#download")["Download the release files"]]
    steps.li[htpy.a(".atr-step-link", href="#checks")["Review file checks"]]
    if len(overlap_rounds) == 2:
        steps.li[htpy.a(".atr-step-link", href="#vote")["Cast your PPMC vote"]]
        steps.li[htpy.a(".atr-step-link", href="#ipmc-vote")["Cast your IPMC vote"]]
    else:
        steps.li[htpy.a(".atr-step-link", href="#vote")["Cast your vote"]]
    if show_resolve_section:
        steps.li[htpy.a(".atr-step-link", href="#resolve")["Resolve the vote (release managers only)"]]
    page.append(steps.collect())


def _render_publish_location(page: htm.Block, release: sql.Release, latest_vote_task: sql.Task | None) -> None:
    if (latest_vote_task is None) or (release.committee is None):
        return
    if not latest_vote_task.task_args.get("automatic_publish_when_resolved", False):
        return
    suffix = latest_vote_task.task_args.get("download_path_suffix")
    location = util.publish_location(release.committee, safe.RelPath(suffix) if isinstance(suffix, str) else None)
    if release.effective_vote_mode == sql.VoteMode.TRUSTED:
        condition = "passes and every vote is +1"
    elif release.committee.is_podling and (release.podling_thread_id is None):
        condition = "and the Incubator PMC vote both pass"
    else:
        condition = "is resolved as passed"
    page.p["If this vote ", condition, ", ATR will publish the release to ", htm.code[location], "."]


def _render_section_checks(page: htm.Block, release: sql.Release, file_totals: checks.FileStats) -> None:
    import atr.get.checks as checks

    page.h2("#checks")["2. Review file checks"]

    page.p["ATR has checked this release candidate with the following results:"]

    summary = htm.Block(htm.div, classes=".card.mb-4")
    summary.div(".card-header.bg-light")["Automated checks"]

    body = htm.Block(htm.div, classes=".card-body")

    note_count = file_totals.total_after(sql.CheckResultStatus.NOTE)
    suggestion_count = file_totals.total_after(sql.CheckResultStatus.SUGGESTION)
    concern_count = file_totals.total_after(sql.CheckResultStatus.CONCERN)
    blocker_count = file_totals.total_after(sql.CheckResultStatus.BLOCKER)
    exception_count = file_totals.total_after(sql.CheckResultStatus.EXCEPTION)

    note_word = util.plural(note_count, "note", include_count=False)
    suggestion_word = util.plural(suggestion_count, "suggestion", include_count=False)
    concern_word = util.plural(concern_count, "concern", include_count=False)
    blocker_word = util.plural(blocker_count, "blocker", include_count=False)
    exception_word = util.plural(exception_count, "exception", include_count=False)

    checks_list = htm.Block(htm.div, classes=".d-flex.flex-wrap.gap-4.mb-3")
    checks_list.span(".text-success")[
        htpy.i(".bi.bi-check-circle-fill.me-2"),
        f"{note_count} {note_word}",
    ]
    if suggestion_count > 0:
        checks_list.span(".text-warning")[
            htpy.i(".bi.bi-exclamation-triangle-fill.me-2"),
            f"{suggestion_count} {suggestion_word}",
        ]
    else:
        checks_list.span(".text-muted")[
            htpy.i(".bi.bi-exclamation-triangle.me-2"),
            "0 suggestions",
        ]
    if concern_count > 0:
        checks_list.span(".text-danger")[
            htpy.i(".bi.bi-x-circle-fill.me-2"),
            f"{concern_count} {concern_word}",
        ]
    else:
        checks_list.span(".text-muted")[
            htpy.i(".bi.bi-x-circle.me-2"),
            "0 concerns",
        ]
    if blocker_count > 0:
        checks_list.span(".atr-text-blocker")[
            htpy.i(".bi.bi-x-octagon-fill.me-2"),
            f"{blocker_count} {blocker_word}",
        ]
    if exception_count > 0:
        checks_list.span(".atr-text-exception")[
            htpy.i(".bi.bi-cone-striped.me-2"),
            f"{exception_count} {exception_word}",
        ]
    body.append(checks_list.collect())

    body.div[
        htpy.a(
            ".btn.btn-outline-primary",
            href=util.as_url(checks.selected, project_key=release.project.key, version_key=release.version),
        )["→ View detailed results"],
    ]

    summary.append(body.collect())
    page.append(summary.collect())

    if release.project.policy_release_checklist:
        _render_checklist_card(page, release)


def _render_section_download(
    page: htm.Block, release: sql.Release, session: web.Committer | None, user_category: UserCategory
) -> None:
    page.h2("#download")["1. Download the release files"]

    page.p[
        "Download the release files to verify signatures, licenses, and test functionality before casting your vote."
    ]

    is_authenticated = user_category != UserCategory.UNAUTHENTICATED

    page.div(".mb-2")[htm.strong["Use your browser:"]]
    buttons_row = htm.Block(htm.div, classes=".d-flex.flex-wrap.gap-3.mb-3")
    buttons_row.append(_download_browse(release))
    if is_authenticated and (session is not None):
        buttons_row.append(_download_zip(release))
    page.append(buttons_row.collect())

    if is_authenticated and (session is not None):
        page.append(_download_rsync(release, session))

    page.append(_download_curl(release))

    if not is_authenticated:
        page.div(".mb-2")[htm.strong["Use alternatives:"]]
        redirect_url = util.as_url(selected, project_key=release.project.key, version_key=release.version)
        login_url = f"/auth?login={urllib.parse.quote(redirect_url, safe='')}"
        page.div(".mt-3")[
            htpy.a(".btn.btn-outline-secondary", href=login_url)[
                htpy.i(".bi.bi-box-arrow-in-right.me-1"),
                "Log in for ZIP and rsync downloads",
            ],
        ]


def _render_section_resolve(page: htm.Block, release: sql.Release, user_category: UserCategory, number: int) -> None:
    page.h2("#resolve")[f"{number}. Resolve the vote (release managers only)"]

    if user_category == UserCategory.UNAUTHENTICATED:
        page.p["If you are the release manager, log in to access vote tallying and resolution tools."]
        redirect_url = util.as_url(selected, project_key=release.project.key, version_key=release.version)
        login_url = f"/auth?login={urllib.parse.quote(redirect_url, safe='')}"
        page.div[
            htpy.a(".btn.btn-outline-secondary", href=login_url)[
                htpy.i(".bi.bi-box-arrow-in-right.me-1"),
                "Log in as Release Manager",
            ]
        ]
    else:
        page.p["Use the resolution page to review votes and record the outcome."]

        resolve_url = util.as_url(
            resolve.selected,
            project_key=release.project.key,
            version_key=release.version,
        )
        page.div[
            htpy.a(".btn.btn-success", href=resolve_url)[
                htpy.i(".bi.bi-clipboard-check.me-1"),
                strings.RESOLVE_VOTE_BUTTON,
            ],
        ]


async def _render_section_vote(
    page: htm.Block,
    release: sql.Release,
    session: web.Committer | None,
    user_category: UserCategory,
    archive_url: str | None,
    latest_vote_task: sql.Task | None,
    early_task: sql.Task | None,
    overlap_rounds: list[int],
) -> None:
    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)

    vote_recipient = _vote_recipient(release, latest_vote_task)
    if (early_task is not None) and (session is not None) and overlap_rounds:
        await _render_section_vote_both_open(
            page, release, session, archive_url, vote_recipient, latest_vote_task, early_task, overlap_rounds
        )
        return

    page.h2("#vote")["3. Cast your vote"]
    if user_category == UserCategory.UNAUTHENTICATED:
        _render_vote_unauthenticated(page, release, archive_url, vote_recipient)
        if early_task is not None:
            early_archive_url = await _get_archive_url(release, session, early_task)
            page.p[
                "The IPMC vote is also open, on ",
                htpy.code[_vote_recipient(release, early_task)],
                _view_thread(early_archive_url),
                ".",
            ]
    else:
        await _render_vote_authenticated(page, release, session, archive_url, vote_recipient, latest_vote_task)


async def _render_section_vote_both_open(
    page: htm.Block,
    release: sql.Release,
    session: web.Committer,
    archive_url: str | None,
    vote_recipient: str,
    latest_vote_task: sql.Task | None,
    early_task: sql.Task,
    overlap_rounds: list[int],
) -> None:
    if overlap_rounds == [2]:
        page.h2("#vote")["3. Cast your IPMC vote"]
        await _render_early_second_round_vote(page, release, session, early_task)
        return
    page.h2("#vote")["3. Cast your PPMC vote"]
    flash_error_data = await form.flash_error_data_pop()
    await _render_vote_authenticated(
        page, release, session, archive_url, vote_recipient, latest_vote_task, flash_error_data, "ppmc_"
    )
    page.h2("#ipmc-vote")["4. Cast your IPMC vote"]
    await _render_early_second_round_vote(page, release, session, early_task, flash_error_data, "ipmc_")


async def _render_trusted_vote_authenticated(
    page: htm.Block,
    release: sql.Release,
    session: web.Committer,
    archive_url: str | None,
    vote_recipient: str,
    latest_vote_task: sql.Task | None,
) -> None:
    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)

    latest_ballot = None
    if release.current_vote_seq is not None:
        latest_ballot = await interaction.latest_ballot_for_voter(
            release.key, release.current_vote_seq, session.uid, vote_mode=sql.VoteMode.TRUSTED
        )

    vote_round = interaction.trusted_vote_round(release)
    potency = await _render_binding_status(page, release.committee, session.uid, vote_round)

    if latest_ballot is not None:
        page.append(_trusted_vote_status(latest_ballot, vote_recipient))
        page.p[
            "Submitting again records a new ballot. New ballots during the voting period always replace old ballots."
        ]

    task_mid = interaction.task_mid_get(latest_vote_task) if (latest_vote_task is not None) else None
    casting_enabled = (release.current_vote_seq is not None) and (task_mid is not None)
    submit_label = "Resubmit vote" if (latest_ballot is not None) else "Submit vote"

    if casting_enabled:
        _render_vote_delivery(
            page,
            archive_url,
            release,
            vote_recipient,
            trusted_vote=True,
            recast=latest_ballot is not None,
        )
        _render_publish_location(page, release, latest_vote_task)
        await _append_cast_vote_form(page, release, potency, submit_label=submit_label)
        return

    pre_submit = _trusted_casting_disabled_notice(archive_url, vote_recipient)
    await _append_cast_vote_form(
        page,
        release,
        potency,
        submit_disabled=True,
        submit_label=submit_label,
        pre_submit=pre_submit,
        skip=["comment"],
    )


async def _render_vote_authenticated(
    page: htm.Block,
    release: sql.Release,
    session: web.Committer | None,
    archive_url: str | None,
    vote_recipient: str,
    latest_vote_task: sql.Task | None,
    flash_error_data: dict[str, Any] | None = None,
    id_prefix: str = "",
) -> None:
    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)
    if session is None:
        raise ValueError("Session required for authenticated vote")

    if release.effective_vote_mode == sql.VoteMode.TRUSTED:
        await _render_trusted_vote_authenticated(page, release, session, archive_url, vote_recipient, latest_vote_task)
        return

    vote_round = interaction.trusted_vote_round(release)
    potency = await _render_binding_status(page, release.committee, session.uid, vote_round)
    submit_label = await _render_email_vote_status(page, release, session.uid, release.current_vote_seq, vote_recipient)
    _render_vote_delivery(page, archive_url, release, vote_recipient, trusted_vote=False)
    _render_publish_location(page, release, latest_vote_task)
    await _append_cast_vote_form(
        page, release, potency, submit_label=submit_label, id_prefix=id_prefix, flash_error_data=flash_error_data
    )


def _render_vote_delivery(
    page: htm.Block,
    archive_url: str | None,
    release: sql.Release,
    vote_recipient: str,
    *,
    trusted_vote: bool,
    recast: bool = False,
) -> None:
    if trusted_vote and archive_url:
        prefix = "A new submission will be" if recast else "Your vote will be"
        page.p[
            prefix,
            " recorded by ATR and a receipt will be queued for ",
            htpy.code[vote_recipient],
            " (",
            htpy.a(href=archive_url, target="_blank", rel="noopener")["view thread"],
            ").",
        ]
    elif trusted_vote:
        prefix = "A new submission will be" if recast else "Your vote will be"
        page.p[prefix, " recorded by ATR and a receipt will be queued for ", htpy.code[vote_recipient], "."]
    elif archive_url:
        page.p[
            "Your vote will be sent to ",
            htpy.code[vote_recipient],
            " (",
            htpy.a(href=archive_url, target="_blank", rel="noopener")["view thread"],
            ").",
        ]
    else:
        page.p["Your vote will be sent to ", htpy.code[vote_recipient], "."]
    if banner := render.archived_project_banner(release.project, errors.RELEASE_ACTIONS_DISABLED):
        page.append(banner)


def _render_vote_task_warnings(page: htm.Block, latest_vote_task: sql.Task | None) -> None:
    if latest_vote_task is None:
        return
    if latest_vote_task.status == sql.TaskStatus.FAILED:
        page.div(".alert.alert-danger", role="alert")[
            "Starting this vote failed: ",
            latest_vote_task.error or "unknown error",
        ]
        return
    if not isinstance(latest_vote_task.result, results.VoteInitiate):
        return
    warnings = latest_vote_task.result.mail_send_warnings
    if not warnings:
        return
    page.div(".alert.alert-warning", role="alert")[
        htm.p["The vote announcement email reported delivery problems:"],
        htpy.ul(".mb-0")[[htpy.li[warning] for warning in warnings]],
    ]


def _render_vote_unauthenticated(
    page: htm.Block,
    release: sql.Release,
    archive_url: str | None,
    vote_recipient: str,
) -> None:
    page.p["Once you have reviewed the release, you can cast your vote."]

    redirect_url = util.as_url(selected, project_key=release.project.key, version_key=release.version)
    login_url = f"/auth?login={urllib.parse.quote(redirect_url, safe='')}"

    # ASF Committers box
    committer_box = htm.Block(htm.div, classes=".card.mb-3")
    committer_box.div(".card-header.bg-light")[
        htpy.i(".bi.bi-key-fill.me-2"),
        "ASF Committers",
    ]
    committer_body = htm.Block(htm.div, classes=".card-body")
    committer_body.p["Log in to vote directly through ATR. Your vote will be recorded and sent to the mailing list."]
    committer_body.div[
        htpy.a(".btn.btn-outline-secondary", href=login_url)[
            htpy.i(".bi.bi-box-arrow-in-right.me-1"),
            "Log in to vote",
        ],
    ]
    committer_box.append(committer_body.collect())
    page.append(committer_box.collect())

    # Everyone else box
    email_box = htm.Block(htm.div, classes=".card.mb-3")
    email_box.div(".card-header.bg-light")[
        htpy.i(".bi.bi-envelope-fill.me-2"),
        "Everyone else",
    ]
    email_body = htm.Block(htm.div, classes=".card-body")
    email_body.p["Contributors and community members can vote by replying to the vote thread on the mailing list."]
    if archive_url:
        email_body.div[
            htpy.a(".btn.btn-outline-primary", href=archive_url, target="_blank", rel="noopener")[
                "View vote thread ",
                htpy.i(".bi.bi-box-arrow-up-right"),
            ],
        ]
    else:
        email_body.p(".text-muted.mb-0")[
            "The vote thread archive is not yet available. ",
            "The current vote was sent to ",
            htpy.code[vote_recipient],
            ".",
        ]
    email_box.append(email_body.collect())
    page.append(email_box.collect())


def _trusted_casting_disabled_notice(archive_url: str | None, vote_recipient: str) -> htm.Element:
    children: list[str | htm.Element] = [
        "ATR ballots are unavailable until the vote-start email has a message ID. ",
        "Votes will be recorded by ATR and receipts will be queued for ",
        htpy.code[vote_recipient],
    ]
    if archive_url is not None:
        children.extend(
            [
                " (",
                htpy.a(href=archive_url, target="_blank", rel="noopener")["view thread"],
                ")",
            ]
        )
    children.append(".")
    return htm.div(".alert.alert-info.mb-3", role="alert")[children]


def _trusted_vote_status(latest_ballot: sql.BallotPaper, vote_recipient: str | None) -> htm.Element:
    body = htm.Block(htm.div, classes=".card-body")
    body.p(".mb-2")["Choice: ", htpy.strong[latest_ballot.choice.value]]
    body.p(".mb-2")["Recorded: ", resolve.format_utc(latest_ballot.created)]
    if vote_recipient is None:
        body.p(".mb-0")["Receipt message ID: ", htpy.code[latest_ballot.receipt_message_id]]
    else:
        receipt_archive_url = shared.vote.message_id_source_archive_url(
            latest_ballot.receipt_message_id, vote_recipient
        )
        body.p(".mb-0")[
            "Receipt message ID: ",
            htpy.a(href=receipt_archive_url, target="_blank", rel="noopener")[
                htpy.code[latest_ballot.receipt_message_id]
            ],
        ]
    return htm.div(".card.mb-3")[
        htm.div(".card-header.bg-light")["You already cast a vote"],
        body.collect(),
    ]


def _view_thread(archive_url: str | None) -> list[str | htm.Element]:
    if archive_url is None:
        return []
    return [" (", htpy.a(href=archive_url, target="_blank", rel="noopener")["view thread"], ")"]


def _vote_committee_name(release: sql.Release) -> str:
    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)
    if _is_podling_round_two(release):
        return "Incubator"
    return release.committee.display_name


def _vote_decision_widget(
    potency: str, *, disabled: bool = False, selected: str | None = None, id_prefix: str = ""
) -> htm.Element:
    input_attrs = {"disabled": True} if disabled else {}
    return htpy.div(id=f"{id_prefix}decision", class_="btn-group", role="group")[
        htpy.input(
            type="radio",
            class_="btn-check",
            name="decision",
            id=f"{id_prefix}decision_0",
            value="+1",
            autocomplete="off",
            checked=selected == "+1",
            **input_attrs,
        ),
        htpy.label(class_="btn btn-outline-success", for_=f"{id_prefix}decision_0")[f"+1 ({potency})"],
        htpy.input(
            type="radio",
            class_="btn-check",
            name="decision",
            id=f"{id_prefix}decision_1",
            value="0",
            autocomplete="off",
            checked=selected == "0",
            **input_attrs,
        ),
        htpy.label(class_="btn btn-outline-secondary", for_=f"{id_prefix}decision_1")["0"],
        htpy.input(
            type="radio",
            class_="btn-check",
            name="decision",
            id=f"{id_prefix}decision_2",
            value="-1",
            autocomplete="off",
            checked=selected == "-1",
            **input_attrs,
        ),
        htpy.label(class_="btn btn-outline-danger", for_=f"{id_prefix}decision_2")[f"-1 ({potency})"],
    ]


def _vote_recipient(release: sql.Release, latest_vote_task: sql.Task | None) -> str:
    if latest_vote_task is not None:
        if (task_recipient := interaction.task_recipient_get(latest_vote_task)) is not None:
            return task_recipient
        task_email_to = latest_vote_task.task_args.get("email_to")
        if isinstance(task_email_to, str) and task_email_to:
            return task_email_to
    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)
    if _is_podling_round_two(release):
        return util.INCUBATOR_GENERAL_ADDRESS
    return f"dev@{release.committee.key}.apache.org"


def _voting_committee_membership(binding_committee: str, vote_round: int | None) -> str:
    if binding_committee == "Incubator":
        return f"{binding_committee} IPMC member"
    if vote_round == 1:
        return f"{binding_committee} PPMC member"
    return f"{binding_committee} PMC member"


async def _workflow_upload_note(release: sql.Release, *, signed_in: bool) -> htm.Element | None:
    # Only the revision being voted on counts. An earlier workflow upload that somebody has since
    # changed by hand is not what the vote is about
    if release.latest_revision_number is None:
        return None
    payload = await attestable.github_tp_payload_read(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    )
    if payload is None:
        return None
    return render.workflow_upload_note(payload, show_actor=signed_in)
