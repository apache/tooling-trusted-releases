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
from typing import Any, Literal

import asfquart.base as base
import htpy
import quart
import strictyaml

import atr.blueprints.get as get
import atr.config as config
import atr.construct as construct
import atr.cycles as cycles
import atr.db as db
import atr.db.interaction as interaction
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
import atr.sessions as sessions
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
async def project_yaml(
    session: web.Committer, _project_yaml: Literal["project/yaml"], project_key: safe.ProjectKey
) -> web.WerkzeugResponse | str:
    """
    URL: /project/yaml/<project_key>
    """
    async with db.session() as data:
        project = await data.project(
            key=str(project_key),
            _committee=True,
            _release_policy=True,
        ).demand(base.ASFQuartException(f"Project {project_key} not found", errorcode=404))

    is_committee_member = bool(project.committee and user.is_committee_member(project.committee, session.uid))
    if not (is_committee_member or session.is_admin):
        raise base.ASFQuartException("You are not a member of this project's committee", errorcode=403)

    yaml_text = _asf_yaml_export(project)

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(view, project_key=project.key))["← Back to project"]]
    page.h1["Export .asf.yaml"]
    page.p[
        "This is the ",
        htm.code[".asf.yaml"],
        " project block describing what ATR currently holds for ",
        project.display_name,
        ". Add it to your repository's ",
        htm.code[".asf.yaml"],
        " to keep this metadata in sync from the repository side.",
    ]

    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")[".asf.yaml"],
        htm.button(
            ".btn.btn-sm.btn-outline-secondary.atr-copy-btn",
            type="button",
            data_clipboard_target="#asf-yaml-output",
        )[htm.i(".bi.bi-clipboard"), " Copy"],
    ]
    card.div(".card-body")[htm.pre("#asf-yaml-output.mb-0")[yaml_text]]
    page.append(card.collect())

    return await template.blank(
        title="Export .asf.yaml",
        description=f"The .asf.yaml project block for {project.display_name}.",
        content=page.collect(),
        javascripts=["clipboard-copy"],
    )


