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


from collections.abc import Sequence
from typing import Final, Literal

import markupsafe
import quart

import atr.blueprints.get as get
import atr.config as config
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.form as form
import atr.get.announce as announce
import atr.get.distribution as distribution
import atr.get.docs as docs
import atr.get.download as download
import atr.get.file as file
import atr.get.root as root
import atr.htm as htm
import atr.mapping as mapping
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.shared.activity as activity
import atr.strings as strings
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web

_DOWNLOAD_PAGE_TITLE: Final = "Check that the project download page links to this release"
_DOWNLOAD_PAGE_WAITING_TITLE: Final = "You can check the download page once this release is published to SVN dist"


@get.typed
async def downloads(
    session: web.Committer,
    _finish: Literal["finish"],
    _downloads: Literal["downloads"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
) -> web.QuartResponse:
    await session.prevent_confusing_ui_display(project_key, flash_admin_warning=False)
    release = await session.release(
        project_key,
        version_key,
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        latest_revision_number=revision_number,
        with_distributions=True,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    message = await shared.announce.blocked_message(release)
    svn_html = None
    if user.is_committee_member(release.committee, session.uid) or user.is_release_manager(
        release.committee, session.uid
    ):
        svn = htm.Block()
        await _render_svn_publish(svn, release)
        svn_html = str(svn.collect())
    return quart.jsonify(
        message=message,
        ready=not message,
        svn_html=svn_html,
        download_page_ready=await _download_page_checkable(release),
    )


@get.typed
async def selected(
    session: web.Committer,
    _finish: Literal["finish"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse | str:
    """
    URL: /finish/<project_key>/<version_key>
    Finish a release preview.
    """
    await session.prevent_confusing_ui_display(project_key)
    try:
        (release, tasks) = await _get_page_data(project_key, version_key)
    except ValueError:
        async with db.session() as data:
            release_fallback = await data.release(
                project_key=str(project_key),
                version=str(version_key),
                _committee=True,
            ).get()
            if release_fallback:
                return await mapping.release_as_redirect(session, release_fallback)

        await quart.flash("Preview revision directory not found.", "error")
        return await session.redirect(root.index)

    return await _render_page(
        session=session,
        release=release,
        distribution_tasks=tasks,
        announce_disable_message=await shared.announce.blocked_message(release),
        download_page_checkable=await _download_page_checkable(release),
        download_page_check=quart.session.pop(f"{shared.finish.DOWNLOAD_PAGE_CHECK_SESSION_KEY}:{release.key}", None),
    )


def _checks_held_summary(release: sql.Release) -> str:
    expected = len(shared.distribution.release_rows(release.distributions))
    if expected == 0:
        return "You can record third-party distributions once this release is published to SVN dist."
    noun = util.plural(expected, "third-party distribution")
    return (
        f"ATR will look for {noun} once this release is published to SVN dist. Looking sooner would reveal"
        " the version to those platforms, so you cannot review them until then."
    )


async def _download_page_checkable(release: sql.Release) -> bool:
    """Whether ATR knows which files were published, which the download page check compares against."""
    async with db.session() as data:
        artifacts = await data.artifact(release_key=release.key, managed=True, _unannounced=True).all()
    return len(artifacts) > 0


async def _get_page_data(
    project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> tuple[sql.Release, Sequence[sql.Task]]:
    """Get all the data needed to render the finish page."""
    async with db.session() as data:
        via = sql.validate_instrumented_attribute
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
            _distributions=True,
        ).demand(errors.ASFQuartException(errors.RELEASE_NOT_FOUND, errorcode=404))
        tasks = [
            t
            for t in (
                await data.task(
                    project_key=str(project_key),
                    version_key=str(version_key),
                    revision_number=release.latest_revision_number,
                    task_type=sql.TaskType.DISTRIBUTION_WORKFLOW,
                    _workflow=True,
                )
                .order_by(sql.sqlmodel.desc(via(sql.Task.started)))
                .all()
            )
            if t.task_args.get("phase") == "finish"
        ]

    if release.phase != sql.ReleasePhase.RELEASE_PREVIEW:
        raise ValueError("Release is not in preview phase")

    return release, tasks


async def _render_page(
    session: web.Committer,
    release: sql.Release,
    distribution_tasks: Sequence[sql.Task],
    announce_disable_message: str,
    download_page_checkable: bool,
    download_page_check: str | None,
) -> str:
    """Render the finish page using htm.py."""
    page = htm.Block()

    render.html_nav(
        page,
        back_url=util.as_url(root.index),
        back_anchor="Select a release",
        phase="FINISH",
    )

    # Page heading
    page.h1[
        "Finish ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]

    if banner := render.archived_project_banner(release.project, errors.RELEASE_ACTIONS_DISABLED):
        page.append(banner)

    if download_page_check is not None:
        page.append(htm.div[markupsafe.Markup(download_page_check)])

    # Release info card
    queued = await interaction.release_queued_announcement(release.safe_project_key, release.safe_version_key)
    # The announcement can be written ahead of time, unless one is waiting already
    can_queue = bool(announce_disable_message) and (queued is None) and (not release.expedited)
    page.append(
        _render_release_card(
            release, announce_disable_message, can_queue=can_queue, download_page_checkable=download_page_checkable
        )
    )
    _render_queued_announcement(page, release, queued)

    proj = release.safe_project_key
    ver = release.safe_version_key
    rev = release.safe_latest_revision_number
    published = (await interaction.release_completed_svn_publish_task_for_revision(proj, ver, rev)) is not None
    if not published:
        published = (await interaction.release_in_flight_svn_publish_task(proj, ver, rev)) is not None

    page.p[
        "On this page you should do three things: ",
        *_render_publish_step(published),
        "; 2. optionally record third party distributions that you made; and then, when ready, 3. use ",
        htm.strong["Announce"],
        " above to complete the process.",
    ]

    if user.is_committee_member(release.committee, session.uid) or user.is_release_manager(
        release.committee, session.uid
    ):
        with page.block(htm.div("#finish-svn-publish")) as svn:
            await _render_svn_publish(svn, release)

    page.h2["Distribute on third party platforms"]
    if release.is_embargoed:
        page.div(".p-3.mb-4.bg-danger-subtle.border.border-danger.rounded")[
            "This is an expedited security release, and is embargoed. Distributing on third party platforms"
            " makes the release files public, which breaks the embargo. Please ensure that you have the"
            " authority to lift the embargo before distributing. This action is not reversible."
        ]
    page.p[
        "During this phase you should distribute release artifacts to your package distribution networks "
        "such as Maven Central, PyPI, or Docker Hub."
    ]
    checks_held = await interaction.release_distribution_checks_held(release)
    if checks_held:
        page.p[_checks_held_summary(release)]
    else:
        page.p[shared.distribution.summary(release.distributions)]

    if len(distribution_tasks) > 0:
        page.append(
            shared.distribution.render_distribution_tasks(
                distribution_tasks,
                util.as_url(selected, project_key=release.project.key, version_key=release.version),
            )
        )

    if not checks_held:
        page.p(".mb-4")[
            htm.a(
                ".btn.btn-primary",
                href=util.as_url(distribution.list_get, project_key=release.project.key, version_key=release.version),
            )["Review distributions"]
        ]

    if user.is_participant_for_committee(release.committee, session.participant_committees):
        page.h2["Inactivity"]
        activity_form = await form.render(
            model_cls=form.Empty,
            action=util.as_url(post.release.activity, project_key=release.project.key, version_key=release.version),
            submit_label=strings.RESET_INACTIVITY_CLOCK_BUTTON,
            submit_classes="btn-outline-primary",
            pre_submit=activity.inactivity_form_intro(release, action="flagged", noun="preview"),
        )
        page.div(".mb-4")[activity_form]

    content = page.collect()

    return await template.blank(
        title=f"Finish {release.project.display_name} {release.version} ~ ATR",
        description=f"Finish {release.project.display_name} {release.version} as a release preview.",
        content=content,
        javascripts=["finish-downloads", "publish-location-preview"],
    )


def _render_publish_step(published: bool) -> list[htm.Element | str]:
    # Resolving a vote can publish automatically, so don't ask for it again
    done = " (already requested, see below)" if published else " if you did not already"
    if util.svn_publish_target() is util.SvnPublishTarget.RELEASE:
        return ["1. publish to SVN dist ", htm.code["dist/release"], done]
    return [
        "1. publish to SVN dist ",
        htm.code["dist/atr"],
        done,
        ", and then ",
        htm.a(href=util.as_url(docs.page, path="promoting-to-release"))[
            "manually ",
            htm.code["svn mv"],
            " the results",
        ],
        " to ",
        htm.code["dist/release"],
    ]


def _render_queued_announcement(page: htm.Block, release: sql.Release, queued: sql.Task | None) -> None:
    if queued is not None:
        page.append(announce.render_queued(release, queued))


def _render_release_card(
    release: sql.Release,
    announce_disable_message: str,
    *,
    can_queue: bool = False,
    download_page_checkable: bool = True,
) -> htm.Element:
    """Render the release information card."""
    # One button, which queues the announcement while the release can't be announced yet
    announce_disabled = bool(announce_disable_message) and (not can_queue)
    announce_classes = ".btn-outline-success" if can_queue else ".btn-success"
    if announce_disabled:
        announce_classes += ".disabled"
    announce_url = util.as_url(announce.selected, project_key=release.project.key, version_key=release.version)
    card = htm.div(".card.mb-4.shadow-sm", id=release.key)[
        htm.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
            htm.h3(".card-title.mb-0")["About this release preview"],
            render.embargoed_badge(release),
        ],
        htm.div(".card-body")[
            htm.div(".d-flex.flex-wrap.gap-3.pb-3.mb-3.border-bottom.text-secondary.fs-6")[
                htm.span(".page-preview-meta-item")[f"Revision: {release.latest_revision_number}"],
                htm.span(".page-preview-meta-item")[f"Created: {release.created.strftime('%Y-%m-%d %H:%M:%S UTC')}"],
            ],
            htm.div[
                htm.a(
                    ".btn.btn-primary.me-2",
                    title="Download all files",
                    href=util.as_url(
                        download.all_selected,
                        project_key=release.project.key,
                        version_key=release.version,
                    ),
                )[
                    htm.icon("download"),
                    " Download all files",
                ],
                htm.a(
                    ".btn.btn-secondary.me-2",
                    title=f"Show files for {release.key}",
                    href=util.as_url(
                        file.selected,
                        project_key=release.project.key,
                        version_key=release.version,
                    ),
                )[
                    htm.icon("archive"),
                    " Show files",
                ],
                htm.a(
                    f"#finish-announce.btn{announce_classes}.me-2",
                    title=f"Announce {release.key}",
                    href=announce_url if (not announce_disabled) else None,
                    data_announce_url=announce_url,
                    data_when_ready="true" if can_queue else None,
                    aria_disabled="true" if announce_disabled else "false",
                    tabindex=-1 if announce_disabled else None,
                )[
                    htm.icon("check-circle"),
                    htm.span("#finish-announce-label")[" Announce when ready" if can_queue else " Announce"],
                ],
                htm.form(
                    ".d-inline",
                    method="post",
                    action=util.as_url(
                        post.finish.download_page, project_key=release.project.key, version_key=release.version
                    ),
                )[
                    form.csrf_input(),
                    htm.button(
                        "#finish-download-page.btn.btn-outline-secondary.me-2",
                        type="submit",
                        title=_DOWNLOAD_PAGE_TITLE if download_page_checkable else _DOWNLOAD_PAGE_WAITING_TITLE,
                        data_ready_title=_DOWNLOAD_PAGE_TITLE,
                        disabled=not download_page_checkable,
                    )[
                        htm.icon("link-45deg"),
                        " Check download page",
                    ],
                ],
            ],
            htm.div(
                "#finish-publication-status.text-danger.mt-2",
                role="status",
                data_status_url=util.as_url(
                    downloads,
                    project_key=release.project.key,
                    version_key=release.version,
                    revision_number=release.safe_latest_revision_number,
                ),
            )[
                htm.p("#finish-publication-message.mb-0", hidden=not announce_disable_message)[
                    announce_disable_message
                ],
                htm.p(".mb-0")["Local SVN repository in use. Download checking is disabled."]
                if config.svn_publish_kind() is config.SvnPublishKind.LOCAL_REPOSITORY
                else None,
                htm.p("#finish-publication-refresh.mb-0", hidden=True)["Refreshing every 30s."],
            ],
        ],
    ]
    return card


async def _render_svn_publish(page: htm.Block, release: sql.Release) -> None:
    proj = release.safe_project_key
    ver = release.safe_version_key
    rev = release.safe_latest_revision_number
    page.h2["Publish to ASF Distribution Area"]
    completed = await interaction.release_completed_svn_publish_task_for_revision(proj, ver, rev)
    if completed is not None:
        _render_svn_publish_completed(page, release, completed)
        return
    in_flight = await interaction.release_in_flight_svn_publish_task(proj, ver, rev)
    if in_flight is not None:
        page.div("#finish-svn-publishing.alert.alert-info.mb-4")[
            htm.p["The release files are being published to SVN dist."],
            htm.a(
                ".btn.btn-primary",
                href=util.as_url(selected, project_key=release.project.key, version_key=release.version),
            )["Refresh"],
        ]
        return
    failed = await interaction.release_latest_failed_svn_publish_task(proj, ver, rev)
    if failed is not None:
        page.div(".alert.alert-danger.mb-4")[
            f"The most recent attempt to publish to SVN dist failed: {failed.error or 'unknown error'}"
        ]
    if release.is_embargoed:
        page.div(".p-3.mb-4.bg-danger-subtle.border.border-danger.rounded")[
            "This is an expedited security release, and is embargoed. Publishing to SVN dist copies the"
            " release files to the public distribution area, which breaks the embargo. Please ensure"
            " that you have the authority to lift the embargo before publishing. This action is not"
            " reversible."
        ]
    location, mismatch = await construct.publish_location_for_release(
        release, await construct.effective_download_path_suffix(release)
    )
    entered = await _svn_download_path_default(release)
    await form.render_block(
        page,
        shared.finish.PublishToSvnForm,
        action=util.as_url(post.finish.selected, project_key=release.project.key, version_key=release.version),
        defaults={
            "download_path_suffix": entered,
            "revision_number": release.latest_revision_number,
        },
        notes={
            "download_path_suffix": render.publish_location_note_for_release(
                release, entered, location, mismatch, confirm_mismatch=True
            )
        },
        prefixes={"download_path_suffix": construct.publish_location_directory(release.committee)},
        submit_label=_svn_publish_label(),
    )


def _render_svn_publish_completed(page: htm.Block, release: sql.Release, completed: sql.Task) -> None:
    revision = _svn_publish_revision(completed)
    text = f"Published to SVN dist as r{revision}" if (revision is not None) else "Published to SVN dist"
    url = _svn_publish_url(release, completed)
    if url is None:
        page.div(".alert.alert-success.mb-4")[text]
        return
    page.div(".alert.alert-success.mb-4")[text, " synced soon to ", htm.a(href=url)[url]]


async def _svn_download_path_default(release: sql.Release) -> str:
    suffix = await construct.effective_download_path_suffix(release)
    return str(suffix) if suffix is not None else ""


def _svn_publish_label() -> str:
    if util.svn_publish_target() is util.SvnPublishTarget.RELEASE:
        return "Publish to SVN dist/release"
    return "Publish to SVN"


def _svn_publish_revision(completed: sql.Task) -> int | None:
    result = completed.result
    if isinstance(result, results.SvnPublish):
        return result.svn_revision
    if isinstance(result, dict):
        candidate = result.get("svn_revision")
        if isinstance(candidate, int):
            return candidate
    return None


def _svn_publish_suffix(completed: sql.Task) -> safe.RelPath | None:
    candidate = completed.task_args.get("download_path_suffix")
    if isinstance(candidate, str) and candidate:
        return safe.RelPath(candidate)
    return None


def _svn_publish_url(release: sql.Release, completed: sql.Task) -> str | None:
    committee = release.project.committee
    if committee is None:
        return None
    try:
        return util.publication_check_url(committee, _svn_publish_suffix(completed), util.DownloadFile.METADATA)
    except ValueError:
        return None
