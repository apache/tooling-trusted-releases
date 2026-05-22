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

from collections import Counter
from typing import Literal

import asfquart.base as base
import htpy
import quart
import strictyaml

import atr.blueprints.get as get
import atr.config as config
import atr.construct as construct
import atr.db as db
import atr.form as form
import atr.get.committees as committees
import atr.get.compose as compose
import atr.get.docs as docs
import atr.get.file as file
import atr.get.finish as finish
import atr.get.start as start
import atr.get.vote as vote
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.registry as registry
import atr.render as render
import atr.shared as shared
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web


@get.typed
async def add_project(
    session: web.Committer, _project_add: Literal["project/add"], committee_key: safe.CommitteeKey
) -> web.WerkzeugResponse | str:
    """
    URL: /project/add/<committee_key>
    """
    await session.prevent_confusing_ui_display_committee(committee_key)

    async with db.session() as data:
        committee = await data.committee(key=str(committee_key)).demand(
            base.ASFQuartException(f"Committee {committee_key!s} not found", errorcode=404)
        )

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(committees.view, name=committee.key))["← Back to committee"]]
    page.h1["Add project"]
    page.p[f"Add a new project to the {committee.display_name} committee."]

    committee_display_name = committee.name or committee.key.title()

    await form.render_block(
        page,
        model_cls=shared.projects.AddProjectForm,
        action=util.as_url(post.projects.add_project, committee_key=committee.key),
        submit_label="Add project",
        cancel_url=util.as_url(committees.view, name=committee.key),
        defaults={
            "committee_key": committee.key,
            "committee_key_display": committee.key,
        },
    )

    # TODO: It would be better to have these attributes on the form
    page.append(
        htpy.div(
            "#projects-add-config.d-none",
            data_committee_key=committee.key,
            data_committee_display_name=committee_display_name,
        )
    )

    return await template.blank(
        title="Add project",
        description=f"Add a new project to the {committee.display_name} committee.",
        content=page.collect(),
        javascripts=["projects-add-form"],
    )


@get.typed
async def projects(session: web.Public, _projects: Literal["projects"]) -> str:
    """
    URL: /projects
    Main project directory page.
    """
    async with db.session() as data:
        projects = await data.project(_committee=True, _releases=True).order_by(sql.Project.name).all()

    committee_project_counts: Counter[str] = Counter(
        str(p.committee.key) for p in projects if p.committee and p.status == sql.ProjectStatus.ACTIVE
    )

    action_forms: dict[str, htm.Element] = {}
    if session is not None:
        for project in projects:
            if project.status != sql.ProjectStatus.ACTIVE:
                continue
            if not (user.is_committee_member(project.committee, session.uid) or session.is_admin):
                continue
            if project.committee and committee_project_counts[str(project.committee.key)] <= 1:
                continue
            if not project.releases:
                action_forms[str(project.key)] = await form.render(
                    model_cls=shared.projects.DeleteSelectedProject,
                    action=util.as_url(post.projects.delete),
                    form_classes=".d-inline-block.m-0",
                    submit_classes="btn-sm btn-outline-danger",
                    submit_label="Delete project",
                    empty=True,
                    defaults={"project_key": str(project.key)},
                    confirm="Are you sure you want to delete this project? This cannot be undone.",
                )
            elif all(r.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT for r in project.releases):
                action_forms[str(project.key)] = await form.render(
                    model_cls=shared.projects.ArchiveSelectedProject,
                    action=util.as_url(post.projects.archive),
                    form_classes=".d-inline-block.m-0",
                    submit_classes="btn-sm btn-outline-secondary",
                    submit_label="Archive project",
                    empty=True,
                    defaults={"project_key": str(project.key)},
                    confirm=(
                        f"This project has {util.plural(len(project.releases), 'draft release')}."
                        " Archiving will delete those drafts and mark the project as retired."
                        " Continue?"
                    ),
                )

    return await template.render("projects.html", projects=projects, action_forms=action_forms)


@get.typed
async def select(session: web.Committer, _project_select: Literal["project/select"]) -> str:
    """
    URL: /project/select
    Select a project to work on.
    """
    user_projects = []
    if session.uid:
        async with db.session() as data:
            # TODO: Move this filtering logic somewhere else
            # Test mode allows test projects to be shown
            test_mode = config.is_test_mode()
            all_projects = await data.project(status=sql.ProjectStatus.ACTIVE, _committee=True).all()
            user_projects = [
                p
                for p in all_projects
                if p.committee
                and (
                    (test_mode and (p.committee.key == "test"))
                    or (session.uid in p.committee.committee_members)
                    or (session.uid in p.committee.committers)
                    or (session.uid in p.committee.release_managers)
                )
            ]
            user_projects.sort(key=lambda p: p.display_name)

    return await template.render("project-select.html", user_projects=user_projects)