@get.typed
async def projects(session: web.Committer, _projects: Literal["projects"]) -> str:
    """
    URL: /projects
    Main project directory page.
    """
    async with db.session() as data:
        projects = await data.project(_committee=True).order_by(sql.Project.name).all()
        approvals = await data.approval_request(
            status_in=[sql.ApprovalStatus.PENDING, sql.ApprovalStatus.APPROVED]
        ).all()
        release_counts = await interaction.project_release_counts(data)

    # Release archival requests surface on the release's own file page, not here
    approvals_by_project = {a.project_key: a for a in approvals if a.action != sql.ApprovalAction.ARCHIVE_RELEASE}

    committee_project_counts: Counter[str] = Counter(
        str(p.committee.key) for p in projects if p.committee and p.is_active
    )

    action_forms: dict[str, htm.Element] = {}
    for project in projects:
        if not project.is_active:
            continue
        if not (user.is_committee_member(project.committee, session.uid) or session.is_admin):
            continue
        approval = approvals_by_project.get(str(project.key))
        if approval is not None:
            action_forms[str(project.key)] = await _approval_request_element(approval)
            continue
        if project.committee and committee_project_counts[str(project.committee.key)] <= 1:
            continue
        total_releases, active_releases = release_counts.get(str(project.key), (0, 0))
        if total_releases == 0:
            action_forms[str(project.key)] = await form.render(
                model_cls=shared.projects.DeleteSelectedProject,
                action=util.as_url(post.projects.delete),
                form_classes=".d-inline-block.m-0",
                submit_classes="btn-sm btn-outline-danger",
                submit_label="Request deletion",
                empty=True,
                defaults={"project_key": str(project.key)},
                confirm=(
                    "This starts a binding CAP approval vote by the committee PMC. ATR will mark the project"
                    " ready to delete only if the vote passes, and you must then return to complete it."
                ),
            )
        elif active_releases == 0:
            action_forms[str(project.key)] = await form.render(
                model_cls=shared.projects.ArchiveSelectedProject,
                action=util.as_url(post.projects.archive),
                form_classes=".d-inline-block.m-0",
                submit_classes="btn-sm btn-outline-secondary",
                submit_label="Request archival",
                empty=True,
                defaults={"project_key": str(project.key)},
                confirm=(
                    "This starts a binding CAP approval vote by the committee PMC. ATR will mark the project"
                    " ready to archive only if the vote passes, and you must then return to complete it, which"
                    " deletes any draft releases and retires the project."
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
            # This is deliberately wider than the release manager role because it only decides which projects to show
            user_projects = [
                p
                for p in all_projects
                if p.committee
                and (
                    (test_mode and (p.committee.key == "test"))
                    or (session.uid in p.committee.committee_members)
                    or (session.uid in p.committee.committers)
                    or user.is_release_manager(p.committee, session.uid)
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
            _committee_signing_certificates=True,
            _release_policy=True,
            _releases=True,
        ).demand(base.ASFQuartException(f"Project {project_key} not found", errorcode=404))
        project_cycles = list(await data.project_cycle(project_key=str(project_key)).all())
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
    is_release_manager = bool(project.committee and user.is_release_manager(project.committee, session.uid))
    is_privileged = session.is_admin
    can_edit_metadata = (is_committee_member or is_release_manager or is_privileged) and (
        project.status != sql.ProjectStatus.RETIRED
    )
    can_edit_policy = (is_committee_member or is_release_manager or is_privileged) and (
        project.status == sql.ProjectStatus.ACTIVE
    )
    can_manage_taxonomy = (is_committee_member or is_privileged) and (project.status != sql.ProjectStatus.RETIRED)
    can_manage_project_actions = is_committee_member or is_privileged
    can_start_release = (is_committee_member or is_privileged) and (project.status != sql.ProjectStatus.RETIRED)
    is_sole_active_project = (project.committee is not None) and (active_committee_projects <= 1)

    if user.can_view_embargoed_release(project.committee, session.uid, is_member=session.is_member):
        visible_releases = project.releases_including_embargoed
    else:
        visible_releases = project.releases

    released_cycle_keys = {r.cycle_key for r in project.releases_including_embargoed}
    visible_cycle_keys = {r.cycle_key for r in visible_releases}
    project_cycles = [
        c for c in project_cycles if (c.cycle_key not in released_cycle_keys) or (c.cycle_key in visible_cycle_keys)
    ]

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
        if (not project.is_active)
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
                        f"Last updated {project.updated.strftime('%Y-%m-%d')} by {project.updated_by_display}"
                    ]
                ]
            ]
        )
    page.append(
        await _render_header_cards(project, can_manage_project_actions, session, is_sole_active_project, is_privileged)
    )

    tabs = await _generate_tabs(
        can_edit_metadata,
        can_edit_policy,
        can_manage_taxonomy,
        can_start_release,
        project,
        project_cycles,
        visible_releases,
    )
    page.append(tabs)

    content = page.collect()

    javascripts = ["copy-variable", "version-scheme-toggle"] if can_edit_policy else []
    return await template.blank(
        title=f"{project.display_name}",
        description=f"Information regarding {project.display_name}.",
        content=content,
        javascripts=javascripts,
    )


async def _approval_request_element(approval: sql.ApprovalRequest) -> htm.Element:
    verb = approval.action.value
    if approval.status == sql.ApprovalStatus.APPROVED:
        return await form.render(
            shared.projects.CompleteApprovalRequest,
            action=util.as_url(post.projects.complete_approval),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-sm btn-outline-danger",
            submit_label=f"Complete {verb}",
            empty=True,
            defaults={"approval_request_id": approval.id},
            confirm=f"The CAP approval vote passed. This will {verb} the project now. Continue?",
        )
    closes = approval.closes_at.strftime("%Y-%m-%d %H:%M UTC")
    question_url = f"{config.get().CAP_API_BASE_URL.rstrip('/')}/#/question/{approval.cap_question_id}"
    return htm.p(".text-muted.small.mb-0")[
        "CAP approval vote ",
        htm.a(href=question_url)[f"#{approval.cap_question_id}"],
        f" to {verb} in progress (ends {closes}).",
    ]


