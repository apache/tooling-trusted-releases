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
import os
import pathlib
from collections.abc import Sequence
from typing import Literal

import aiofiles.os
import asfquart.base as base
import markupsafe
import quart

import atr.analysis as analysis
import atr.blueprints.get as get
import atr.config as config
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.get.announce as announce
import atr.get.distribution as distribution
import atr.get.download as download
import atr.get.file as file
import atr.get.revisions as revisions
import atr.get.root as root
import atr.htm as htm
import atr.mapping as mapping
import atr.models.args as args
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.shared.activity as activity
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web


@dataclasses.dataclass
class RCTagAnalysisResult:
    affected_paths_preview: list[tuple[str, str]]
    affected_count: int
    total_paths: int


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
        (release, deletable_dirs, rc_analysis, tasks) = await _get_page_data(project_key, version_key)
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
    except FileNotFoundError:
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
        deletable_dirs=deletable_dirs,
        rc_analysis=rc_analysis,
        distribution_tasks=tasks,
        announce_disable_message=announce_msg,
    )


async def _analyse_rc_tags(latest_revision_dir: os.PathLike) -> RCTagAnalysisResult:
    r = RCTagAnalysisResult(
        affected_paths_preview=[],
        affected_count=0,
        total_paths=0,
    )

    if not await aiofiles.os.path.exists(latest_revision_dir):
        return r

    async for p_rel in util.paths_recursive_all(latest_revision_dir):
        r.total_paths += 1
        original_path_str = str(p_rel)
        stripped_path_str = str(analysis.candidate_removed(p_rel))
        if original_path_str == stripped_path_str:
            continue
        r.affected_count += 1
        if len(r.affected_paths_preview) >= 5:
            # Can't break here, because we need to update the counts
            continue
        r.affected_paths_preview.append((original_path_str, stripped_path_str))

    return r


async def _deletable_choices(
    latest_revision_dir: safe.StatePath, target_dirs: set[safe.RelPath]
) -> list[tuple[str, str]]:
    empty_deletable_dirs: list[safe.RelPath] = []
    if await aiofiles.os.path.exists(latest_revision_dir):
        for d_rel in target_dirs:
            if d_rel == pathlib.Path("."):
                # Disallow deletion of the root directory
                continue
            d_full = latest_revision_dir / d_rel
            if (await aiofiles.os.path.isdir(d_full)) and (not await aiofiles.os.listdir(d_full)):
                empty_deletable_dirs.append(d_rel)
    return sorted([(str(p), str(p)) for p in empty_deletable_dirs])


