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
import atr.config as config
import atr.construct as construct
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.announce as announce
import atr.get.distribution as distribution
import atr.get.download as download
import atr.get.file as file
import atr.get.root as root
import atr.htm as htm
import atr.mapping as mapping
import atr.models.args as args
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

    page.h2["Distribute on third party platforms"]
    page.p[
        "During this phase you should distribute release artifacts to your package distribution networks "
        "such as Maven Central, PyPI, or Docker Hub."
    ]

    if len(distribution_tasks) > 0:
        page.append(_render_distribution_tasks(release, distribution_tasks))

    page.append(_render_dist_warning())
    page.append(_render_distribution_buttons(release))

    if config.get().SVN_PUBLISH_URL and (
        user.is_committee_member(release.committee, session.uid)
        or user.is_release_manager(release.committee, session.uid)
    ):
        proj, ver, rev = release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
        in_flight = await interaction.release_in_flight_svn_publish_task(proj, ver, rev)
        completed = await interaction.release_completed_svn_publish_task_for_revision(proj, ver, rev)
        if (in_flight is None) and (completed is None):
            download_path_suffix = ""
            committee = release.project.committee
            if committee is not None:
                suffix = construct.resolve_download_path_suffix(
                    template=release.project.policy_download_path_suffix,
                    project_key=release.project.key,
                    version=release.version,
                    is_top_level=(release.project.key == util.unwrap(committee.key)),
                )
                download_path_suffix = str(suffix) if suffix is not None else ""
            page.h2["Publish to SVN"]
            await form.render_block(
                page,
                shared.finish.PublishToSvnForm,
                defaults={
                    "download_path_suffix": download_path_suffix,
                    "revision_number": release.latest_revision_number,
                },
                submit_label="Publish to SVN",
            )

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
                    title=f"Publish and announce {release.key}",
                    href=util.as_url(
                        announce.selected,
                        project_key=release.project.key,
                        version_key=release.version,
                    )
                    if (not announce_disable_message)
                    else None,
                )[
                    htm.icon("check-circle"),
                    " Publish and announce",
                ],
                htm.span(".page-preview-meta-item.page-extra-muted")[f"{announce_disable_message}"],
            ],
        ],
    ]
    return card


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
