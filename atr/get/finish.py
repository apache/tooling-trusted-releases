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
from typing import Literal

import asfquart.base as base
import markupsafe
import quart

import atr.blueprints.get as get
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.announce as announce
import atr.get.distribution as distribution
import atr.get.docs as docs
import atr.get.download as download
import atr.get.file as file
import atr.get.root as root
import atr.htm as htm
import atr.mapping as mapping
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.shared.activity as activity
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web


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

    announce_msg = ""
    if release.project.release_policy and release.project.release_policy.file_tag_mappings:
        missing = []
        tags = release.project.release_policy.file_tag_mappings.keys()
        distributions = [d.platform.value.gh_slug for d in release.distributions if (not d.staging) and (not d.pending)]
        for tag in tags:
            if tag not in distributions:
                missing.append(tag)
        if missing:
            announce_msg = f"This release cannot be announced until the following distributions have been recorded: {
                ', '.join(missing)
            }"

    if (not announce_msg) and (release.latest_revision_number is not None):
        completed_publish = await interaction.release_completed_svn_publish_task_for_revision(
            project_key, version_key, release.safe_latest_revision_number
        )
        if completed_publish is None:
            announce_msg = "This release cannot be announced until it has been published to SVN."

    return await _render_page(
        session=session,
        release=release,
        distribution_tasks=tasks,
        announce_disable_message=announce_msg,
    )


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
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))
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
        ]

    if release.phase != sql.ReleasePhase.RELEASE_PREVIEW:
        raise ValueError("Release is not in preview phase")

    return release, tasks


def _render_dist_warning() -> htm.Element:
    """Render the alert about distribution tools."""
    return htm.div(".alert.alert-warning.mb-4", role="alert")[
        htm.p(".fw-semibold.mb-1")["NOTE:"],
        htm.p(".mb-1")[
            "Tools to distribute automatically are still being developed, "
            "you must do this manually at present. Please use the manual record function below to do so.",
        ],
    ]


def _render_distribution_buttons(release: sql.Release) -> htm.Element:
    """Render the distribution tool buttons."""
    return htm.div()[
        htm.p(".mb-1")[
            htm.a(
                ".btn.btn-primary.me-2",
                href=util.as_url(
                    distribution.automate,
                    project_key=release.project.key,
                    version_key=release.version,
                ),
            )["Distribute"],
            htm.a(
                ".btn.btn-secondary.me-2",
                href=util.as_url(
                    distribution.record,
                    project_key=release.project.key,
                    version_key=release.version,
                ),
            )["Record a manual distribution"],
        ],
    ]


def _render_distribution_tasks(release: sql.Release, tasks: Sequence[sql.Task]) -> htm.Element:
    """Render current and failed distribution tasks."""
    failed_tasks = [
        t for t in tasks if (t.status == sql.TaskStatus.FAILED) or (t.workflow and (t.workflow.status == "failed"))
    ]
    in_progress_tasks = [
        t
        for t in tasks
        if (t.status in [sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE])
        or (t.workflow and (t.workflow.status not in ["completed", "success", "failed"]))
    ]

    block = htm.Block()

    if len(failed_tasks) > 0:
        summary = f"{len(failed_tasks)} distribution{'s' if (len(failed_tasks) != 1) else ''} failed for this release"
        block.append(
            htm.div(".alert.alert-danger.mb-3")[
                htm.h3["Failed distributions"],
                htm.details[
                    htm.summary[summary],
                    htm.div[*[_render_task(f) for f in failed_tasks]],
                ],
            ]
        )
    if len(in_progress_tasks) > 0:
        block.append(
            htm.div(".alert.alert-info.mb-3")[
                htm.h3["In-progress distributions"],
                htm.p["One or more automatic distributions are still in-progress:"],
                *[_render_task(f) for f in in_progress_tasks],
                htm.a(
                    ".btn.btn-success.mt-2",
                    href=util.as_url(
                        selected,
                        project_key=release.project.key,
                        version_key=release.version,
                    ),
                )["Refresh"],
            ]
        )
    return block.collect()