def _asf_yaml_export(project: sql.Project) -> str:
    block: dict[str, object] = {"metadata": _asf_yaml_metadata(project)}
    policy = _asf_yaml_policy(project)
    if policy:
        block["policy"] = policy
    # atr_sync defaults to on, so we emit it as a visible reminder the sync exists
    block["features"] = {"atr_sync": "true"}
    return strictyaml.as_document({"project": block}).as_yaml()


def _asf_yaml_metadata(project: sql.Project) -> dict[str, object]:
    metadata: dict[str, object] = {"key": str(project.key)}
    if project.committee_key:
        metadata["committee"] = project.committee_key
    if project.name:
        metadata["name"] = project.name
    for field in (
        "description",
        "short_description",
        "homepage",
        "lifecycle_page",
        "download_page",
        "bug_database",
        "mailing_lists",
        "security_contact",
        "threat_model_link",
        "threat_model_src_link",
    ):
        value = getattr(project, field)
        if value:
            metadata[field] = value
    if project.repositories:
        metadata["repositories"] = list(project.repositories)
    if project.standards:
        metadata["standards"] = list(project.standards)
    # categories and programming_languages are stored comma-joined, so split them back out
    for field, raw in (("categories", project.categories), ("programming_languages", project.programming_languages)):
        items = [item.strip() for item in (raw or "").split(",") if item.strip()]
        if items:
            metadata[field] = items
    metadata.update(_asf_yaml_version_scheme(project))
    return metadata


def _asf_yaml_policy(project: sql.Project) -> dict[str, object]:
    release_policy = project.release_policy
    if release_policy is None:
        return {}
    policy = _asf_yaml_policy_fields(release_policy)
    policy.update(_asf_yaml_policy_extras(release_policy))
    policy.update(_asf_yaml_recipient_blocks(project))
    return policy


def _asf_yaml_policy_extras(release_policy: sql.ReleasePolicy) -> dict[str, object]:
    # Fields that need converting rather than passing straight through. strictyaml serialises
    # only strings, so the int and bool go out as text.
    extras: dict[str, object] = {}
    if release_policy.file_tag_mappings:
        extras["file_tag_mappings"] = {tag: list(paths) for tag, paths in release_policy.file_tag_mappings.items()}
    if release_policy.license_check_mode != sql.LicenseCheckMode.BOTH:
        extras["license_check_mode"] = release_policy.license_check_mode.value
    if release_policy.vote_mode != sql.VoteMode.EMAIL:
        extras["vote_mode"] = release_policy.vote_mode.value
    if release_policy.min_hours is not None:
        extras["min_hours"] = str(release_policy.min_hours)
    return extras


def _asf_yaml_policy_fields(release_policy: sql.ReleasePolicy) -> dict[str, object]:
    # Read release_policy directly, not the policy_* properties - those fall back to the
    # default templates, and we only want fields that have actually been set.
    fields: dict[str, object] = {}
    for field in (
        "announce_release_subject",
        "announce_release_template",
        "start_vote_subject",
        "start_vote_template",
        "finish_vote_template",
        "vote_comment_template",
        "release_checklist",
        "github_repository_name",
        "github_repository_branch",
        "download_path_suffix",
    ):
        value = getattr(release_policy, field)
        if value:
            fields[field] = value
    for field in (
        "binary_artifact_paths",
        "source_artifact_paths",
        "source_excludes_lightweight",
        "source_excludes_rat",
        "github_compose_workflow_path",
        "github_vote_workflow_path",
        "github_finish_workflow_path",
    ):
        value = getattr(release_policy, field)
        if value:
            fields[field] = list(value)
    return fields


def _asf_yaml_recipient_blocks(project: sql.Project) -> dict[str, object]:
    policy: dict[str, object] = {}
    for key, action in (
        ("vote_recipients", sql.RecipientAction.VOTE),
        ("announce_recipients", sql.RecipientAction.ANNOUNCE),
    ):
        to, cc, bcc = project.policy_recipients(action)
        recipients: dict[str, object] = {}
        if to:
            recipients["to"] = to
        if cc:
            recipients["cc"] = list(cc)
        if bcc:
            recipients["bcc"] = list(bcc)
        if recipients:
            policy[key] = recipients
    return policy