@get.typed
async def view(
    session: web.Committer, _projects: Literal["projects"], project_key: safe.ProjectKey
) -> web.WerkzeugResponse | str:
    """
    URL: /projects/<project_key>
    """
    async with db.session() as data:
        project = await data.project(
            key=str(project_key),
            _committee=True,
            _committee_public_signing_keys=True,
            _release_policy=True,
            _releases=True,
        ).demand(base.ASFQuartException(f"Project {project_key} not found", errorcode=404))
        cycles = list(await data.project_cycle(project_key=str(project_key)).all())
        active_committee_projects = 0
        if project.committee is not None:
            active_committee_projects = len(
                await data.project(
                    committee_key=project.committee.key,
                    status=sql.ProjectStatus.ACTIVE,
                    _committee=False,
                ).all()
            )

    is_committee_member = bool(project.committee and user.is_committee_member(project.committee, session.uid))
    is_privileged = session.is_admin
    can_edit = (is_committee_member or is_privileged) and project.status != sql.ProjectStatus.RETIRED

    page = htm.Block()

    page_styles = """
        .page-remove-tag {
            font-size: 0.65em;
            padding: 0.2em 0.3em;
            cursor: pointer;
        }
    """
    page.style[page_styles]

    title_row = htm.div(".row")[
        htm.div(".col-md")[htm.h1[project.display_name]],
        htm.div(".col-sm-auto")[htm.span(".badge.text-bg-secondary")[project.status.value.lower()]]
        if (project.status.value.lower() != "active")
        else "",
    ]
    page.append(title_row)

    archived_banner = render.archived_project_banner(project, "Actions are disabled.")
    if archived_banner:
        page.append(archived_banner)

    if project.updated:
        page.append(
            htm.div(".row.mb-2")[
                htm.div(".col")[
                    htm.p(".text-muted.small.mb-0")[
                        f"Last updated {project.updated.strftime('%Y-%m-%d')} by {project.updated_by or 'unknown'}"
                    ]
                ]
            ]
        )
    page.append(_render_project_label_card(project))
    page.append(_render_pmc_card(project))

    tabs = await _generate_tabs(can_edit, project, cycles)
    page.append(tabs)

    # Below the tabs: admin-only project actions.
    if is_committee_member or is_privileged:
        section = htm.Block(htm.div)
        section.h2["Actions"]

        is_sole_active_project = project.committee is not None and active_committee_projects <= 1
        if project.status == sql.ProjectStatus.ACTIVE and not is_sole_active_project:
            await _delete_section(section, project)

        if project.committee:
            if (project.committee.key in session.committees) or is_privileged:
                section.p[
                    htm.a(
                        ".btn.btn-sm.btn-outline-primary",
                        href=util.as_url(add_project, committee_key=project.committee.key),
                    )["Create a sibling project"]
                ]
        page.append(section.collect())

    content = page.collect()

    javascripts = ["copy-variable"] if can_edit else []
    return await template.blank(
        title=f"{project.display_name}",
        description=f"Information regarding {project.display_name}.",
        content=content,
        javascripts=javascripts,
    )


async def _generate_tabs(can_edit: bool, project: sql.Project, cycles: list[sql.ProjectCycle]) -> htpy.Element:
    tab_items: list[htm.Tab] = [
        htm.Tab(
            key="releases",
            label="Releases",
            render=lambda: _render_releases_tab(
                project,
                cycles,
                can_edit=can_edit,
            ),
        ),
        htm.Tab(
            key="metadata",
            label="Metadata",
            render=lambda: _render_metadata_tab(project, can_edit=can_edit),
        ),
    ]

    if can_edit:
        tab_items.append(
            htm.Tab(
                key="lifecycle",
                label="Lifecycle",
                render=lambda: _render_lifecycle_tab(project, can_edit=can_edit),
            )
        )

    if project.status == sql.ProjectStatus.ACTIVE:
        if can_edit:
            tab_items.extend(
                [
                    htm.Tab(
                        "trusted-publishing",
                        "Trusted Publishing",
                        lambda: _render_trusted_publishing_form(project),
                    ),
                    htm.Tab("compose", "Compose", lambda: _render_compose_form(project)),
                    htm.Tab("vote", "Vote", lambda: _render_vote_form(project)),
                    htm.Tab("finish", "Finish", lambda: _render_finish_form(project)),
                ]
            )
        else:
            tab_items.append(htm.Tab("policy", "Policy", lambda: _render_policy_readonly(project)))

    active_tab = quart.request.args.get("tab", tab_items[0].key)
    base_url = util.as_url(view, project_key=str(project.key))

    return await htm.tabs(tab_items, active_key=active_tab, base_url=base_url)