async def _render_page(
    session: web.Committer,
    release: sql.Release,
    distribution_tasks: Sequence[sql.Task],
    announce_disable_message: str,
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

    if banner := render.archived_project_banner(release.project, "Release actions are disabled."):
        page.append(banner)

    # Release info card
    page.append(_render_release_card(release, announce_disable_message))

    page.p[
        "On this page you should do three things: ",
        *_render_publish_step(),
        "; 2. optionally record third party distributions that you made; and then, when ready, 3. use ",
        htm.strong["Announce"],
        " above to complete the process.",
    ]

    if user.is_committee_member(release.committee, session.uid) or user.is_release_manager(
        release.committee, session.uid
    ):
        await _render_svn_publish(page, release)

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

    if len(distribution_tasks) > 0:
        page.append(_render_distribution_tasks(release, distribution_tasks))

    page.append(_render_dist_warning())
    page.append(_render_distribution_buttons(release))

    if user.is_participant_for_committee(release.committee, session.participant_committees):
        page.h2["Inactivity"]
        activity_form = await form.render(
            model_cls=form.Empty,
            action=util.as_url(post.release.activity, project_key=release.project.key, version_key=release.version),
            submit_label="Reset inactivity clock",
            submit_classes="btn-outline-primary",
            pre_submit=activity.inactivity_form_intro(release, action="flagged", noun="preview"),
        )
        page.div(".mb-4")[activity_form]

    # Custom styles
    page_styles = """
        .page-extra-muted {
            color: #aaaaaa;
        }
    """
    page.style[markupsafe.Markup(page_styles)]

    content = page.collect()

    return await template.blank(
        title=f"Finish {release.project.display_name} {release.version} ~ ATR",
        description=f"Finish {release.project.display_name} {release.version} as a release preview.",
        content=content,
    )


def _render_publish_step() -> list[htm.Element | str]:
    if util.svn_publish_target() is util.SvnPublishTarget.RELEASE:
        return ["1. publish to SVN ", htm.code["dist/release"], " if you did not already"]
    return [
        "1. publish to SVN ",
        htm.code["dist/atr"],
        " if you did not already, and then ",
        htm.a(href=util.as_url(docs.page, path="promoting-to-release"))[
            "manually ",
            htm.code["svn mv"],
            " the results",
        ],
        " to ",
        htm.code["dist/release"],
    ]


def _render_release_card(release: sql.Release, announce_disable_message: str) -> htm.Element:
    """Render the release information card."""
    announce_classes = ".btn-success"
    if announce_disable_message:
        announce_classes += ".disabled"
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
                    f".btn{announce_classes}.me-2",
                    title=f"Announce {release.key}",
                    href=util.as_url(
                        announce.selected,
                        project_key=release.project.key,
                        version_key=release.version,
                    )
                    if (not announce_disable_message)
                    else None,
                )[
                    htm.icon("check-circle"),
                    " Announce",
                ],
                htm.span(".page-preview-meta-item.page-extra-muted")[f"{announce_disable_message}"],
            ],
        ],
    ]
    return card


async def _render_svn_publish(page: htm.Block, release: sql.Release) -> None:
    proj = release.safe_project_key
    ver = release.safe_version_key
    rev = release.safe_latest_revision_number
    page.h2["Publish to SVN"]
    completed = await interaction.release_completed_svn_publish_task_for_revision(proj, ver, rev)
    if completed is not None:
        _render_svn_publish_completed(page, release, completed)
        return
    in_flight = await interaction.release_in_flight_svn_publish_task(proj, ver, rev)
    if in_flight is not None:
        page.div(".alert.alert-info.mb-4")[
            htm.p["The release files are being published to SVN."],
            htm.a(
                ".btn.btn-primary",
                href=util.as_url(selected, project_key=release.project.key, version_key=release.version),
            )["Refresh"],
        ]
        return
    failed = await interaction.release_latest_failed_svn_publish_task(proj, ver, rev)
    if failed is not None:
        page.div(".alert.alert-danger.mb-4")[
            f"The most recent attempt to publish to SVN failed: {failed.error or 'unknown error'}"
        ]
    if release.is_embargoed:
        page.div(".p-3.mb-4.bg-danger-subtle.border.border-danger.rounded")[
            "This is an expedited security release, and is embargoed. Publishing to SVN copies the"
            " release files to the public distribution area, which breaks the embargo. Please ensure"
            " that you have the authority to lift the embargo before publishing. This action is not"
            " reversible."
        ]
    await form.render_block(
        page,
        shared.finish.PublishToSvnForm,
        defaults={
            "download_path_suffix": _svn_download_path_default(release),
            "revision_number": release.latest_revision_number,
        },
        submit_label="Publish to SVN",
    )


def _render_svn_publish_completed(page: htm.Block, release: sql.Release, completed: sql.Task) -> None:
    revision = _svn_publish_revision(completed)
    text = f"Published to SVN as r{revision}" if (revision is not None) else "Published to SVN"
    url = _svn_publish_url(release, completed)
    if url is None:
        page.div(".alert.alert-success.mb-4")[text]
        return
    page.div(".alert.alert-success.mb-4")[text, " at ", htm.a(href=url)[url]]


def _render_task(task: sql.Task) -> htm.Element:
    """Render a distribution task's details."""
    workflow_args: args.DistributionWorkflow = args.DistributionWorkflow.model_validate(task.task_args)
    task_date = task.added.strftime("%Y-%m-%d %H:%M:%S")
    task_status = task.status.value
    workflow_status = task.workflow.status if task.workflow else ""
    workflow_message = (
        task.workflow.message if (task.workflow and task.workflow.message) else workflow_status.capitalize()
    )
    if task_status != sql.TaskStatus.COMPLETED:
        return htm.details(".ms-4")[
            htm.summary[f"{task_date} {workflow_args.platform} ({workflow_args.package} {workflow_args.version})"],
            htm.p(".ms-4")[task.error if task.error else task_status.capitalize()],
        ]
    else:
        return htm.details(".ms-4")[
            htm.summary[f"{task_date} {workflow_args.platform} ({workflow_args.package} {workflow_args.version})"],
            *[htm.p(".ms-4")[w] for w in workflow_message.split("\n")],
        ]


def _svn_download_path_default(release: sql.Release) -> str:
    suffix = construct.effective_download_path_suffix(release)
    return str(suffix) if suffix is not None else ""


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
