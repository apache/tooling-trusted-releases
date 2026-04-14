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

import atr.blueprints.get as get
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
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected_revision(
    session: web.Committer,
    _voting: Literal["voting"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
) -> web.WerkzeugResponse | str:
    """
    URL: /voting/<project_key>/<version_key>/<revision>
    """
    await session.check_access(project_key)
    async with db.session() as data:
        match await interaction.release_ready_for_vote(
            session, project_key, version_key, revision, data, manual_vote=False
        ):
            case str() as error:
                return await session.redirect(
                    compose.selected,
                    error=error,
                    project_key=str(project_key),
                    version_key=str(version_key),
                    revision=str(revision),
                )
            case (release, committee):
                pass

        permitted_recipients = util.permitted_voting_recipients(session.uid, committee.key)
        second_round_recipients = (
            util.permitted_podling_second_round_recipients(session.uid) if committee.is_podling else []
        )

        min_hours = 72
        if release.project.release_policy and (release.project.release_policy.min_hours is not None):
            min_hours = release.project.release_policy.min_hours

        default_subject_template = await construct.start_vote_subject_default(project_key)
        default_body_template = await construct.start_vote_default(project_key)
        subject_template_hash = construct.template_hash(default_subject_template)

        options = construct.StartVoteOptions(
            asfuid=session.uid,
            fullname=session.fullname,
            project_key=project_key,
            version_key=release.safe_version_key,
            revision_number=revision,
            vote_duration=min_hours,
        )
        default_subject, default_body = await construct.start_vote_subject_and_body(
            default_subject_template, default_body_template, options
        )

        keys_warning = await _check_keys_warning(committee)

        content = await _render_page(
            release=release,
            revision_number=str(revision),
            permitted_recipients=permitted_recipients,
            second_round_recipients=second_round_recipients,
            default_subject=default_subject,
            subject_template_hash=subject_template_hash,
            default_body=default_body,
            min_hours=min_hours,
            keys_warning=keys_warning,
        )

        return await template.blank(
            title=f"Start voting on {release.project.short_display_name} {release.version}",
            content=content,
            javascripts=["vote-body-duration"],
        )


async def _check_keys_warning(committee: sql.Committee) -> bool:
    if committee.is_podling:
        keys_file_path = paths.get_downloads_dir() / "incubator" / committee.key / "KEYS"
    else:
        keys_file_path = paths.get_downloads_dir() / committee.key / "KEYS"

    return not await aiofiles.os.path.isfile(keys_file_path)


def _render_body_field(default_body: str, project_key: str) -> htm.Element:
    """Render the body textarea with a link to edit the template."""
    textarea = htpy.textarea(
        "#body.form-control.font-monospace",
        name="body",
        rows="12",
    )[default_body]

    settings_url = util.as_url(projects.view, project_key=project_key) + "#start_vote_template"
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
    default_subject: str,
    subject_template_hash: str,
    default_body: str,
    min_hours: int,
    keys_warning: bool,
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
    default_to = permitted_recipients[0] if permitted_recipients else None
    to_radios = htm.div[
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
            render.html_recipients_cc_bcc_table(permitted_recipients),
        ],
    ]

    custom: dict[str, htm.Element | htm.VoidElement] = {
        "email_to": to_radios,
        "subject": custom_subject_widget,
        "body": custom_body_widget,
    }
    skip = ["email_cc", "email_bcc"]

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

    vote_form = await form.render(
        model_cls=shared.voting.StartVotingForm,
        submit_label="Send vote email",
        cancel_url=cancel_url,
        defaults={
            "vote_duration": min_hours,
            "subject_template_hash": subject_template_hash,
            "body": default_body,
        },
        custom=custom,
        skip=skip,
    )
    page.append(vote_form)

    preview_url = util.as_url(
        post.voting.body_preview,
        project_key=release.project.key,
        version_key=release.version,
        revision_number=revision_number,
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