def _cycle_has_dates(cycle: sql.ProjectCycle) -> bool:
    return any(getattr(cycle, attr) is not None for attr in ("start", "begin", "latest", "eod", "eos", "eol"))


def _input_with_variables(
    field_name: str,
    default_value: str,
    template_variables: list[tuple[str, str]],
    documentation: str | None = None,
) -> htm.Element:
    text_input = htpy.input(
        f"#{field_name}.form-control.font-monospace",
        type="text",
        name=field_name,
        value=default_value,
    )

    variable_rows = []
    for name, description in template_variables:
        variable_rows.append(
            htm.tr[
                htm.td(".font-monospace.text-nowrap.py-1")[f"{{{{{name}}}}}"],
                htm.td(".py-1")[description],
                htm.td(".text-end.py-1")[
                    htpy.button(
                        ".btn.btn-sm.btn-outline-secondary.copy-var-btn",
                        type="button",
                        data_variable=f"{{{{{name}}}}}",
                    )["Copy"]
                ],
            ]
        )

    variables_table = htm.table(".table.table-sm.mb-0")[
        htm.thead[
            htm.tr[
                htm.th(".py-1")["Variable"],
                htm.th(".py-1")["Description"],
                htm.th(".py-1")[""],
            ]
        ],
        htm.tbody[*variable_rows],
    ]

    details = htm.details(".mt-2")[
        htm.summary(".text-muted")["Available template variables"],
        htm.div(".mt-2")[variables_table],
    ]

    elements: list[htm.Element | htm.VoidElement] = [text_input]
    if documentation:
        elements.append(htm.div(".text-muted.mt-1.form-text")[documentation])
    elements.append(details)

    return htm.div[elements]


def _render_categories_section(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Categories"]]

    current_categories = project.category.split(", ") if project.category else []
    category_badges = []
    for cat in current_categories:
        remove_button = (
            # Manual form as badges are not handled by the form system
            htm.form(
                ".d-inline.m-0",
                method="post",
                action=_view_action(project, "metadata"),
            )[
                form.csrf_input(),
                htpy.input(type="hidden", name="project_key", value=str(project.key)),
                htpy.input(type="hidden", name="variant", value="remove_category"),
                htpy.input(type="hidden", name="category_to_remove", value=cat),
                htpy.button(
                    ".btn-close.btn-close-white.ms-1.page-remove-tag", type="submit", aria_label=f"Remove {cat}"
                ),
            ]
            if (cat not in registry.FORBIDDEN_PROJECT_CATEGORIES)
            else ""
        )
        badge = htm.div(".badge.bg-primary.d-inline-flex.align-items-center.px-2.py-1")[
            htm.span[cat],
            remove_button,
        ]
        category_badges.append(badge)

    add_form = htm.form(
        ".mb-3",
        method="post",
        action=_view_action(project, "metadata"),
    )[
        form.csrf_input(),
        htpy.input(type="hidden", name="project_key", value=str(project.key)),
        htpy.input(type="hidden", name="variant", value="add_category"),
        htm.div(".d-flex.align-items-center")[
            htpy.input(
                ".form-control.form-control-sm.me-2", type="text", name="category_to_add", placeholder="New category"
            ),
            htpy.button(".btn.btn-sm.btn-success.text-nowrap.pe-3", type="submit")[htpy.i(".bi.bi-plus"), " Add"],
        ],
    ]

    with card.block(htm.div, classes=".card-body") as card_body:
        card_body.append(add_form)
        if category_badges:
            card_body.append(htm.div(".d-flex.flex-wrap.gap-2.align-items-center.mt-3")[*category_badges])
    return card.collect()


async def _render_compose_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Release policy - Compose options"]
    ]

    schema = strictyaml.EmptyDict() | strictyaml.MapPattern(strictyaml.Str(), strictyaml.UniqueSeq(strictyaml.Str()))
    if project.policy_file_tag_mappings:
        atr_tag_yaml = strictyaml.as_document(project.policy_file_tag_mappings, schema).as_yaml()
    else:
        atr_tag_yaml = ""
    with card.block(htm.div, classes=".card-body") as card_body:
        await form.render_block(
            card_body,
            model_cls=shared.projects.ComposePolicyForm,
            action=_view_action(project, "compose"),
            submit_label="Save",
            defaults={
                "project_key": str(project.key),
                "license_check_mode": project.policy_license_check_mode,
                "source_excludes_lightweight": "\n".join(project.policy_source_excludes_lightweight),
                "source_excludes_rat": "\n".join(project.policy_source_excludes_rat),
                "file_tag_mappings": atr_tag_yaml,
            },
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            # wider_widgets=True,
            textarea_rows=5,
        )
    return card.collect()


def _cycle_display_name(cycle: sql.ProjectCycle) -> str:
    # The "default" cycle is what every project gets when it has no cycle_match
    return "No lifecycle information" if cycle.cycle == "default" else f"Version {cycle.cycle}"


