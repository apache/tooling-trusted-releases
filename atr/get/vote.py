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
from typing import TYPE_CHECKING, Literal

import asfquart.base as base
import htpy

import atr.blueprints.get as get
import atr.config as config
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.checklist as checklist
import atr.get.download as download
import atr.get.keys as keys
import atr.get.manual as manual
import atr.get.resolve as resolve
import atr.get.root as root
import atr.htm as htm
import atr.mapping as mapping
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.storage as storage
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
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))

        if release.committee is None:
            raise ValueError("Release has no committee")

        latest_vote_task = await interaction.release_current_vote_task(release, data)
        vote_initiator_uid: str | None = None
        if latest_vote_task is not None:
            vote_initiator_uid = latest_vote_task.task_args.get("initiator_id")

    if session is None:
        return UserCategory.UNAUTHENTICATED, release, latest_vote_task

    is_pmc_member = user.is_committee_member(release.committee, session.uid)
    is_designated_release_manager = (session.uid in release.committee.release_managers) and (
        session.uid in release.committee.committers
    )
    is_release_manager = is_designated_release_manager or (
        (vote_initiator_uid is not None) and (session.uid == vote_initiator_uid)
    )

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

    file_totals = await checks.get_file_totals(release, session)
    archive_url = await _get_archive_url(release, session, latest_vote_task)

    page = htm.Block()
    _render_header(page, release, show_resolve_section, latest_vote_task)
    _render_section_download(page, release, session, user_category)
    _render_section_checks(page, release, file_totals)
    await _render_section_vote(page, release, session, user_category, archive_url, latest_vote_task)
    if show_resolve_section:
        _render_section_resolve(page, release, user_category)

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
    vote_widget: htm.Element,
    *,
    submit_disabled: bool = False,
    submit_label: str = "Submit vote",
    pre_submit: htm.Element | None = None,
    skip: list[str] | None = None,
) -> None:
    defaults: dict[str, object] = {
        "vote_seq": release.current_vote_seq,
        "vote_mode": release.effective_vote_mode,
    }
    if (skip is None) or ("comment" not in skip):
        defaults["comment"] = release.project.policy_vote_comment_template

    vote_action_url = util.as_url(
        post.vote.selected_post,
        project_key=release.project.key,
        version_key=release.version,
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
    app_host = config.get().APP_HOST
    script_url = util.as_url(
        download.sh_selected,
        project_key=release.project.key,
        version_key=release.version,
    )
    curl_command = f"curl -s https://{app_host}{script_url} | sh"

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


def _format_vote_end(vote_end: datetime.datetime) -> str:
    now = datetime.datetime.now(datetime.UTC)
    timestamp = vote_end.strftime("%a %Y-%m-%d at %H:%M UTC")

    remaining = vote_end - now
    total_seconds = int(remaining.total_seconds())
    if total_seconds <= 0:
        return f"The vote ended on {timestamp}."

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if (days > 0) or (hours > 0):
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")

    return f"The vote ends in {' '.join(parts)}, on {timestamp}."


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


def _render_binding_status(page: htm.Block, is_binding: bool, binding_committee: str, vote_round: int | None) -> None:
    binding_word, non_binding_word = user.binding_terminology(vote_round)
    committee_membership = _voting_committee_membership(binding_committee, vote_round)
    article = "an" if committee_membership[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
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


def _render_header(
    page: htm.Block, release: sql.Release, show_resolve_section: bool, latest_vote_task: sql.Task | None
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

    if release.committee is None:
        raise ValueError("Release has no committee")

    voting_committee_name = _vote_committee_name(release)
    page.p[
        "The ",
        htm.strong[voting_committee_name],
        " committee is currently voting on the release candidate for"
        f" {release.project.display_name} {release.version}.",
    ]

    vote_end = interaction.vote_end_get(latest_vote_task)
    if vote_end is not None:
        page.p[_format_vote_end(vote_end)]

    page.p["To participate in this vote, please select your next step:"]

    steps = htm.Block(htpy.ol, classes=".atr-steps")
    steps.li[htpy.a(".atr-step-link", href="#download")["Download the release files"]]
    steps.li[htpy.a(".atr-step-link", href="#checks")["Review file checks"]]
    steps.li[htpy.a(".atr-step-link", href="#vote")["Cast your vote"]]
    if show_resolve_section:
        steps.li[htpy.a(".atr-step-link", href="#resolve")["Resolve the vote (release managers only)"]]
    page.append(steps.collect())


def _render_section_checks(page: htm.Block, release: sql.Release, file_totals: checks.FileStats) -> None:
    import atr.get.checks as checks

    page.h2("#checks")["2. Review file checks"]

    page.p["ATR has checked this release candidate with the following results:"]

    summary = htm.Block(htm.div, classes=".card.mb-4")
    summary.div(".card-header.bg-light")["Automated checks"]

    body = htm.Block(htm.div, classes=".card-body")

    note_count = file_totals.total_after(sql.CheckResultStatus.NOTE)
    suggestion_count = file_totals.total_after(sql.CheckResultStatus.SUGGESTION)
    issue_count = file_totals.total_after(sql.CheckResultStatus.CONCERN) + file_totals.total_after(
        sql.CheckResultStatus.BLOCKER
    )
    exception_count = file_totals.total_after(sql.CheckResultStatus.EXCEPTION)

    note_word = util.plural(note_count, "note", include_count=False)
    suggestion_word = util.plural(suggestion_count, "suggestion", include_count=False)
    issue_word = util.plural(issue_count, "issue", include_count=False)
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
    if issue_count > 0:
        checks_list.span(".text-danger")[
            htpy.i(".bi.bi-x-circle-fill.me-2"),
            f"{issue_count} {issue_word}",
        ]
    else:
        checks_list.span(".text-muted")[
            htpy.i(".bi.bi-x-circle.me-2"),
            "0 issues",
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


def _render_section_resolve(page: htm.Block, release: sql.Release, user_category: UserCategory) -> None:
    page.h2("#resolve")["4. Resolve the vote (release managers only)"]

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
        page.p["When the voting period concludes, use the resolution page to tally votes and record the outcome."]

        if release.effective_vote_mode == sql.VoteMode.MANUAL:
            resolve_url = util.as_url(
                manual.resolve_selected,
                project_key=release.project.key,
                version_key=release.version,
            )
        else:
            resolve_url = util.as_url(
                resolve.selected,
                project_key=release.project.key,
                version_key=release.version,
            )
        page.div[
            htpy.a(".btn.btn-success", href=resolve_url)[
                htpy.i(".bi.bi-clipboard-check.me-1"),
                "Resolve vote",
            ],
        ]


async def _render_section_vote(
    page: htm.Block,
    release: sql.Release,
    session: web.Committer | None,
    user_category: UserCategory,
    archive_url: str | None,
    latest_vote_task: sql.Task | None,
) -> None:
    page.h2("#vote")["3. Cast your vote"]

    if release.committee is None:
        raise ValueError("Release has no committee")

    if release.effective_vote_mode == sql.VoteMode.MANUAL:
        _render_vote_manual(page)
        return

    vote_recipient = _vote_recipient(release, latest_vote_task)
    if user_category == UserCategory.UNAUTHENTICATED:
        _render_vote_unauthenticated(page, release, archive_url, vote_recipient)
    else:
        await _render_vote_authenticated(page, release, session, archive_url, vote_recipient, latest_vote_task)


async def _render_trusted_vote_authenticated(
    page: htm.Block,
    release: sql.Release,
    session: web.Committer,
    archive_url: str | None,
    vote_recipient: str,
    latest_vote_task: sql.Task | None,
) -> None:
    if release.committee is None:
        raise ValueError("Release has no committee")

    latest_ballot = None
    if release.current_vote_seq is not None:
        latest_ballot = await interaction.effective_latest_ballot_for_voter(
            release,
            release.current_vote_seq,
            session.uid,
        )

    vote_round = _vote_round(release)
    is_binding, binding_committee = await user.is_binding_for_release(release.committee, session.uid, vote_round)
    binding_word, non_binding_word = user.binding_terminology(vote_round)
    potency = binding_word if is_binding else non_binding_word
    _render_binding_status(page, is_binding, binding_committee, vote_round)

    if latest_ballot is not None:
        is_carried = (latest_ballot.vote_round == 1) and (vote_round == 2)
        receipt_recipient: str | None = vote_recipient
        if is_carried and (release.current_vote_seq is not None):
            receipt_recipient = await interaction.previous_round_one_recipient(release, release.current_vote_seq)
        page.append(_trusted_vote_status(latest_ballot, receipt_recipient, is_carried=is_carried))
        page.p[
            "Submitting again records a new ballot. New ballots during the voting period always replace old ballots."
        ]

    task_mid = interaction.task_mid_get(latest_vote_task) if (latest_vote_task is not None) else None
    casting_enabled = (release.current_vote_seq is not None) and (task_mid is not None)
    vote_widget = _vote_decision_widget(potency, disabled=not casting_enabled)
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
        await _append_cast_vote_form(page, release, vote_widget, submit_label=submit_label)
        return

    pre_submit = _trusted_casting_disabled_notice(archive_url, vote_recipient)
    await _append_cast_vote_form(
        page,
        release,
        vote_widget,
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
) -> None:
    if release.committee is None:
        raise ValueError("Release has no committee")
    if session is None:
        raise ValueError("Session required for authenticated vote")

    if release.effective_vote_mode == sql.VoteMode.TRUSTED:
        await _render_trusted_vote_authenticated(page, release, session, archive_url, vote_recipient, latest_vote_task)
        return

    vote_round = _vote_round(release)
    is_binding, binding_committee = await user.is_binding_for_release(release.committee, session.uid, vote_round)
    binding_word, non_binding_word = user.binding_terminology(vote_round)

    potency = binding_word if is_binding else non_binding_word
    _render_binding_status(page, is_binding, binding_committee, vote_round)
    _render_vote_delivery(page, archive_url, release, vote_recipient, trusted_vote=False)
    vote_widget = _vote_decision_widget(potency)
    await _append_cast_vote_form(page, release, vote_widget)


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
    if banner := render.archived_project_banner(release.project, "Release actions are disabled."):
        page.append(banner)


def _render_vote_manual(page: htm.Block) -> None:
    page.div(".alert.alert-info", role="alert")[
        htpy.i(".bi.bi-info-circle.me-2"),
        "This release uses manual voting. The vote thread is hosted on the project mailing list, ",
        "and ATR cannot record votes or display the thread for it. ",
        "To participate, reply to the vote thread directly on the mailing list.",
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
        "Trusted ballots are unavailable until the vote-start email has a message ID. ",
        "Trusted votes will be recorded by ATR and receipts will be queued for ",
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


def _trusted_vote_status(
    latest_ballot: sql.BallotPaper,
    vote_recipient: str | None,
    *,
    is_carried: bool = False,
) -> htm.Element:
    header = "You already cast a vote (carried from the first round)" if is_carried else "You already cast a vote"
    body = htm.Block(htm.div, classes=".card-body")
    body.p(".mb-2")["Choice: ", htpy.strong[latest_ballot.choice.value]]
    body.p(".mb-2")["Recorded: ", resolve.format_utc(latest_ballot.created)]
    receipt_class = ".mb-2" if is_carried else ".mb-0"
    if vote_recipient is None:
        body.p(receipt_class)["Receipt message ID: ", htpy.code[latest_ballot.receipt_message_id]]
    else:
        receipt_archive_url = shared.vote.message_id_source_archive_url(
            latest_ballot.receipt_message_id, vote_recipient
        )
        body.p(receipt_class)[
            "Receipt message ID: ",
            htpy.a(href=receipt_archive_url, target="_blank", rel="noopener")[
                htpy.code[latest_ballot.receipt_message_id]
            ],
        ]
    if is_carried:
        body.div(".alert.alert-info.mb-0", role="alert")[
            "Your first round PPMC ballot is being counted as a binding Incubator vote in this round ",
            "because you are on the Incubator PMC. Submitting a new ballot below will replace your ",
            "carried vote for this round.",
        ]
    return htm.div(".card.mb-3")[
        htm.div(".card-header.bg-light")[header],
        body.collect(),
    ]


def _vote_committee_name(release: sql.Release) -> str:
    if release.committee is None:
        raise ValueError("Release has no committee")
    if _is_podling_round_two(release):
        return "Incubator"
    return release.committee.display_name


def _vote_decision_widget(potency: str, *, disabled: bool = False) -> htm.Element:
    input_attrs = {"disabled": True} if disabled else {}
    return htpy.div(id="decision", class_="btn-group", role="group")[
        htpy.input(
            type="radio",
            class_="btn-check",
            name="decision",
            id="decision_0",
            value="+1",
            autocomplete="off",
            **input_attrs,
        ),
        htpy.label(class_="btn btn-outline-success", for_="decision_0")[f"+1 ({potency})"],
        htpy.input(
            type="radio",
            class_="btn-check",
            name="decision",
            id="decision_1",
            value="0",
            autocomplete="off",
            **input_attrs,
        ),
        htpy.label(class_="btn btn-outline-secondary", for_="decision_1")["0"],
        htpy.input(
            type="radio",
            class_="btn-check",
            name="decision",
            id="decision_2",
            value="-1",
            autocomplete="off",
            **input_attrs,
        ),
        htpy.label(class_="btn btn-outline-danger", for_="decision_2")[f"-1 ({potency})"],
    ]


def _vote_recipient(release: sql.Release, latest_vote_task: sql.Task | None) -> str:
    if latest_vote_task is not None:
        if (task_recipient := interaction.task_recipient_get(latest_vote_task)) is not None:
            return task_recipient
        task_email_to = latest_vote_task.task_args.get("email_to")
        if isinstance(task_email_to, str) and task_email_to:
            return task_email_to
    if release.committee is None:
        raise ValueError("Release has no committee")
    if _is_podling_round_two(release):
        return util.INCUBATOR_GENERAL_ADDRESS
    return f"dev@{release.committee.key}.apache.org"


def _vote_round(release: sql.Release) -> int | None:
    if (release.committee is not None) and release.committee.is_podling:
        return 2 if (release.podling_thread_id is not None) else 1
    return None


def _voting_committee_membership(binding_committee: str, vote_round: int | None) -> str:
    if binding_committee == "Incubator":
        return f"{binding_committee} IPMC member"
    if vote_round == 1:
        return f"{binding_committee} PPMC member"
    return f"{binding_committee} PMC member"