def _asf_yaml_version_scheme(project: sql.Project) -> dict[str, object]:
    scheme: dict[str, object] = {}
    if project.version_method != sql.VersionMethod.SIMPLE:
        scheme["version_method"] = project.version_method.value
    if project.version_pattern:
        scheme["version_pattern"] = project.version_pattern
    # A calver project authors its date format, and cycle_match is compiled from
    # it, so we emit whichever of the two the project actually sets
    if project.version_method == sql.VersionMethod.CALVER:
        if project.calver_format:
            scheme["calver_format"] = project.calver_format
    elif project.cycle_match:
        scheme["cycle_match"] = project.cycle_match
    if project.branch_template:
        scheme["branch_template"] = project.branch_template
    return scheme


def _cycle_has_dates(cycle: sql.ProjectCycle) -> bool:
    return any(getattr(cycle, attr) is not None for attr in ("start", "begin", "latest", "eod", "eos", "eol"))


async def _delete_form(project: sql.Project) -> htm.Element | None:
    async with db.session() as data:
        approval = await data.approval_request(
            project_key=str(project.key),
            status_in=[sql.ApprovalStatus.PENDING, sql.ApprovalStatus.APPROVED],
            release_version=None,
        ).get()
    if approval is not None:
        return await _approval_request_element(approval)
    delete_form = None
    releases = project.releases_including_embargoed
    if not releases:
        delete_form = await form.render(
            shared.projects.DeleteSelectedProject,
            action=util.as_url(post.projects.delete),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-sm btn-outline-danger",
            submit_label="Request deletion",
            defaults={"project_key": str(project.key)},
            confirm=(
                "This starts a binding CAP approval vote by the committee PMC. ATR will mark the project"
                " ready to delete only if the vote passes, and you must then return to complete it."
            ),
            empty=True,
        )
    elif not any((r.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT) and (not r.is_archived) for r in releases):
        delete_form = await form.render(
            model_cls=shared.projects.ArchiveSelectedProject,
            action=util.as_url(post.projects.archive),
            form_classes=".d-inline-block.m-0",
            submit_classes="btn-sm btn-outline-secondary",
            submit_label="Request archival",
            empty=True,
            defaults={"project_key": str(project.key)},
            confirm=(
                "This starts a binding CAP approval vote by the committee PMC. ATR will mark the project"
                " ready to archive only if the vote passes, and you must then return to complete it, which"
                " deletes any draft releases and retires the project."
            ),
        )
    return delete_form


def _embargoed_button_badges(release: sql.Release) -> list[htm.Element]:
    if not release.is_embargoed:
        return []
    return [htm.span(".badge.text-bg-danger.ms-2")["Embargoed"]]


def _flash_or_stored(flash_error_data: dict[str, Any], field_name: str, stored: str) -> str:
    for key in (field_name, f"!{field_name}"):
        datum = flash_error_data.get(key)
        if datum is not None:
            original = datum.get("original")
            return original if isinstance(original, str) else stored
    return stored


async def _generate_tabs(
    can_edit_metadata: bool,
    can_edit_policy: bool,
    can_manage_taxonomy: bool,
    can_start_release: bool,
    project: sql.Project,
    project_cycles: list[sql.ProjectCycle],
    releases: list[sql.Release],
) -> htpy.Element:
    tab_items: list[htm.Tab] = [
        htm.Tab(
            key="releases",
            label="Releases",
            render=lambda: _render_releases_tab(
                project,
                project_cycles,
                releases,
                can_edit_policy=can_edit_policy,
                can_start_release=can_start_release,
            ),
        ),
        htm.Tab(
            key="metadata",
            label="Metadata",
            render=lambda: _render_metadata_tab(
                project,
                can_edit_metadata=can_edit_metadata,
                can_manage_taxonomy=can_manage_taxonomy,
            ),
        ),
        htm.Tab(
            key="security",
            label="Security",
            render=lambda: _render_security_tab(project, can_edit_metadata=can_edit_metadata),
        ),
    ]

    if can_edit_policy:
        tab_items.append(
            htm.Tab(
                key="lifecycle",
                label="Lifecycle",
                render=lambda: _render_lifecycle_tab(project, can_edit_policy=can_edit_policy),
            )
        )

    if project.is_active:
        if can_edit_policy:
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


