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

# TODO: Improve upon the routes_release pattern
import atr.blueprints.get as get
import atr.config as config
import atr.construct as construct
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
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
) -> str | web.WerkzeugResponse:
    """
    URL: /announce/<project_name>/<version_name>
    Allow the user to announce a release preview.
    """
    await session.check_access(project_name)
    release = await _get_page_data(session, project_name, version_name)

    latest_revision_number = release.latest_revision_number
    if latest_revision_number is None:
        return await session.redirect(
            projects.view,
            error="No revisions exist for this release.",
            name=project_name,
        )

    # Get the templates from the release policy
    default_subject_template = await construct.announce_release_subject_default(project_name)
    default_body_template = await construct.announce_release_default(project_name)
    subject_template_hash = construct.template_hash(default_subject_template)

    # Expand the templates
    options = construct.AnnounceReleaseOptions(
        asfuid=session.uid,
        fullname=session.fullname,
        project_name=project_name,
        version_name=version_name,
        revision_number=latest_revision_number,
    )
    default_subject, default_body = await construct.announce_release_subject_and_body(
        default_subject_template, default_body_template, options
    )

    # The download path suffix can be changed
    # The defaults depend on whether the project is top level or not
    if (committee := release.project.committee) is None:
        raise ValueError("Release has no committee")
    top_level_project = release.project.name == util.unwrap(committee.name)
    # These defaults are as per #136, but we allow the user to change the result
    default_download_path_suffix = "/" if top_level_project else f"/{release.project.name}-{release.version}/"

    # This must NOT end with a "/"
    description_download_prefix = f"https://{config.get().APP_HOST}/downloads"
    if committee.is_podling:
        description_download_prefix += "/incubator"
    description_download_prefix += f"/{committee.name}"

    permitted_recipients = util.permitted_announce_recipients(session.uid)
    mailing_list_choices = sorted([(recipient, recipient) for recipient in permitted_recipients])

    content = await _render_page(
        release=release,
        mailing_list_choices=mailing_list_choices,
        default_subject=default_subject,
        subject_template_hash=subject_template_hash,
        default_body=default_body,
        default_download_path_suffix=default_download_path_suffix,
        download_path_description=f"The URL will be {description_download_prefix} plus this suffix",
    )

    return await template.blank(
        title=f"Announce and distribute {release.project.display_name} {release.version}",
        description=f"Announce and distribute {release.project.display_name} {release.version} as a release.",
        content=content,
        javascripts=["announce-confirm"],
    )


