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

import asfquart.base as base

import atr.blueprints.get as get
import atr.db as db
import atr.form as form
import atr.htm as htm
import atr.models.sql as sql
import atr.post as post
import atr.render as render
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web
from atr.tasks import gha


@get.committer("/distribution/automate/<project>/<version>")
async def automate(session: web.Committer, project: str, version: str) -> str:
    return await _automate_form_page(project, version, staging=False)


@get.committer("/distributions/list/<project_name>/<version_name>")
async def list_get(session: web.Committer, project_name: str, version_name: str) -> str:
    distributions, tasks = await _get_page_data(project_name, version_name)

    block = htm.Block()

    release = await shared.distribution.release_validated(project_name, version_name, staging=None)
    staging = release.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
    render.html_nav_phase(block, project_name, version_name, staging)

    record_a_distribution = htm.a(
        ".btn.btn-primary",
        href=util.as_url(
            stage_record if staging else record,
            project=project_name,
            version=version_name,
        ),
    )["Record a distribution"]

    # Distribution list for project-version
    block.h1["Distribution list for ", htm.em[f"{project_name}-{version_name}"]]

    if len(tasks) > 0:
        _render_distribution_tasks(tasks, block, project_name, version_name)

    if not distributions:
        block.p["No distributions found."]
        block.p[record_a_distribution]
        return await template.blank(
            "Distribution list",
            content=block.collect(),
        )
    block.p["Here are all of the distributions recorded for this release."]
    block.p[record_a_distribution]
    # Table of contents
    block.append(htm.ul_links(*[(f"#distribution-{dist.identifier}", dist.title) for dist in distributions]))

    ## Distributions
    block.h2["Distributions"]
    for dist in distributions:
        ### Platform package version
        block.h3(
            # Cannot use "#id" here, because the ID contains "."
            # If an ID contains ".", htm parses that as a class
            id=f"distribution-{dist.identifier}"
        )[dist.title]
        tbody = htm.tbody[
            shared.distribution.html_tr("Release name", dist.release_name),
            shared.distribution.html_tr("Platform", dist.platform.value.name),
            shared.distribution.html_tr("Owner or Namespace", dist.owner_namespace or "-"),
            shared.distribution.html_tr("Package", dist.package),
            shared.distribution.html_tr("Version", dist.version),
            shared.distribution.html_tr("Staging", "Yes" if dist.staging else "No"),
            shared.distribution.html_tr("Upload date", str(dist.upload_date)),
            shared.distribution.html_tr_a("API URL", dist.api_url),
            shared.distribution.html_tr_a("Web URL", dist.web_url),
        ]
        block.table(".table.table-striped.table-bordered")[tbody]

        delete_form = form.render(
            model_cls=shared.distribution.DeleteForm,
            action=util.as_url(post.distribution.delete, project=project_name, version=version_name),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-danger btn-sm",
            submit_label="Delete",
            empty=True,
            defaults={
                "release_name": dist.release_name,
                "platform": shared.distribution.DistributionPlatform.from_sql(dist.platform),
                "owner_namespace": dist.owner_namespace or "",
                "package": dist.package,
                "version": dist.version,
            },
            confirm=("Are you sure you want to delete this distribution? This cannot be undone."),
        )
        block.append(htm.div(".mb-3")[delete_form])

    title = f"Distribution list for {project_name} {version_name}"
    return await template.blank(title, content=block.collect())


@get.committer("/distribution/record/<project>/<version>")
async def record(session: web.Committer, project: str, version: str) -> str:
    return await _record_form_page(project, version, staging=False)


@get.committer("/distribution/stage/automate/<project>/<version>")
async def stage_automate(session: web.Committer, project: str, version: str) -> str:
    return await _automate_form_page(project, version, staging=True)


@get.committer("/distribution/stage/record/<project>/<version>")
async def stage_record(session: web.Committer, project: str, version: str) -> str:
    return await _record_form_page(project, version, staging=True)