def _input_with_variables(
    field_name: str,
    value: str,
    template_variables: list[tuple[str, str]],
    documentation: str | None = None,
) -> htm.Element:
    text_input = htpy.input(
        f"#{field_name}.form-control.font-monospace",
        type="text",
        name=field_name,
        value=value,
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


def _optional_link(url: str | None) -> htm.Element | str:
    if not url:
        return "Not set"
    return htm.a(href=url)[url]


def _recipient_grid_widget(project: sql.Project, action: sql.RecipientAction) -> htm.Element:
    committee = project.committee
    committee_key = committee.key if (committee is not None) else str(project.key)
    is_podling = bool(committee is not None and committee.is_podling)
    options = util.configurable_recipients(action, committee_key, is_podling=is_podling)
    stored_to, stored_cc, stored_bcc = project.policy_recipients(action)
    # Include any already-stored recipients (eg set via .asf.yaml) so a saved
    # value remains a selectable option even when it's outside the committee
    # defaults.
    for address in (stored_to, *stored_cc, *stored_bcc):
        if address and (address not in options):
            options.append(address)
    default_to = stored_to if (stored_to in options) else (options[0] if options else None)
    return htm.div("#email_to")[
        render.html_recipients_to_radios(options, default_to=default_to),
        htpy.details(".mt-2")[
            htpy.summary["Select CC and BCC recipients"],
            render.html_recipients_cc_bcc_table(options, selected_cc=set(stored_cc), selected_bcc=set(stored_bcc)),
        ],
    ]


def _recipient_summary(to: str, cc: list[str], bcc: list[str]) -> htm.Element | str:
    if not to:
        return "Not set"
    extras = []
    if cc:
        extras.append(f"{len(cc)} CC")
    if bcc:
        extras.append(f"{len(bcc)} BCC")
    suffix = f" (+{', '.join(extras)})" if extras else ""
    return htm.span[htm.a(href=f"mailto:{to}")[to], suffix]


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


async def _render_actions_card(
    project: sql.Project, session: web.Committer, is_sole_active_project: bool, is_privileged: bool
) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Actions"]]
    with card.block(htm.div, classes=".card-body.d-flex.flex-column.align-items-start.gap-2") as body:
        if project.is_active and not is_sole_active_project:
            action_form = await _delete_form(project)
            if action_form:
                body.append(action_form)

        if project.committee:
            if (project.committee.key in session.member_committees) or is_privileged:
                body.append(
                    htm.a(
                        ".btn.btn-sm.btn-outline-primary",
                        href=util.as_url(add_project, committee_key=project.committee.key),
                    )["Create a sibling project"]
                )
                body.append(
                    htm.a(
                        ".btn.btn-sm.btn-outline-secondary",
                        href=util.as_url(project_yaml, project_key=project.key),
                    )["Export .asf.yaml"]
                )
    return card.collect()


def _render_categories_section(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Categories"]]

    current_categories = project.categories.split(", ") if project.categories else []
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
            action=_view_action(project, "releases"),
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


async def _render_finish_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Release policy - Finish options"]
    ]

    flash_error_data = await sessions.form_error_pop(quart.request.path)

    announce_release_subject_widget = _input_with_variables(
        field_name="announce_release_subject",
        value=_flash_or_stored(
            flash_error_data, "announce_release_subject", project.policy_announce_release_subject or ""
        ),
        template_variables=construct.announce_subject_template_variables(),
        documentation="Subject line template for announcement emails.",
    )

    announce_release_template_widget = _textarea_with_variables(
        field_name="announce_release_template",
        value=_flash_or_stored(
            flash_error_data, "announce_release_template", project.policy_announce_release_template or ""
        ),
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
                "archive_prior_release": project.policy_auto_archive_prior_release,
                "download_path_suffix": project.policy_download_path_suffix,
            },
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            # wider_widgets=True,
            textarea_rows=10,
            custom={
                "announce_release_subject": announce_release_subject_widget,
                "announce_release_template": announce_release_template_widget,
                "email_to": _recipient_grid_widget(project, sql.RecipientAction.ANNOUNCE),
            },
            skip=["email_cc", "email_bcc", *(["archive_prior_release"] if not project.cycle_match else [])],
            flash_error_data=flash_error_data,
        )
    return card.collect()