def _render_cycle_dates_card(cycle: sql.ProjectCycle) -> htm.Element:
    card = htm.Block(htm.details, classes=".card.mb-4")
    card.summary(".card-header.bg-light")[htm.h3(".mb-0.d-inline-block")["Cycle dates"]]
    rows = []
    # Order matches the rough lifecycle reading: when did things start, when
    # do they wind down, and is this an LTS line.
    # Labels match the column semantics in #912.
    date_fields: list[tuple[str, str]] = [
        ("First release candidate", "begin"),
        ("First release", "start"),
        ("Latest release", "latest"),
        ("End of development", "eod"),
        ("End of support", "eos"),
        ("End of life", "eol"),
    ]
    for label, attr in date_fields:
        value = getattr(cycle, attr)
        value_cell = (
            htm.td[value.strftime("%Y-%m-%d")] if value is not None else htm.td(".text-muted.fst-italic")["Not set"]
        )
        rows.append(
            htm.tr[
                htm.td(".pe-3.text-muted")[label],
                value_cell,
            ]
        )
    if cycle.lts:
        rows.append(
            htm.tr[
                htm.td(".pe-3.text-muted")["Long-term support"],
                htm.td[htm.span(".badge.text-bg-info")["LTS"]],
            ]
        )
    with card.block(htm.div, classes=".card-body") as body:
        body.append(htm.table(".table.table-sm.mb-0")[*rows])
    return card.collect()


async def _render_cycle_dates_form(project: sql.Project, cycle: sql.ProjectCycle) -> htm.Element:
    card = htm.Block(htm.details, classes=".card.mb-4")
    card.summary(".card-header.bg-light")[htm.h3(".mb-0.d-inline-block")["Lifecycle"]]
    with card.block(htm.div, classes=".card-body") as body:
        await form.render_block(
            body,
            model_cls=shared.projects.EditCycleDatesForm,
            action=_view_action(project, "lifecycle"),
            submit_label="Save cycle dates",
            defaults={
                "project_key": str(project.key),
                "cycle_key": cycle.cycle_key,
                "eod": cycle.eod.date() if cycle.eod is not None else None,
                "eos": cycle.eos.date() if cycle.eos is not None else None,
                "eol": cycle.eol.date() if cycle.eol is not None else None,
                "lts": cycle.lts,
            },
        )
    return card.collect()


async def _delete_section(section: htm.Block, project: sql.Project):
    delete_form = None
    if not project.releases:
        delete_form = await form.render(
            shared.projects.DeleteSelectedProject,
            action=util.as_url(post.projects.delete),
            form_classes="",
            submit_classes="btn-sm btn-outline-danger",
            submit_label="Delete project",
            defaults={"project_key": str(project.key)},
            confirm="Are you sure you want to delete this project? This cannot be undone.",
            empty=True,
        )
    elif all(r.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT for r in project.releases):
        delete_form = await form.render(
            model_cls=shared.projects.ArchiveSelectedProject,
            action=util.as_url(post.projects.archive),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-sm btn-outline-secondary",
            submit_label="Archive project",
            empty=True,
            defaults={"project_key": str(project.key)},
            confirm=(
                f"This project has {util.plural(len(project.releases), 'draft release')}."
                " Archiving will delete those drafts and mark the project as retired."
                " Continue?"
            ),
        )
    if delete_form:
        section.div(".my-3")[delete_form]


async def _render_finish_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Release policy - Finish options"]
    ]

    announce_release_subject_widget = _input_with_variables(
        field_name="announce_release_subject",
        default_value=project.policy_announce_release_subject or "",
        template_variables=construct.announce_subject_template_variables(),
        documentation="Subject line template for announcement emails.",
    )

    announce_release_template_widget = _textarea_with_variables(
        field_name="announce_release_template",
        default_value=project.policy_announce_release_template or "",
        template_variables=construct.announce_template_variables(),
        rows=18,
        documentation="Email template for messages to announce a finished release.",
    )

    with card.block(htm.div, classes=".card-body") as card_body:
        await form.render_block(
            card_body,
            model_cls=shared.projects.FinishPolicyForm,
            action=_view_action(project, "finish"),
            submit_label="Save",
            defaults={
                "project_key": project.key,
                "announce_release_subject": project.policy_announce_release_subject or "",
                "announce_release_template": project.policy_announce_release_template or "",
                "preserve_download_files": project.policy_preserve_download_files,
                "archive_prior_release": project.policy_auto_archive_prior_release,
            },
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            # wider_widgets=True,
            textarea_rows=10,
            custom={
                "announce_release_subject": announce_release_subject_widget,
                "announce_release_template": announce_release_template_widget,
            },
            skip=["archive_prior_release"] if not project.cycle_match else [],
        )
    return card.collect()


