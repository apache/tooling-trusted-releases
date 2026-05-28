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

import aiofiles.os
import htpy
import quart

import atr.blueprints.get as get
import atr.config as config
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.compose as compose
import atr.get.keys as keys
import atr.get.projects as projects
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.render as render
import atr.sessions as sessions
import atr.shared as shared
import atr.storage as storage
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web


@get.typed
async def selected(
    session: web.Committer,
    _voting: Literal["voting"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse | str:
    """
    URL: /voting/<project_key>/<version_key>
    """
    await session.prevent_confusing_ui_display(project_key)
    async with db.session() as data:
        match await interaction.release_ready_to_start_vote(
            session,
            project_key,
            version_key,
            data,
            frozenset({sql.VoteMode.EMAIL, sql.VoteMode.TRUSTED}),
        ):
            case str() as error:
                return await session.redirect(
                    compose.selected,
                    error=error,
                    project_key=str(project_key),
                    version_key=str(version_key),
                )
            case (release, committee):
                pass

        permitted_recipients = util.permitted_podling_first_round_recipients(
            session.uid,
            committee.key,
            is_podling=committee.is_podling,
        )
        second_round_recipients = (
            util.permitted_podling_second_round_recipients(session.uid) if committee.is_podling else []
        )

        min_hours = 72
        release_policy = release.project.release_policy
        if release_policy and (release_policy.min_hours is not None):
            min_hours = release_policy.min_hours

        vote_mode = release.effective_vote_mode
        default_subject_template = await construct.start_vote_subject_default(project_key)
        default_body_template = await construct.start_vote_default(project_key)
        subject_template_hash = construct.template_hash(default_subject_template)

        options = construct.StartVoteOptions(
            asfuid=session.uid,
            fullname=session.fullname,
            project_key=project_key,
            version_key=release.safe_version_key,
            revision_number=release.safe_latest_revision_number,
            vote_duration=min_hours,
        )
        default_subject, default_body = await construct.start_vote_subject_and_body(
            default_subject_template, default_body_template, options
        )

        keys_warning = await _check_keys_warning(committee)

        async with storage.read(session) as read:
            concern_groups = await shared.voting.concern_groups_for_release(read.as_general_public(), release)
        flash_data = await sessions.form_error_pop(quart.request.path)
        submitted_concerns = util.submitted_concerns_from_flash(flash_data)

        publish_eligible = bool(config.get().SVN_PUBLISH_URL) and user.is_committee_member(committee, session.uid)
        default_download_path_suffix = _default_download_path_suffix(release, committee)
        content = await _render_page(
            release=release,
            revision_number=str(release.safe_latest_revision_number),
            permitted_recipients=permitted_recipients,
            second_round_recipients=second_round_recipients,
            podling_vote_round=_podling_vote_round(release, committee),
            default_subject=default_subject,
            subject_template_hash=subject_template_hash,
            default_body=default_body,
            min_hours=min_hours,
            vote_mode=vote_mode,
            keys_warning=keys_warning,
            asf_uid=session.uid,
            concern_groups=concern_groups,
            submitted_concerns=submitted_concerns,
            flash_data=flash_data,
            publish_eligible=publish_eligible,
            default_download_path_suffix=default_download_path_suffix,
        )

        return await template.blank(
            title=f"Start voting on {release.project.short_display_name} {release.version}",
            content=content,
            javascripts=["vote-body-duration"],
        )


def _add_automatic_publish_fields(
    custom: dict[str, htm.Element | htm.VoidElement],
    skip: list[str],
    publish_eligible: bool,
    vote_mode: sql.VoteMode,
    podling_vote_round: int | None,
) -> None:
    if not publish_eligible:
        skip.append("automatic_publish_when_resolved")
        skip.append("download_path_suffix")
        return
    vote_label = _publish_vote_label(vote_mode, podling_vote_round)
    custom["automatic_publish_when_resolved"] = htm.div[
        htpy.input(
            type="checkbox",
            name="automatic_publish_when_resolved",
            id="automatic_publish_when_resolved",
            value="on",
            checked=True,
            class_="form-check-input",
        ),
        htm.div(".form-text.text-muted.mt-1")[
            "If enabled, ATR will publish the preview revision to SVN automatically",
            f" when the final approving {vote_label} resolves.",
        ],
    ]


async def _check_keys_warning(committee: sql.Committee) -> bool:
    keys_file_path = paths.committee_downloads_dir(committee) / "KEYS"
    return not await aiofiles.os.path.isfile(keys_file_path)


def _default_download_path_suffix(release: sql.Release, committee: sql.Committee) -> safe.RelPath | None:
    if release.project.key == util.unwrap(committee.key):
        return None
    return safe.RelPath(f"{release.project.key}-{release.version}")


def _podling_vote_round(release: sql.Release, committee: sql.Committee) -> int | None:
    if not committee.is_podling:
        return None
    return 2 if (release.podling_thread_id is not None) else 1


def _publish_vote_label(vote_mode: sql.VoteMode, podling_vote_round: int | None) -> str:
    if vote_mode != sql.VoteMode.TRUSTED:
        return "vote"
    if podling_vote_round == 1:
        return "first round vote"
    if podling_vote_round == 2:
        return "second round vote"
    return "vote"


def _render_body_field(default_body: str, project_key: str) -> htm.Element:
    """Render the body textarea with a link to edit the template."""
    textarea = htpy.textarea(
        "#body.form-control.font-monospace",
        name="body",
        rows="12",
    )[default_body]

    settings_url = util.as_url(projects.view, project_key=project_key) + "?tab=vote#start_vote_template"
    link = htm.div(".form-text.text-muted.mt-2")[
        "To edit the template, go to the ",
        htm.a(href=settings_url)["project settings"],
        ".",
    ]

    return htm.div[textarea, link]


async def _render_page(
    release,
    revision_number: str,
    permitted_recipients: list[str],
    second_round_recipients: list[str],
    podling_vote_round: int | None,
    default_subject: str,
    subject_template_hash: str,
    default_body: str,
    min_hours: int,
    vote_mode: sql.VoteMode,
    keys_warning: bool,
    asf_uid: str,
    concern_groups: list[util.ConcernGroup],
    submitted_concerns: list[str],
    flash_data: dict,
    publish_eligible: bool,
    default_download_path_suffix: safe.RelPath | None,
) -> htm.Element:
    page = htm.Block()

    back_link_url = util.as_url(
        compose.selected,
        project_key=release.project.key,
        version_key=release.version,
    )
    render.html_nav(
        page,
        back_link_url,
        f"Compose {release.short_display_name}",
        "COMPOSE",
    )

    page.h1(".mb-4")[
        "Start voting on ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]

    if banner := render.archived_project_banner(release.project, "Release actions are disabled."):
        page.append(banner)

    page.div(".px-3.py-4.mb-4.bg-light.border.rounded")[
        htm.p(".mb-0")[
            "Starting a vote for this draft release will cause an email to be sent to the appropriate mailing list, "
            "and advance the draft to the VOTE phase. Please note that this feature is currently in development."
        ]
    ]

    if keys_warning:
        keys_url = util.as_url(keys.keys) + f"#committee-{release.committee.key}"
        page.div(".p-3.mb-4.bg-warning-subtle.border.border-warning.rounded")[
            htm.strong["Warning: "],
            "The KEYS file is missing. Please autogenerate one on the ",
            htm.a(href=keys_url)["KEYS page"],
            ".",
        ]

    cancel_url = util.as_url(
        compose.selected,
        project_key=release.project.key,
        version_key=release.version,
    )

    custom_subject_widget = _render_subject_field(default_subject, release.project.key)
    custom_body_widget = _render_body_field(default_body, release.project.key)
    policy_to, policy_cc, policy_bcc = release.project.policy_recipients(sql.RecipientAction.VOTE)
    permitted_set = set(permitted_recipients)
    fallback_to = permitted_recipients[0] if permitted_recipients else None
    default_to = policy_to if (policy_to in permitted_set) else fallback_to
    settings_url = util.as_url(projects.view, project_key=release.project.key) + "?tab=vote#email_to"
    recipient_radios = htm.div[
        render.html_recipients_to_radios(
            permitted_recipients,
            default_to=default_to,
            documentation=(
                "Note: The options to send to the user-tests "
                "mailing list and yourself are provided for "
                "testing purposes only, and will not be "
                "available in the finished version of ATR. "
                "If the option you pick is not a mailing list, "
                "you will not be able to use vote tabulation."
            ),
        ),
        htpy.details(".mt-2")[
            htpy.summary["Select CC and BCC recipients"],
            render.html_recipients_cc_bcc_table(
                permitted_recipients,
                selected_cc={address for address in policy_cc if (address in permitted_set)},
                selected_bcc={address for address in policy_bcc if (address in permitted_set)},
            ),
        ],
        render.html_recipients_defaults_note(settings_url),
    ]

    custom: dict[str, htm.Element | htm.VoidElement] = {
        "email_to": recipient_radios,
        "subject": custom_subject_widget,
        "body": custom_body_widget,
    }
    skip = ["email_cc", "email_bcc"]

    if concern_groups:
        custom["concerns_noted"] = render.html_concerns_noted_checkboxes(concern_groups, checked=submitted_concerns)
    else:
        skip.append("concerns_noted")

    if second_round_recipients:
        default_second_round = second_round_recipients[0]
        custom["second_round_email_to"] = htm.div[
            render.html_recipients_to_radios(
                second_round_recipients,
                default_to=default_second_round,
                field_name="second_round_email_to",
            ),
        ]
    else:
        skip.append("second_round_email_to")

    if vote_mode == sql.VoteMode.TRUSTED:
        if podling_vote_round == 1:
            vote_label = "first round vote"
        elif podling_vote_round == 2:
            vote_label = "second round vote"
        else:
            vote_label = "vote"
        custom["notify_when_finished"] = htm.div[
            htpy.input(
                type="checkbox",
                name="notify_when_finished",
                id="notify_when_finished",
                value="on",
                class_="form-check-input",
            ),
            htm.div(".form-text.text-muted.mt-1")[
                f"If enabled, ATR will send a reminder to {asf_uid}@apache.org when the {vote_label} finishes.",
            ],
        ]
        if podling_vote_round is None:
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
                    f"If enabled, ATR will resolve the {vote_label} automatically, "
                    "using only ATR ballots, when the voting period ends.",
                ],
            ]
        else:
            skip.append("automatic_resolve_when_finished")
    else:
        skip.append("notify_when_finished")
        skip.append("automatic_resolve_when_finished")

    _add_automatic_publish_fields(custom, skip, publish_eligible, vote_mode, podling_vote_round)

    download_suffix_default = str(default_download_path_suffix) if (default_download_path_suffix is not None) else ""
    vote_form = await form.render(
        model_cls=shared.voting.StartVotingForm,
        submit_label="Send vote email",
        cancel_url=cancel_url,
        defaults={
            "vote_duration": min_hours,
            "vote_mode": vote_mode,
            "subject_template_hash": subject_template_hash,
            "body": default_body,
            "rendered_revision": revision_number,
            "download_path_suffix": download_suffix_default,
        },
        custom=custom,
        skip=skip,
        flash_error_data=flash_data,
    )
    page.append(vote_form)

    preview_url = util.as_url(
        post.voting.body_preview,
        project_key=release.project.key,
        version_key=release.version,
    )
    page.append(htpy.div("#vote-body-config.d-none", data_preview_url=preview_url))

    return page.collect()


def _render_subject_field(default_subject: str, project_key: str) -> htm.Element:
    settings_url = util.as_url(projects.view, project_key=project_key) + "#start_vote_subject"
    return htm.div[
        htpy.input(
            type="text",
            name="subject",
            id="subject",
            value=default_subject,
            readonly=True,
            **{"class": "form-control bg-light"},
        ),
        htm.div(".form-text.text-muted.mt-2")[
            "The subject is computed from the template when the email is sent. ",
            "To edit the template, go to the ",
            htm.a(href=settings_url)["project settings"],
            ".",
        ],
    ]