async def _get_page_data(
    project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> tuple[sql.Release, list[tuple[str, str]], RCTagAnalysisResult, Sequence[sql.Task]]:
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

    latest_revision_dir = paths.release_directory(release)
    _, target_dirs = await _sources_and_targets(latest_revision_dir)
    deletable_dirs = await _deletable_choices(latest_revision_dir, target_dirs)
    rc_analysis_result = await _analyse_rc_tags(latest_revision_dir)

    return release, deletable_dirs, rc_analysis_result, tasks


async def _render_delete_directory_form(deletable_dirs: list[tuple[str, str]]) -> htm.Element:
    """Render the delete directory form."""
    section = htm.Block()

    section.h3["Delete an empty directory"]

    await form.render_block(
        section,
        shared.finish.DeleteEmptyDirectoryForm,
        defaults={"directory_to_delete": deletable_dirs},
        submit_label="Delete empty directory",
        submit_classes="btn-danger",
    )

    return section.collect()


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
    deletable_dirs: list[tuple[str, str]],
    rc_analysis: RCTagAnalysisResult,
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

    if (badge := render.expedited_badge(release)) is not None:
        page.append(htm.p[badge])

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

    if config.get().SVN_PUBLISH_URL:
        proj, ver, rev = release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
        in_flight = await interaction.release_in_flight_svn_publish_task(proj, ver, rev)
        completed = await interaction.release_completed_svn_publish_task_for_revision(proj, ver, rev)
        if (in_flight is None) and (completed is None):
            download_path_suffix = ""
            committee = release.project.committee
            if (committee is not None) and (release.project.key != util.unwrap(committee.key)):
                download_path_suffix = f"{release.project.key}-{release.version}"
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

    page.h2["Tidy up the release"]
    # Delete directory form
    if deletable_dirs:
        page.append(await _render_delete_directory_form(deletable_dirs))

    # Remove RC tags section
    page.append(await _render_rc_tags_section(rc_analysis))

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
        .page-file-select-text {
            vertical-align: middle;
            margin-left: 8px;
        }
        .page-table-button-cell {
            width: 1%;
            white-space: nowrap;
            vertical-align: middle;
        }
        .page-table-path-cell {
            vertical-align: middle;
        }
        .page-item-selected td {
            background-color: #e9ecef;
            font-weight: 500;
        }
        .page-table-row-interactive {
            height: 52px;
        }
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


def _render_rc_preview_table(affected_paths: list[tuple[str, str]]) -> htm.Element:
    """Render the RC tags preview table."""
    rows = [htm.tr[htm.td[original], htm.td[stripped]] for original, stripped in affected_paths]

    return htm.div[
        htm.p(".mb-2")["Preview of changes:"],
        htm.table(".table.table-sm.table-striped.border.mt-3")[htm.tbody[rows]],
    ]


async def _render_rc_tags_section(rc_analysis: RCTagAnalysisResult) -> htm.Element:
    """Render the remove RC tags section."""
    section = htm.Block()

    section.h3["Remove release candidate tags"]

    if rc_analysis.affected_count > 0:
        section.div(".alert.alert-info.mb-3")[
            htm.p(".mb-3.fw-semibold")[
                f"{rc_analysis.affected_count} / {rc_analysis.total_paths} paths would be affected by RC tag removal."
            ],
            _render_rc_preview_table(rc_analysis.affected_paths_preview) if rc_analysis.affected_paths_preview else "",
        ]

        await form.render_block(
            section,
            shared.finish.RemoveRCTagsForm,
            submit_label="Remove RC tags",
            submit_classes="btn-warning",
            form_classes=".mb-4.atr-canary",
        )
    else:
        section.p["No paths with RC tags found to remove."]

    return section.collect()


def _render_release_card(release: sql.Release, announce_disable_message: str) -> htm.Element:
    """Render the release information card."""
    announce_classes = ".btn-success"
    if announce_disable_message:
        announce_classes += ".disabled"
    card = htm.div(".card.mb-4.shadow-sm", id=release.key)[
        htm.div(".card-header.bg-light")[htm.h3(".card-title.mb-0")["About this release preview"]],
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
                    ".btn.btn-secondary.me-2",
                    title=f"Show revisions for {release.key}",
                    href=util.as_url(
                        revisions.selected,
                        project_key=release.project.key,
                        version_key=release.version,
                    ),
                )[
                    htm.icon("clock-history"),
                    " Show revisions",
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


async def _sources_and_targets(latest_revision_dir: safe.StatePath) -> tuple[list[pathlib.Path], set[safe.RelPath]]:
    source_items_rel: list[pathlib.Path] = []
    target_dirs: set[safe.RelPath] = {safe.RelPath(".")}

    async for item_rel_path in util.paths_recursive_all(latest_revision_dir):
        current_parent = item_rel_path.parent
        source_items_rel.append(item_rel_path)

        while True:
            target_dirs.add(safe.RelPath.from_path(current_parent))
            if current_parent == pathlib.Path("."):
                break
            current_parent = current_parent.parent

        item_abs_path = latest_revision_dir / item_rel_path
        if await aiofiles.os.path.isfile(item_abs_path):
            pass
        elif await aiofiles.os.path.isdir(item_abs_path):
            target_dirs.add(safe.RelPath.from_path(item_rel_path))

    return source_items_rel, target_dirs