async def _get_page_data(
    session: web.Committer, project_name: safe.ProjectName, version_name: safe.VersionName
) -> sql.Release:
    release = await session.release(
        project_name,
        version_name,
        with_committee=True,
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        with_distributions=True,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    return release


def _render_body_field(default_body: str, project_name: str) -> htm.Element:
    """Render the body textarea with a link to edit the template."""
    textarea = htpy.textarea(
        "#body.form-control.font-monospace",
        name="body",
        rows="12",
    )[default_body]

    settings_url = util.as_url(projects.view, project_name=project_name) + "#announce_release_template"
    link = htm.div(".form-text.text-muted.mt-2")[
        "To edit the template, go to the ",
        htm.a(href=settings_url)["project settings"],
        ".",
    ]

    return htm.div[textarea, link]


def _render_download_path_field(default_value: str, description: str) -> htm.Element:
    """Render the download path suffix field with custom help text."""
    base_text = description.split(" plus this suffix")[0] if (" plus this suffix" in description) else description
    return htm.div[
        htpy.input(
            "#download_path_suffix.form-control",
            type="text",
            name="download_path_suffix",
            value=default_value,
        ),
        htpy.div(".form-text.text-muted.mt-2", data_base_text=base_text)[description],
    ]


def _render_mailing_list_with_warning(choices: list[tuple[str, str]], default_value: str) -> htm.Element:
    """Render the mailing list radio buttons with a warning card."""
    container = htm.Block(htm.div)

    # Radio buttons
    radio_container = htm.div(".d-flex.flex-wrap.gap-2.mb-3")
    radio_buttons = []
    for value, label in choices:
        radio_id = f"mailing_list_{value}"
        radio_attrs = {
            "type": "radio",
            "name": "mailing_list",
            "value": value,
        }
        if value == default_value:
            radio_attrs["checked"] = ""

        radio_buttons.append(
            htm.div(".form-check")[
                htpy.input(f"#{radio_id}.form-check-input", **radio_attrs),
                htpy.label(".form-check-label", for_=radio_id)[label],
            ]
        )
    container.append(radio_container[radio_buttons])

    # Warning card
    warning_card = htm.div(".card.bg-warning-subtle.mb-3")[
        htm.span(".card-body.p-3")[
            htpy.i(".bi.bi-exclamation-triangle.me-1"),
            htm.strong["TODO: "],
            "The limited options above are provided for testing purposes. In the finished version of ATR, "
            "you will be able to send to your own specified mailing lists.",
        ]
    ]
    container.append(warning_card)

    return container.collect()


async def _render_page(
    release: sql.Release,
    mailing_list_choices: list[tuple[str, str]],
    default_subject: str,
    subject_template_hash: str,
    default_body: str,
    default_download_path_suffix: str,
    download_path_description: str,
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

    render.html_nav_phase(page, release.project.name, release.version, staging=False)

    page.h1[
        "Announce ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]
    page.append(_render_release_card(release))
    page.h2["Announce this release"]

    announce_msg = ""
    policy = release.release_policy or release.project.release_policy
    if policy and policy.file_tag_mappings:
        missing = []
        tags = policy.file_tag_mappings.keys()
        distributions = [d.platform.value.gh_slug for d in release.distributions if (not d.staging) and (not d.pending)]
        for tag in tags:
            if tag not in distributions:
                missing.append(tag)
        if missing:
            announce_msg = f"This release cannot be announced until the following distributions have been recorded: {
                ', '.join(missing)
            }"

    if not announce_msg:
        page.p[f"This form will send an announcement to the ASF {util.USER_TESTS_ADDRESS} mailing list."]

        custom_subject_widget = _render_subject_field(default_subject, release.project.name)
        custom_body_widget = _render_body_field(default_body, release.project.name)
        custom_mailing_list_widget = _render_mailing_list_with_warning(mailing_list_choices, util.USER_TESTS_ADDRESS)

        # Custom widget for download_path_suffix with custom documentation
        download_path_widget = _render_download_path_field(default_download_path_suffix, download_path_description)

        defaults_dict = {
            "revision_number": release.unwrap_revision_number,
            "subject_template_hash": subject_template_hash,
            "body": default_body,
        }

        form.render_block(
            page,
            model_cls=shared.announce.AnnounceForm,
            action=util.as_url(post.announce.selected, project_name=release.project.name, version_name=release.version),
            submit_label="Send announcement email",
            defaults=defaults_dict,
            custom={
                "subject": custom_subject_widget,
                "body": custom_body_widget,
                "mailing_list": custom_mailing_list_widget,
                "download_path_suffix": download_path_widget,
            },
            form_classes=".atr-canary.py-4.px-5.mb-4.border.rounded",
            border=True,
            wider_widgets=True,
        )
    else:
        page.p[htm.strong[announce_msg]]

    return page.collect()


def _render_release_card(release: sql.Release) -> htm.Element:
    """Render the release information card."""
    card = htm.div(f"#{release.name}.card.mb-4.shadow-sm")[
        htm.div(".card-header.bg-light")[htm.h3(".card-title.mb-0")["About this release preview"]],
        htm.div(".card-body")[
            htm.div(".d-flex.flex-wrap.gap-3.pb-1.text-secondary.fs-6")[
                htm.span(".page-preview-meta-item")[f"Revision: {release.latest_revision_number}"],
                htm.span(".page-preview-meta-item")[f"Created: {release.created.strftime('%Y-%m-%d %H:%M:%S UTC')}"],
            ],
        ],
    ]
    return card


def _render_subject_field(default_subject: str, project_name: str) -> htm.Element:
    settings_url = util.as_url(projects.view, project_name=project_name) + "#announce_release_subject"
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