def _render_languages_section(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Programming languages"]]

    current_languages = project.programming_languages.split(", ") if project.programming_languages else []
    language_badges = []
    for lang in current_languages:
        # Manual form as badges are not handled by the form system
        remove_button = htm.form(
            ".d-inline.m-0",
            method="post",
            action=_view_action(project, "metadata"),
        )[
            form.csrf_input(),
            htpy.input(type="hidden", name="project_key", value=str(project.key)),
            htpy.input(type="hidden", name="variant", value="remove_language"),
            htpy.input(type="hidden", name="language_to_remove", value=lang),
            htpy.button(".btn-close.btn-close-white.ms-1.page-remove-tag", type="submit", aria_label=f"Remove {lang}"),
        ]
        badge = htm.div(".badge.bg-success.d-inline-flex.align-items-center.px-2.py-1")[
            htm.span[lang],
            remove_button,
        ]
        language_badges.append(badge)

    add_form = htm.form(
        ".mb-3",
        method="post",
        action=_view_action(project, "metadata"),
    )[
        form.csrf_input(),
        htpy.input(type="hidden", name="project_key", value=str(project.key)),
        htpy.input(type="hidden", name="variant", value="add_language"),
        htm.div(".d-flex.align-items-center")[
            htpy.input(
                ".form-control.form-control-sm.me-2", type="text", name="language_to_add", placeholder="New language"
            ),
            htpy.button(".btn.btn-sm.btn-success.text-nowrap.pe-3", type="submit")[htpy.i(".bi.bi-plus"), " Add"],
        ],
    ]

    with card.block(htm.div, classes=".card-body") as card_body:
        card_body.append(add_form)
        if language_badges:
            card_body.append(htm.div(".d-flex.flex-wrap.gap-2.align-items-center.mt-3")[*language_badges])
    return card.collect()


async def _render_lifecycle_tab(project: sql.Project, *, can_edit: bool) -> htm.Element:
    block = htm.Block()
    if can_edit:
        block.append(await _render_version_scheme_form(project))
    return block.collect()


def _render_metadata_card(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Reference metadata"]]

    rows: list[htm.Element] = []
    for label, url in [
        ("Homepage", project.homepage),
        ("Lifecycle page", project.lifecycle_page),
        ("Download page", project.download_page),
        ("Bug database", project.bug_database),
        ("Mailing lists", project.mailing_lists),
    ]:
        if url:
            rows.append(
                htm.tr[
                    htm.th(".border-0.w-25")[label],
                    htm.td(".text-break.border-0")[htm.a(href=url, target="_blank", rel="noopener")[url]],
                ]
            )

    for label, urls in [("Repositories", project.repository), ("Standards", project.standards)]:
        if urls:
            rows.append(
                htm.tr[
                    htm.th(".border-0.w-25")[label],
                    htm.td(".text-break.border-0")[
                        htm.ul(".mb-0.ps-3")[*[htm.li[htm.a(href=u, target="_blank", rel="noopener")[u]] for u in urls]]
                    ],
                ]
            )

    if rows:
        card.div(".card-body")[htm.table(".table.mb-0")[htm.tbody[*rows]]]
    else:
        card.div(".card-body")[htm.div(".text-muted.fst-italic")["No reference metadata set."]]
    return card.collect()


async def _render_metadata_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-0")["Reference metadata"]]
    with card.block(htm.div, classes=".card-body") as body:
        await form.render_block(
            body,
            model_cls=shared.projects.EditMetadataForm,
            action=_view_action(project, "metadata"),
            submit_label="Save",
            defaults={
                "project_key": str(project.key),
                "display_name": (project.name or "").removeprefix("Apache "),
                "description": project.description or "",
                "short_description": project.short_description or "",
                "homepage": project.homepage or "",
                "lifecycle_page": project.lifecycle_page or "",
                "download_page": project.download_page or "",
                "bug_database": project.bug_database or "",
                "mailing_lists": project.mailing_lists or "",
                "repository": "\n".join(project.repository),
                "standards": "\n".join(project.standards),
            },
        )
    return card.collect()


async def _render_metadata_tab(project: sql.Project, *, can_edit: bool) -> htm.Element:
    block = htm.Block()
    if can_edit:
        block.append(await _render_metadata_form(project))
        block.append(_render_categories_section(project))
        block.append(_render_languages_section(project))
    else:
        block.append(_render_metadata_card(project))
    return block.collect()


def _render_pmc_card(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["PMC"]]
    if project.committee:
        committee_link = htm.a(href=util.as_url(committees.view, name=project.committee.key))[
            project.committee.display_name
        ]
        card.div(".card-body")[htm.div(".d-flex.flex-wrap.gap-3.small.mb-1")[committee_link]]
    else:
        card.div(".card-body")[htm.div(".d-flex.flex-wrap.gap-3.small.mb-1")["No committee"]]
    return card.collect()


def _render_policy_readonly(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Release policy"]]

    email_content = (
        htm.a(href=f"mailto:{project.policy_mailto_addresses[0]}")[project.policy_mailto_addresses[0]]
        if project.policy_mailto_addresses
        else "Not set"
    )

    tbody = htm.tbody[
        htm.tr[
            htm.th(".border-0.w-25")["Email"],
            htm.td(".text-break.border-0")[email_content],
        ],
        htm.tr[
            htm.th(".border-0")["Vote mode"],
            htm.td(".text-break.border-0")[_vote_mode_label(project.policy_vote_mode)],
        ],
        htm.tr[
            htm.th(".border-0")["Minimum voting period"],
            htm.td(".text-break.border-0")[f"{project.policy_min_hours}h"],
        ],
    ]

    card.div(".card-body")[htm.div(".card.h-100.border")[htm.div(".card-body")[htm.table(".table.mb-0")[tbody]]]]
    return card.collect()


def _render_project_label_card(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Project key"]]
    card.div(".card-body")[htm.code(".fs-6")[str(project.key)]]
    return card.collect()


def _release_url(project: sql.Project, release: sql.Release) -> str:
    project_key = str(project.key)
    version_key = str(release.version)
    match release.phase:
        case sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            return util.as_url(compose.selected, project_key=project_key, version_key=version_key)
        case sql.ReleasePhase.RELEASE_CANDIDATE:
            return util.as_url(vote.selected, project_key=project_key, version_key=version_key)
        case sql.ReleasePhase.RELEASE_PREVIEW:
            return util.as_url(finish.selected, project_key=project_key, version_key=version_key)
        case sql.ReleasePhase.RELEASE:
            return util.as_url(file.selected, project_key=project_key, version_key=version_key)


async def _render_releases_sections(
    project: sql.Project,
    candidate_drafts: list[sql.Release],
    candidates: list[sql.Release],
    previews: list[sql.Release],
    full_releases: list[sql.Release],
    *,
    nested: bool = False,
) -> htm.Element:
    # When `nested` is true the section sits beneath a cycle heading (h2), so
    # we demote the section titles to h3. Otherwise keep them as h2 so the
    # tab content has top-level structure.
    heading = htm.h3 if nested else htm.h2
    sections = htm.Block(htm.div)

    if candidate_drafts:
        sections.append(heading["Draft candidate releases"])
        draft_buttons = []
        for drf in candidate_drafts:
            file_count = await util.number_of_release_files(drf)
            draft_buttons.append(
                htm.a(
                    ".btn.btn-sm.btn-outline-secondary.py-2.px-3",
                    href=_release_url(project, drf),
                    title=f"View draft {project.key} {drf.version}",
                )[
                    f"{project.key} {drf.version} ",
                    htm.span(".badge.bg-secondary.ms-2")[util.plural(file_count, "file")],
                ]
            )
        sections.div(".d-flex.flex-wrap.gap-2.mb-4")[*draft_buttons]

    if candidates:
        sections.append(heading["Candidate releases"])
        candidate_buttons = []
        for cnd in candidates:
            file_count = await util.number_of_release_files(cnd)
            candidate_buttons.append(
                htm.a(
                    ".btn.btn-sm.btn-outline-info.py-2.px-3",
                    href=_release_url(project, cnd),
                    title=f"View candidate {project.key} {cnd.version}",
                )[
                    f"{project.key} {cnd.version} ",
                    htm.span(".badge.bg-info.ms-2")[util.plural(file_count, "file")],
                ]
            )
        sections.div(".d-flex.flex-wrap.gap-2.mb-4")[*candidate_buttons]

    if previews:
        sections.append(heading["Preview releases"])
        preview_buttons = []
        for prv in previews:
            file_count = await util.number_of_release_files(prv)
            preview_buttons.append(
                htm.a(
                    ".btn.btn-sm.btn-outline-warning.py-2.px-3",
                    href=_release_url(project, prv),
                    title=f"View preview {project.key} {prv.version}",
                )[
                    f"{project.key} {prv.version} ",
                    htm.span(".badge.bg-warning.ms-2")[util.plural(file_count, "file")],
                ]
            )
        sections.div(".d-flex.flex-wrap.gap-2.mb-4")[*preview_buttons]

    if full_releases:
        sections.append(heading["Full releases"])
        release_buttons = []
        for rel in full_releases:
            file_count = await util.number_of_release_files(rel)
            release_buttons.append(
                htm.a(
                    ".btn.btn-sm.btn-outline-success.py-2.px-3",
                    href=_release_url(project, rel),
                    title=f"View release {project.key} {rel.version}",
                )[
                    f"{project.key} {rel.version} ",
                    htm.span(".badge.bg-success.ms-2")[util.plural(file_count, "file")],
                ]
            )
        sections.div(".d-flex.flex-wrap.gap-2.mb-4")[*release_buttons]

    return sections.collect()


async def _render_releases_tab(
    project: sql.Project,
    cycles: list[sql.ProjectCycle],
    *,
    can_edit: bool,
) -> htm.Element:
    block = htm.Block()
    if can_edit:
        block.p(".mb-4")[
            htm.a(
                ".btn.btn-sm.btn-outline-primary",
                href=util.as_url(start.selected, project_key=str(project.key)),
            )["Start a new release"]
        ]

    if not project.releases:
        block.p(".text-muted.mb-4")["No releases found."]

    # Stay flat for projects with only the implicit "default" cycle and no
    # cycle dates set. Once cycles get used or dates get filled in, headings
    # surface automatically. The card / form surfaces whenever can_edit, so a
    # PMC of a simple-default project can still set eod/eos/eol/lts.
    show_cycle_heading = (len(cycles) > 1) or any(c.cycle != "default" for c in cycles)

    # Newest first, as the old per-phase queries returned them.
    releases = sorted(project.releases, key=lambda r: r.created, reverse=True)

    for cycle in cycles:
        cycle_drafts = [
            r
            for r in releases
            if r.cycle_key == cycle.cycle_key and r.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
        ]
        cycle_candidates = [
            r for r in releases if r.cycle_key == cycle.cycle_key and r.phase == sql.ReleasePhase.RELEASE_CANDIDATE
        ]
        cycle_previews = [
            r for r in releases if r.cycle_key == cycle.cycle_key and r.phase == sql.ReleasePhase.RELEASE_PREVIEW
        ]
        cycle_full = [r for r in releases if r.cycle_key == cycle.cycle_key and r.phase == sql.ReleasePhase.RELEASE]

        cycle_has_dates = _cycle_has_dates(cycle)
        cycle_has_releases = bool(cycle_drafts or cycle_candidates or cycle_previews or cycle_full)
        if not (cycle_has_dates or cycle_has_releases or show_cycle_heading or can_edit):
            continue

        if show_cycle_heading:
            block.h2(".mt-4.mb-3")[_cycle_display_name(cycle)]

        # Skip cycle dates UI for the default cycle - it's the catch-all for
        # projects without cycle_match and shouldn't carry lifecycle dates.
        if cycle.cycle != "default":
            if can_edit:
                block.append(await _render_cycle_dates_form(project, cycle))
            elif cycle_has_dates:
                block.append(_render_cycle_dates_card(cycle))

        block.append(
            await _render_releases_sections(
                project, cycle_drafts, cycle_candidates, cycle_previews, cycle_full, nested=show_cycle_heading
            )
        )
        if not cycle_has_releases and show_cycle_heading:
            block.p(".text-muted.mb-4")["No releases."]

    return block.collect()


async def _render_trusted_publishing_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Release policy - Trusted Publishing"]
    ]

    with card.block(htm.div, classes=".card-body") as card_body:
        card_body.p(".text-muted.mb-3")[
            "See the ",
            htm.a(href=util.as_url(docs.page, path="trusted-publishing"))["Trusted Publishing guide"],
            " for an explanation of these settings.",
        ]
        await form.render_block(
            card_body,
            model_cls=shared.projects.TrustedPublishingPolicyForm,
            action=_view_action(project, "trusted-publishing"),
            submit_label="Save",
            defaults={
                "project_key": str(project.key),
                "github_repository_name": project.policy_github_repository_name or "",
                "github_repository_branch": project.policy_github_repository_branch or "",
                "github_compose_workflow_path": "\n".join(project.policy_github_compose_workflow_path),
                "github_vote_workflow_path": "\n".join(project.policy_github_vote_workflow_path),
                "github_finish_workflow_path": "\n".join(project.policy_github_finish_workflow_path),
            },
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            textarea_rows=5,
        )
    return card.collect()


async def _render_version_scheme_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-0")["Version scheme"]]
    with card.block(htm.div, classes=".card-body") as body:
        await form.render_block(
            body,
            model_cls=shared.projects.EditVersionSchemeForm,
            action=_view_action(project, "lifecycle"),
            submit_label="Save",
            defaults={
                "project_key": str(project.key),
                "version_method": project.version_method,
                "version_pattern": project.version_pattern or "",
                "cycle_match": project.cycle_match or "",
                "branch_template": project.branch_template or "",
            },
        )
    return card.collect()


async def _render_vote_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Release policy - Vote options"]
    ]

    defaults_dict = {
        "project_key": str(project.key),
        "mailto_addresses": project.policy_mailto_addresses[0]
        if project.policy_mailto_addresses
        else f"dev@{project.key}.apache.org",
        "vote_mode": project.policy_vote_mode,
        "min_hours": project.policy_min_hours,
        "release_checklist": project.policy_release_checklist or "",
        "vote_comment_template": project.policy_vote_comment_template or "",
        "start_vote_subject": project.policy_start_vote_subject or "",
        "start_vote_template": project.policy_start_vote_template or "",
    }

    release_checklist_widget = _textarea_with_variables(
        field_name="release_checklist",
        default_value=project.policy_release_checklist or "",
        template_variables=construct.checklist_template_variables(),
        rows=3,
        documentation="Markdown text describing how to test release candidates.",
    )

    start_vote_subject_widget = _input_with_variables(
        field_name="start_vote_subject",
        default_value=project.policy_start_vote_subject or "",
        template_variables=construct.vote_subject_template_variables(),
        documentation="Subject line template for vote emails.",
    )

    start_vote_template_widget = _textarea_with_variables(
        field_name="start_vote_template",
        default_value=project.policy_start_vote_template or "",
        template_variables=construct.vote_template_variables(),
        rows=18,
        documentation="Email template for messages to start a vote on a release.",
    )
    vote_mode_widget = _vote_mode_radios(project)

    with card.block(htm.div, classes=".card-body") as card_body:
        await form.render_block(
            card_body,
            model_cls=shared.projects.VotePolicyForm,
            action=_view_action(project, "vote"),
            submit_label="Save",
            defaults=defaults_dict,
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            # wider_widgets=True,
            textarea_rows=10,
            custom={
                "release_checklist": release_checklist_widget,
                "start_vote_subject": start_vote_subject_widget,
                "start_vote_template": start_vote_template_widget,
                "vote_mode": vote_mode_widget,
            },
        )
    return card.collect()


def _textarea_with_variables(
    field_name: str,
    default_value: str,
    template_variables: list[tuple[str, str]],
    rows: int = 10,
    documentation: str | None = None,
) -> htm.Element:
    textarea = htpy.textarea(
        f"#{field_name}.form-control.font-monospace",
        name=field_name,
        rows=str(rows),
    )[default_value]

    variable_rows = []
    for name, description in template_variables:
        variable_rows.append(
            htm.tr[
                htm.td(".font-monospace.text-nowrap.py-1")[f"{{{{{name}}}}}"],
                htm.td(".py-1")[description],
                htm.td(".text-end.py-1")[
                    htpy.button(
                        ".btn.btn-sm.btn-outline-secondary.copy-var-btn",
                        type="button",
                        data_variable=f"{{{{{name}}}}}",
                    )["Copy"]
                ],
            ]
        )

    variables_table = htm.table(".table.table-sm.mb-0")[
        htm.thead[
            htm.tr[
                htm.th(".py-1")["Variable"],
                htm.th(".py-1")["Description"],
                htm.th(".py-1")[""],
            ]
        ],
        htm.tbody[*variable_rows],
    ]

    details = htm.details(".mt-2")[
        htm.summary(".text-muted")["Available template variables"],
        htm.div(".mt-2")[variables_table],
    ]

    elements: list[htm.Element | htm.VoidElement] = [textarea]
    if documentation:
        elements.append(htm.div(".text-muted.mt-1.form-text")[documentation])
    elements.append(details)

    return htm.div[elements]


def _view_action(project: sql.Project, tab: str) -> str:
    return util.as_url(post.projects.view, name=str(project.key)) + f"?tab={tab}"


def _vote_mode_label(mode: sql.VoteMode) -> str:
    return mode.value.capitalize()


def _vote_mode_radios(project: sql.Project) -> htm.Element:
    choices = list(sql.VoteMode)
    if project.committee and project.committee.is_podling:
        choices = [mode for mode in choices if mode != sql.VoteMode.MANUAL]
    elements: list[htm.Element | htm.VoidElement] = []
    if project.policy_vote_mode not in choices:
        elements.append(
            htm.div(".alert.alert-warning.mb-3")[
                f"The current vote mode, {_vote_mode_label(project.policy_vote_mode)}, "
                "is not available for this project."
            ]
        )
    radios = []
    for mode in choices:
        radio_id = f"vote_mode_{mode.value}"
        attrs: dict[str, str] = {
            "type": "radio",
            "name": "vote_mode",
            "id": radio_id,
            "value": mode.value,
            "class_": "form-check-input",
            "required": "",
        }
        if mode == project.policy_vote_mode:
            attrs["checked"] = ""
        radios.append(
            htpy.div(".form-check")[
                htpy.input(**attrs),
                htpy.label(".form-check-label", for_=radio_id)[_vote_mode_label(mode)],
            ]
        )
    elements.append(htm.div("#vote_mode")[*radios])
    return htm.div[*elements]