async def _render_header_cards(
    project: sql.Project,
    can_manage_project_actions: bool,
    session: web.Committer,
    is_sole_active_project: bool,
    is_privileged: bool,
) -> htm.Element:
    block = htm.Block(htm.div, classes=".row.row-cols-1.row-cols-md-2.row-cols-lg-3.row-cols-xl-3.g-4")
    block.div(".col")[_render_project_label_card(project)]
    block.div(".col")[_render_pmc_card(project)]
    if can_manage_project_actions:
        block.div(".col.col-sm-12.col-md-12.col-lg-4")[
            await _render_actions_card(project, session, is_sole_active_project, is_privileged)
        ]
    return block.collect()


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


async def _render_lifecycle_tab(project: sql.Project, *, can_edit_policy: bool) -> htm.Element:
    block = htm.Block()
    if can_edit_policy:
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

    for label, urls in [("Repositories", project.repositories), ("Standards", project.standards)]:
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
            defaults=shared.projects.edit_metadata_defaults(project),
        )
    return card.collect()


async def _render_metadata_tab(
    project: sql.Project,
    *,
    can_edit_metadata: bool,
    can_manage_taxonomy: bool,
) -> htm.Element:
    block = htm.Block()
    if can_edit_metadata:
        block.append(await _render_metadata_form(project))
    else:
        block.append(_render_metadata_card(project))
    if can_manage_taxonomy:
        block.append(_render_categories_section(project))
        block.append(_render_languages_section(project))
    return block.collect()


def _render_pmc_card(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["PMC"]]
    if project.committee:
        committee_link = htm.a(href=util.as_url(committees.view, name=project.committee.key))[
            project.committee.display_name
        ]
        # release_managers_link = htm.a(
        #     ".text-muted",
        #     href=util.as_url(committees.view, name=project.committee.key) + "#release-managers",
        # )["Release managers"]
        card.div(".card-body")[
            htm.div(".d-flex.flex-wrap.gap-3.small.mb-1")[committee_link],
            # htm.div(".small.text-muted")[release_managers_link],
        ]
    else:
        card.div(".card-body")[htm.div(".d-flex.flex-wrap.gap-3.small.mb-1")["No committee"]]
    return card.collect()


