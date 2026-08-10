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
import markupsafe
import quart

# TODO: Improve upon the routes_release pattern
import atr.blueprints.get as get
import atr.config as config
import atr.construct as construct
import atr.db.interaction as interaction
import atr.form as form
import atr.get.projects as projects
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected(
    session: web.Committer,
    _announce: Literal["announce"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str | web.WerkzeugResponse:
    """
    URL: /announce/<project_key>/<version_key>
    Allow the user to announce a release preview.
    """
    await session.prevent_confusing_ui_display(project_key)
    release = await _get_page_data(session, project_key, version_key)

    latest_revision_number = release.latest_revision_number
    if latest_revision_number is None:
        return await session.redirect(
            projects.view,
            error="No revisions exist for this release.",
            name=project_key,
        )

    # Get the templates from the release policy
    default_subject_template = await construct.announce_release_subject_default(project_key)
    default_body_template = await construct.announce_release_default(project_key)
    subject_template_hash = construct.template_hash(default_subject_template)

    if (committee := release.project.committee) is None:
        raise ValueError("Release has no committee")

    # Expand the templates
    options = construct.AnnounceReleaseOptions(
        asfuid=session.uid,
        fullname=session.fullname,
        project_key=project_key,
        version_key=version_key,
        revision_number=release.safe_latest_revision_number,
    )
    default_subject, default_body = await construct.announce_release_subject_and_body(
        default_subject_template, default_body_template, options
    )

    permitted_recipients = util.permitted_announce_recipients(
        session.uid, committee_key=util.unwrap(committee.key), project=release.project
    )

    embargo_message = None
    if release.is_embargoed:
        embargo_message = await _embargo_message(release)

    content = await _render_page(
        release=release,
        permitted_recipients=permitted_recipients,
        default_subject=default_subject,
        subject_template_hash=subject_template_hash,
        default_body=default_body,
        embargo_message=embargo_message,
    )

    return await template.blank(
        title=f"Announce and publish {release.project.display_name} {release.version}",
        description=f"Announce and publish {release.project.display_name} {release.version} as a release.",
        content=content,
        javascripts=["announce-confirm"],
    )


async def _embargo_message(release: sql.Release) -> str:
    if any((not d.staging) and (not d.pending) for d in release.distributions):
        return (
            "This is an expedited security release. The embargo was already lifted when the release was"
            " distributed on a third party platform. Submitting this form sends the public announcement."
            " This action is not reversible."
        )
    completed = await interaction.release_completed_svn_publish_task(release.safe_project_key, release.safe_version_key)
    if completed is not None:
        return (
            "This is an expedited security release. The embargo was already lifted when the release files"
            " were published to the public SVN distribution area. Submitting this form sends the public"
            " announcement. This action is not reversible."
        )
    in_flight = await interaction.release_in_flight_svn_publish_task(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    )
    if in_flight is not None:
        return (
            "This is an expedited security release. The release files are currently being published to the"
            " public SVN distribution area, which breaks the embargo. Submitting this form sends the public"
            " announcement. This action is not reversible."
        )
    return (
        "This is an expedited security release, and is embargoed. The embargo is broken when the release"
        " files are published to SVN, when the release is distributed on a third party platform, or when"
        " this form is submitted, whichever happens first. Please ensure that you have the authority to"
        " lift the embargo before proceeding. This action is not reversible."
    )


async def _get_page_data(
    session: web.Committer, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> sql.Release:
    release = await session.release(
        project_key,
        version_key,
        with_committee=True,
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        with_distributions=True,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    return release


def _missing_distributions_message(release: sql.Release) -> str:
    policy = release.release_policy or release.project.release_policy
    if not (policy and policy.file_tag_mappings):
        return ""
    published = [d.platform.value.gh_slug for d in release.distributions if (not d.staging) and (not d.pending)]
    missing = [tag for tag in policy.file_tag_mappings if tag not in published]
    if not missing:
        return ""
    return (
        f"This release cannot be announced until the following distributions have been recorded: {', '.join(missing)}"
    )


async def _publication_pending_message(release: sql.Release) -> str:
    completed = await interaction.release_completed_svn_publish_task_for_revision(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    )
    if completed is not None:
        return ""
    return "This release cannot be announced until it has been published to SVN."


def _recipients_documentation() -> str | None:
    if config.get().ATR_STATUS != "ALPHA":
        return None
    return (
        "Note: The options to send to the user-tests mailing list and yourself are provided for"
        " testing purposes only, and will not be available in the finished version of ATR."
    )


async def _render_announce_form(
    page: htm.Block,
    release: sql.Release,
    *,
    permitted_recipients: list[str],
    subject_template_hash: str,
    default_subject: str,
    default_body: str,
) -> None:
    custom_subject_widget = _render_subject_field(default_subject, release.project.key)
    custom_body_widget = _render_body_field(default_body, release.project.key)
    policy_to, policy_cc, policy_bcc = release.project.policy_recipients(sql.RecipientAction.ANNOUNCE)
    permitted_set = set(permitted_recipients)
    fallback_to = permitted_recipients[0] if permitted_recipients else None
    default_to = policy_to if (policy_to in permitted_set) else fallback_to
    settings_url = util.as_url(projects.view, project_key=release.project.key) + "?tab=finish#email_to"
    recipient_radios = htm.div[
        render.html_recipients_to_radios(
            permitted_recipients,
            default_to=default_to,
            documentation=_recipients_documentation(),
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

    prior_release_version = ""
    if release.archive_prior_release and release.project.policy_auto_archive_prior_release:
        prior = await interaction.prior_release_for_archive(release.project, release.version)
        if prior is not None:
            prior_release_version = prior.version

    defaults_dict = {
        "revision_number": release.unwrap_revision_number,
        "subject_template_hash": subject_template_hash,
        "body": default_body,
        "auto_archive": bool(release.archive_prior_release),
        "auto_archive_release": prior_release_version or "",
    }

    skip = ["email_cc", "email_bcc"]
    if release.project.download_page:
        skip.append("download_page")
    if not quart.request.args.get("unreachable"):
        skip.append("announce_unreachable")
    if not prior_release_version:
        # Prior release version will only be set if the options for archival are True *and* we found a release
        skip.extend(["auto_archive", "auto_archive_release"])
    await form.render_block(
        page,
        model_cls=shared.announce.AnnounceForm,
        action=util.as_url(post.announce.selected, project_key=release.project.key, version_key=release.version),
        submit_label="Publish & announce",
        defaults=defaults_dict,
        custom={
            "email_to": recipient_radios,
            "subject": custom_subject_widget,
            "body": custom_body_widget,
        },
        skip=skip,
        form_classes=".atr-canary.py-4.px-5.mb-4.border.rounded",
        border=True,
        wider_widgets=True,
    )


def _render_body_field(default_body: str, project_key: str) -> htm.Element:
    """Render the body textarea with a link to edit the template."""
    textarea = htpy.textarea(
        "#body.form-control.font-monospace",
        name="body",
        rows="12",
    )[default_body]

    settings_url = util.as_url(projects.view, project_key=project_key) + "?tab=finish#announce_release_template"
    link = htm.div(".form-text.text-muted.mt-2")[
        "To edit the template, go to the ",
        htm.a(href=settings_url)["project settings"],
        ".",
    ]

    return htm.div[textarea, link]


async def _render_page(
    release: sql.Release,
    permitted_recipients: list[str],
    default_subject: str,
    subject_template_hash: str,
    default_body: str,
    embargo_message: str | None,
) -> htm.Element:
    """Render the announce page."""
    page = htm.Block()

    page_styles = """
        .page-preview-meta-item::after {
            content: "•";
            margin-left: 1rem;
            color: #ccc;
        }
        .page-preview-meta-item:last-child::after {
            content: none;
        }
    """
    page.style[markupsafe.Markup(page_styles)]

    render.html_nav_phase(page, release.project.key, release.version, staging=False)

    page.h1[
        "Publish ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]
    page.append(_render_release_card(release))
    page.h2["Publish and announce this release"]

    if banner := render.archived_project_banner(release.project, "Release actions are disabled."):
        page.append(banner)

    announce_msg = _missing_distributions_message(release)
    if not announce_msg:
        announce_msg = await _publication_pending_message(release)
    if announce_msg:
        page.p[htm.strong[announce_msg]]
        return page.collect()

    page.p["This form will send an announcement to the selected recipients."]
    if embargo_message is not None:
        page.div(".p-3.mb-4.bg-danger-subtle.border.border-danger.rounded")[embargo_message]
    await _render_announce_form(
        page,
        release,
        permitted_recipients=permitted_recipients,
        subject_template_hash=subject_template_hash,
        default_subject=default_subject,
        default_body=default_body,
    )
    return page.collect()


def _render_release_card(release: sql.Release) -> htm.Element:
    """Render the release information card."""
    card = htm.div(f"#{release.key}.card.mb-4.shadow-sm")[
        htm.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
            htm.h3(".card-title.mb-0")["About this release preview"],
            render.embargoed_badge(release),
        ],
        htm.div(".card-body")[
            htm.div(".d-flex.flex-wrap.gap-3.pb-1.text-secondary.fs-6")[
                htm.span(".page-preview-meta-item")[f"Revision: {release.latest_revision_number}"],
                htm.span(".page-preview-meta-item")[f"Created: {release.created.strftime('%Y-%m-%d %H:%M:%S UTC')}"],
            ],
        ],
    ]
    return card


def _render_subject_field(default_subject: str, project_key: str) -> htm.Element:
    settings_url = util.as_url(projects.view, project_key=project_key) + "#announce_release_subject"
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