async def _automate_form_page(project: str, version: str, staging: bool) -> str:
    """Helper to render the distribution automation form page."""
    await shared.distribution.release_validated(project, version, staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, project, version, staging=staging)

    title = "Create a staging distribution" if staging else "Create a distribution"
    block.h1[title]

    block.p[
        "Create a distribution of ",
        htm.strong[f"{project}-{version}"],
        " using the form below.",
    ]
    block.p[
        "You can also ",
        htm.a(href=util.as_url(list_get, project_name=project, version_name=version))["view the distribution list"],
        ".",
    ]

    # Determine the action based on staging
    action = (
        util.as_url(post.distribution.stage_automate_selected, project=project, version=version)
        if staging
        else util.as_url(post.distribution.automate_selected, project=project, version=version)
    )

    # TODO: Reuse the same form for now - maybe we can combine this and the function below adding an automate=True arg
    # Render the distribution form
    form_html = form.render(
        model_cls=shared.distribution.DistributeForm,
        submit_label="Distribute",
        action=action,
        defaults={"package": project, "version": version},
    )
    block.append(form_html)

    return await template.blank(title, content=block.collect())


async def _get_page_data(project_name: str, version_name: str) -> tuple[Sequence[sql.Distribution], Sequence[sql.Task]]:
    """Get all the data needed to render the finish page."""
    async with db.session() as data:
        via = sql.validate_instrumented_attribute
        distributions = await data.distribution(
            release_name=sql.release_name(project_name, version_name),
        ).all()
        release = await data.release(
            project_name=project_name,
            version=version_name,
            _committee=True,
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))
        tasks = [
            t
            for t in (
                await data.task(
                    project_name=project_name,
                    version_name=version_name,
                    revision_number=release.latest_revision_number,
                    task_type=sql.TaskType.DISTRIBUTION_WORKFLOW,
                    _workflow=True,
                )
                .order_by(sql.sqlmodel.desc(via(sql.Task.started)))
                .all()
            )
            if t.status in [sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE, sql.TaskStatus.FAILED]
            or (t.workflow and t.workflow.status in ["in-progress", "failed"])
        ]

    return distributions, tasks


async def _record_form_page(project: str, version: str, staging: bool) -> str:
    """Helper to render the distribution recording form page."""
    await shared.distribution.release_validated(project, version, staging=staging)

    block = htm.Block()
    render.html_nav_phase(block, project, version, staging=staging)

    title = "Record a manual staging distribution" if staging else "Record a manual distribution"
    block.h1[title]

    block.p[
        "Record a manual distribution of ",
        htm.strong[f"{project}-{version}"],
        " using the form below.",
    ]
    block.p[
        "You can also ",
        htm.a(href=util.as_url(list_get, project_name=project, version_name=version))["view the distribution list"],
        ".",
    ]

    # Determine the action based on staging
    action = (
        util.as_url(post.distribution.stage_record_selected, project=project, version=version)
        if staging
        else util.as_url(post.distribution.record_selected, project=project, version=version)
    )

    # Render the distribution form
    form_html = form.render(
        model_cls=shared.distribution.DistributeForm,
        submit_label="Record distribution",
        action=action,
        defaults={"package": project, "version": version},
    )
    block.append(form_html)

    return await template.blank(title, content=block.collect())


def _render_distribution_tasks(tasks: Sequence[sql.Task], block: htm.Block, project_name: str, version_name: str):
    failed_tasks = [
        t for t in tasks if t.status == sql.TaskStatus.FAILED or (t.workflow and t.workflow.status == "failed")
    ]
    in_progress_tasks = [
        t
        for t in tasks
        if t.status in [sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE]
        or (t.workflow and t.workflow.status not in ["completed", "success", "failed"])
    ]
    if len(failed_tasks) > 0:
        summary = f"{len(failed_tasks)} distribution{'s' if (len(failed_tasks) != 1) else ''} failed for this version"
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
                        list_get,
                        project_name=project_name,
                        version_name=version_name,
                    ),
                )["Refresh"],
            ]
        )


def _render_task(task: sql.Task) -> htm.Element:
    """Render a distribution task's details."""
    args: gha.DistributionWorkflow = gha.DistributionWorkflow.model_validate(task.task_args)
    task_date = task.added.strftime("%Y-%m-%d %H:%M:%S")
    task_status = task.status.value
    workflow_status = task.workflow.status if task.workflow else ""
    workflow_message = (
        task.workflow.message if task.workflow and task.workflow.message else workflow_status.capitalize()
    )
    if task_status != sql.TaskStatus.COMPLETED:
        return htm.details(".ms-4")[
            htm.summary[f"{task_date} {args.platform} ({args.package} {args.version})"],
            htm.p(".ms-4")[task.error if task.error else task_status.capitalize()],
        ]
    else:
        return htm.details(".ms-4")[
            htm.summary[f"{task_date} {args.platform} ({args.package} {args.version})"],
            *[htm.p(".ms-4")[w] for w in workflow_message.split("\n")],
        ]