def _render_policy_readonly(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Release policy"]]

    vote_to, vote_cc, vote_bcc = project.policy_recipients(sql.RecipientAction.VOTE)
    announce_to, announce_cc, announce_bcc = project.policy_recipients(sql.RecipientAction.ANNOUNCE)

    tbody = htm.tbody[
        htm.tr[
            htm.th(".border-0.w-25")["Vote email"],
            htm.td(".text-break.border-0")[_recipient_summary(vote_to, vote_cc, vote_bcc)],
        ],
        htm.tr[
            htm.th(".border-0")["Announce email"],
            htm.td(".text-break.border-0")[_recipient_summary(announce_to, announce_cc, announce_bcc)],
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
                    *_embargoed_button_badges(drf),
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
                    *_embargoed_button_badges(cnd),
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
                    *_embargoed_button_badges(prv),
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
    project_cycles: list[sql.ProjectCycle],
    releases: list[sql.Release],
    *,
    can_edit_policy: bool,
    can_start_release: bool,
) -> htm.Element:
    block = htm.Block(htm.div, classes=".card.mb-4")
    block.div(".card-header.bg-light")[htm.h3(".mb-0")["Project releases"]]
    with block.block(htm.div, classes=".card-body") as body:
        if can_start_release:
            body.p(".mb-4")[
                htm.a(
                    ".btn.btn-sm.btn-outline-primary",
                    href=util.as_url(start.selected, project_key=str(project.key)),
                )["Start a new release"]
            ]

        if not releases:
            body.p(".text-muted.mb-4")["No releases found."]

        # Stay flat for projects with only the implicit "default" cycle and no
        # cycle dates set. Once cycles get used or dates get filled in, headings
        # surface automatically. The card / form surfaces whenever policy can be edited, so a
        # PMC of a simple-default project can still set eod/eos/eol/lts.
        show_cycle_heading = cycles.headings_needed(c.cycle for c in project_cycles)

        # Newest first, as the old per-phase queries returned them.
        releases = sorted(releases, key=lambda r: r.created, reverse=True)

        for cycle in project_cycles:
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
            cycle_released = [
                r for r in releases if r.cycle_key == cycle.cycle_key and r.phase == sql.ReleasePhase.RELEASE
            ]
            cycle_full = [r for r in cycle_released if not r.is_archived]

            cycle_has_dates = _cycle_has_dates(cycle)
            # A cycle with no releases is hidden, so this stays a list of releases and not of empty
            # versions. Archived releases still count, so a cycle whose releases have all been archived
            # keeps its place and its dates rather than vanishing
            cycle_has_releases = bool(cycle_drafts or cycle_candidates or cycle_previews or cycle_released)
            if not cycle_has_releases:
                continue

            if show_cycle_heading:
                body.h2(".mt-4.mb-3")[cycles.display_name(cycle.cycle)]

            # Skip cycle dates UI for the default cycle - it's the catch-all for
            # projects without cycle_match and shouldn't carry lifecycle dates.
            if cycle.cycle != cycles.DEFAULT_CYCLE:
                if can_edit_policy:
                    body.append(await _render_cycle_dates_form(project, cycle))
                elif cycle_has_dates:
                    body.append(_render_cycle_dates_card(cycle))

            body.append(
                await _render_releases_sections(
                    project, cycle_drafts, cycle_candidates, cycle_previews, cycle_full, nested=show_cycle_heading
                )
            )

    return block.collect()


async def _render_security_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-0")["Security"]]

    defaults_dict = {
        "project_key": str(project.key),
        "security_contact": project.security_contact or "security@apache.org",
        "threat_model_link": project.threat_model_link or "",
        "threat_model_src_link": project.threat_model_src_link or "",
    }

    with card.block(htm.div, classes=".card-body") as card_body:
        await form.render_block(
            card_body,
            model_cls=shared.projects.SecurityForm,
            action=_view_action(project, "security"),
            submit_label="Save",
            defaults=defaults_dict,
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            custom={
                "security_contact": _security_contact_radios(project),
            },
        )
    return card.collect()


def _render_security_readonly(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Security"]]

    contact = project.security_contact or "security@apache.org"
    tbody = htm.tbody[
        htm.tr[
            htm.th(".border-0.w-25")["Security contact"],
            htm.td(".text-break.border-0")[contact],
        ],
        htm.tr[
            htm.th(".border-0")["Threat model"],
            htm.td(".text-break.border-0")[_optional_link(project.threat_model_link)],
        ],
        htm.tr[
            htm.th(".border-0")["Threat model source"],
            htm.td(".text-break.border-0")[_optional_link(project.threat_model_src_link)],
        ],
    ]

    card.div(".card-body")[htm.div(".card.h-100.border")[htm.div(".card-body")[htm.table(".table.mb-0")[tbody]]]]
    return card.collect()


async def _render_security_tab(project: sql.Project, *, can_edit_metadata: bool) -> htm.Element:
    if can_edit_metadata:
        return await _render_security_form(project)
    return _render_security_readonly(project)


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
                "calver_format": project.calver_format or "",
                "branch_template": project.branch_template or "",
            },
        )
    return card.collect()


async def _render_vote_form(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Release policy - Vote options"]
    ]

    flash_error_data = await sessions.form_error_pop(quart.request.path)

    defaults_dict = {
        "project_key": str(project.key),
        "vote_mode": project.policy_vote_mode,
        "min_hours": project.policy_min_hours,
        "release_checklist": project.policy_release_checklist or "",
        "vote_comment_template": project.policy_vote_comment_template or "",
        "start_vote_subject": project.policy_start_vote_subject or "",
        "start_vote_template": project.policy_start_vote_template or "",
        "finish_vote_template": project.policy_finish_vote_template or "",
    }

    release_checklist_widget = _textarea_with_variables(
        field_name="release_checklist",
        value=_flash_or_stored(flash_error_data, "release_checklist", project.policy_release_checklist or ""),
        template_variables=construct.checklist_template_variables(),
        rows=3,
        documentation="Markdown text describing how to test release candidates.",
    )

    start_vote_subject_widget = _input_with_variables(
        field_name="start_vote_subject",
        value=_flash_or_stored(flash_error_data, "start_vote_subject", project.policy_start_vote_subject or ""),
        template_variables=construct.vote_subject_template_variables(),
        documentation="Subject line template for vote emails.",
    )

    start_vote_template_widget = _textarea_with_variables(
        field_name="start_vote_template",
        value=_flash_or_stored(flash_error_data, "start_vote_template", project.policy_start_vote_template or ""),
        template_variables=construct.vote_template_variables(),
        rows=18,
        documentation="Email template for messages to start a vote on a release.",
    )
    finish_vote_template_widget = _textarea_with_variables(
        field_name="finish_vote_template",
        value=_flash_or_stored(flash_error_data, "finish_vote_template", project.policy_finish_vote_template or ""),
        template_variables=construct.finish_vote_template_variables(),
        rows=18,
        documentation="Email template for vote resolution messages.",
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
                "email_to": _recipient_grid_widget(project, sql.RecipientAction.VOTE),
                "release_checklist": release_checklist_widget,
                "start_vote_subject": start_vote_subject_widget,
                "start_vote_template": start_vote_template_widget,
                "finish_vote_template": finish_vote_template_widget,
                "vote_mode": vote_mode_widget,
            },
            skip=["email_cc", "email_bcc"],
            flash_error_data=flash_error_data,
        )
    return card.collect()


def _security_contact_radios(project: sql.Project) -> htm.Element:
    committee = project.committee
    options = ["security@apache.org"]
    if committee is not None:
        options.append(f"security@{committee.key}.apache.org")
    selected = project.security_contact or "security@apache.org"
    radios: list[htm.Element] = []
    for address in options:
        radio_id = f"security_contact_{address}"
        attrs: dict[str, str] = {
            "type": "radio",
            "name": "security_contact",
            "id": radio_id,
            "value": address,
            "class_": "form-check-input",
            "required": "",
        }
        if address == selected:
            attrs["checked"] = ""
        radios.append(
            htpy.div(".form-check")[
                htpy.input(**attrs),
                htpy.label(".form-check-label", for_=radio_id)[address],
            ]
        )
    return htm.div("#security_contact")[*radios]


def _textarea_with_variables(
    field_name: str,
    value: str,
    template_variables: list[tuple[str, str]],
    rows: int = 10,
    documentation: str | None = None,
) -> htm.Element:
    textarea = htpy.textarea(
        f"#{field_name}.form-control.font-monospace",
        name=field_name,
        rows=str(rows),
    )[value]

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


def _vote_mode_description(mode: sql.VoteMode) -> str:
    match mode:
        case sql.VoteMode.MANUAL:
            return (
                "The vote is held entirely outside ATR. You provide the vote thread URL and record the result manually."
            )
        case sql.VoteMode.EMAIL:
            return (
                "ATR announces the vote, and votes are cast by replying to the thread."
                " ATR tabulates the replies when the vote is resolved."
            )
        case sql.VoteMode.TRUSTED:
            return (
                "ATR announces the vote, but votes are cast on the ATR vote page and"
                " recorded as ballots, with receipts sent to the thread."
                " The vote can be resolved automatically."
            )


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
                htm.div(".form-text.text-muted.mt-0.mb-2")[_vote_mode_description(mode)],
            ]
        )
    elements.append(htm.div("#vote_mode")[*radios])
    return htm.div[*elements]
