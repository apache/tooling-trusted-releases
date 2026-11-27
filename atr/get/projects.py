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

import dataclasses
import inspect
import urllib.parse
from collections import Counter
from typing import TYPE_CHECKING, Any, Final, Literal

import htpy
import quart
import strictyaml

import atr.blueprints.get as get
import atr.config as config
import atr.construct as construct
import atr.cycles as cycles
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
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
import atr.models.validation as validation
import atr.post as post
import atr.purl as purl
import atr.registry as registry
import atr.render as render
import atr.sessions as sessions
import atr.shared as shared
import atr.strings as strings
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# For each release settings form, the fields a cycle can override: the release policy field behind
# each one and, where that is the recipients, the action they are for
_COMPOSE_OVERRIDES: Final[dict[str, tuple[str, sql.RecipientAction | None]]] = {
    "license_check_mode": ("license_check_mode", None),
    "source_excludes_lightweight": ("source_excludes_lightweight", None),
    "source_excludes_rat": ("source_excludes_rat", None),
    "rat_excludes_url": ("rat_excludes_url", None),
}
_FINISH_OVERRIDES: Final[dict[str, tuple[str, sql.RecipientAction | None]]] = {
    "download_path_suffix": ("download_path_suffix", None),
    "archive_prior_release": ("auto_archive_prior_release", None),
    "announce_release_subject": ("announce_release_subject", None),
    "announce_release_template": ("announce_release_template", None),
    "announce_release_template_url": ("announce_release_template_url", None),
    "email_to": ("recipient_defaults", sql.RecipientAction.ANNOUNCE),
}
_VOTE_OVERRIDES: Final[dict[str, tuple[str, sql.RecipientAction | None]]] = {
    "email_to": ("recipient_defaults", sql.RecipientAction.VOTE),
    "min_hours": ("min_hours", None),
    "release_checklist": ("release_checklist", None),
    "vote_comment_template": ("vote_comment_template", None),
    "vote_comment_template_url": ("vote_comment_template_url", None),
    "start_vote_subject": ("start_vote_subject", None),
    "start_vote_template": ("start_vote_template", None),
    "start_vote_template_url": ("start_vote_template_url", None),
    "finish_vote_template": ("finish_vote_template", None),
    "finish_vote_template_url": ("finish_vote_template_url", None),
}
# The release settings tabs that a cycle can have its own values on
_CYCLE_TABS: Final[frozenset[str]] = frozenset({"compose", "finish", "vote"})
# A cycle can set a value to blank where its project has one. strictyaml has no way to write an
# empty list, mapping or string, so the export writes a marker and puts the YAML for it in afterwards
_YAML_BLANKS: Final[dict[str, str]] = {"[]": "ATR-BLANK-LIST", "{}": "ATR-BLANK-MAP", '""': "ATR-BLANK-TEXT"}


@dataclasses.dataclass(frozen=True)
class _SettingsScope:
    """Which cycle the release settings tabs show, and what each of the project's cycles overrides."""

    # Empty for the settings that every cycle uses
    cycle: str
    settings: sql.PolicySettings
    overrides: dict[str, sql.CycleOverrides]
    # The cycles the viewer can see a release in, newest first
    in_use: list[str]
    # Every cycle that some release is in
    matched: frozenset[str]
    selectable: bool
    # The cycle typed to give it settings before its first release, and what is wrong with it if anything
    entered: str = ""
    problem: str = ""

    @property
    def current(self) -> sql.CycleOverrides | None:
        return self.overrides.get(self.cycle) if self.cycle else None

    @property
    def overridable(self) -> list[str]:
        # Versions with no cycle of their own are in the default cycle, and use the project's settings
        return [cycle for cycle in self.in_use if cycle != cycles.DEFAULT_CYCLE]

    @property
    def unmatched(self) -> list[str]:
        return sorted(cycle for cycle in self.overrides if cycle not in self.matched)


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
            errors.ASFQuartException(f"Committee {committee_key!s} not found", errorcode=404)
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
async def advanced(
    session: web.Committer, _project_advanced: Literal["project/advanced"], project_key: safe.ProjectKey
) -> web.WerkzeugResponse | str:
    """
    URL: /project/advanced/<project_key>
    """
    async with db.session() as data:
        project = await data.project(key=str(project_key), _committee=True).demand(
            errors.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )
        options = await data.project_security_options(project_key=str(project_key)).get()

    is_committee_member = bool(project.committee and user.is_committee_member(project.committee, session.uid))
    if not (is_committee_member or session.is_admin):
        raise errors.ASFQuartException("You are not a member of this project's committee", errorcode=403)

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(view, project_key=project.key))["← Back to project"]]
    page.h1["Advanced settings"]
    page.p[
        f"Settings for {project.display_name} that are held in ATR and can't be set through ",
        htm.code[".asf.yaml"],
        ".",
    ]
    page.h2["GitHub OIDC restrictions"]
    page.p[
        "These optional settings restrict which GitHub Actions workflow runs can upload to this project. ",
        "With nothing set, any workflow on any branch or tag in the project's repositories can upload. See ",
        htm.a(href=util.as_url(docs.page, path="github-actions-uploads"))["Uploads from GitHub Actions"],
        ".",
    ]
    page.p[
        "They may not be secure without matching configuration on GitHub. A branch or tag restriction only has an ",
        "effect if that branch or tag is protected there, because anyone who can push to the repository could ",
        "otherwise create or move it.",
    ]
    await form.render_block(
        page,
        model_cls=shared.projects.UploadRestrictionsForm,
        action=util.as_url(post.projects.advanced, project_key=project.key),
        submit_label="Save",
        defaults=_upload_restriction_defaults(options),
        textarea_rows=3,
        readonly=project.is_archived,
    )

    return await template.blank(
        title="Advanced settings",
        description=f"Advanced settings for {project.display_name}.",
        content=page.collect(),
    )


@get.typed
async def archive(
    session: web.Committer, _project_archive: Literal["project/archive"], project_key: safe.ProjectKey
) -> web.WerkzeugResponse | str:
    """
    URL: /project/archive/<project_key>
    """
    return await _request_approval_page(session, project_key, sql.ApprovalAction.ARCHIVE)


@get.typed
async def delete(
    session: web.Committer, _project_delete: Literal["project/delete"], project_key: safe.ProjectKey
) -> web.WerkzeugResponse | str:
    """
    URL: /project/delete/<project_key>
    """
    return await _request_approval_page(session, project_key, sql.ApprovalAction.DELETE)


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
        ).demand(errors.ASFQuartException(f"Project {project_key} not found", errorcode=404))
        expected = await data.expected_distribution(project_key=str(project_key)).all()
        overrides = await data.cycle_overrides(project_key=str(project_key)).all()

    is_committee_member = bool(project.committee and user.is_committee_member(project.committee, session.uid))
    if not (is_committee_member or session.is_admin):
        raise errors.ASFQuartException("You are not a member of this project's committee", errorcode=403)

    full = project.update_type == sql.UpdateType.ASFYAML_FULL
    yaml_merge = _asf_yaml_export(project, expected, overrides, full=False)
    yaml_full = _asf_yaml_export(project, expected, overrides, full=True)

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
    page.append(_render_asf_yaml_mode_choice(full=full))

    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")[".asf.yaml"],
        htm.button(
            ".btn.btn-sm.btn-outline-secondary.atr-copy-btn",
            type="button",
            data_clipboard_target="#asf-yaml-output",
        )[htm.i(".bi.bi-clipboard"), " Copy"],
    ]
    card.div(".card-body")[
        htm.pre("#asf-yaml-output.mb-0", data_yaml_merge=yaml_merge, data_yaml_full=yaml_full)[
            yaml_full if full else yaml_merge
        ]
    ]
    page.append(card.collect())

    return await template.blank(
        title="Export .asf.yaml",
        description=f"The .asf.yaml project block for {project.display_name}.",
        content=page.collect(),
        javascripts=["clipboard-copy", "asf-yaml-mode"],
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

    approvals_by_project = {a.project_key: a for a in approvals}

    committee_project_counts: Counter[str] = Counter(
        str(p.committee.key) for p in projects if p.committee and p.is_active
    )

    action_forms: dict[str, htm.Element] = {}
    for project in projects:
        is_dormant = project.status == sql.ProjectStatus.DORMANT
        if not (project.is_active or is_dormant):
            continue
        if not (user.is_committee_member(project.committee, session.uid) or session.is_admin):
            continue
        approval = approvals_by_project.get(str(project.key))
        if approval is not None:
            action_forms[str(project.key)] = await _approval_request_element(approval)
            continue
        if is_dormant:
            action_forms[str(project.key)] = _approval_link(project, sql.ApprovalAction.UNARCHIVE)
            continue
        if project.committee and committee_project_counts[str(project.committee.key)] <= 1:
            continue
        total_releases, active_releases = release_counts.get(str(project.key), (0, 0))
        if total_releases == 0:
            action_forms[str(project.key)] = _approval_link(project, sql.ApprovalAction.DELETE)
        elif active_releases == 0:
            action_forms[str(project.key)] = _approval_link(project, sql.ApprovalAction.ARCHIVE)

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
async def unarchive(
    session: web.Committer, _project_unarchive: Literal["project/unarchive"], project_key: safe.ProjectKey
) -> web.WerkzeugResponse | str:
    """
    URL: /project/unarchive/<project_key>
    """
    return await _request_approval_page(session, project_key, sql.ApprovalAction.UNARCHIVE)


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
        ).demand(errors.ASFQuartException(f"Project {project_key} not found", errorcode=404))
        project_cycles = list(await data.project_cycle(project_key=str(project_key)).all())
        all_overrides = {
            overrides.cycle: overrides for overrides in await data.cycle_overrides(project_key=str(project_key)).all()
        }
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
    can_edit_metadata = (is_committee_member or is_release_manager or is_privileged) and (not project.is_archived)
    can_edit_policy = (is_committee_member or is_release_manager or is_privileged) and (
        project.status == sql.ProjectStatus.ACTIVE
    )
    can_manage_taxonomy = (is_committee_member or is_privileged) and (not project.is_archived)
    can_manage_project_actions = is_committee_member or is_privileged
    can_start_release = (is_committee_member or is_privileged) and (not project.is_archived)
    is_sole_active_project = (project.committee is not None) and (active_committee_projects <= 1)

    if user.can_view_embargoed_release(project.committee, session.uid, is_member=session.is_member):
        visible_releases = project.releases_including_embargoed
    else:
        visible_releases = project.releases

    released_cycle_keys = {r.cycle_key for r in project.releases_including_embargoed}
    visible_cycle_keys = {r.cycle_key for r in visible_releases}
    project_cycles = cycles.newest_first(
        c for c in project_cycles if (c.cycle_key not in released_cycle_keys) or (c.cycle_key in visible_cycle_keys)
    )
    scope = _settings_scope(project, project_cycles, visible_cycle_keys, released_cycle_keys, all_overrides)

    page = htm.Block()

    page_styles = """
        .page-remove-tag {
            font-size: 0.65em;
            padding: 0.2em 0.3em;
            cursor: pointer;
        }
    """
    page.style[page_styles]

    catalog_url = f"{config.get().RELEASE_CATALOG_URL}{project.key}/"
    title_row = htm.div(".row")[
        htm.div(".col-md.d-flex.align-items-baseline.flex-wrap.gap-3")[
            htm.h1[project.display_name],
            htm.a(".small", href=catalog_url, target="_blank", rel="noopener")["View in catalog"],
        ],
        htm.div(".col-sm-auto")[htm.span(".badge.text-bg-secondary")[project.status.value.lower()]]
        if (not project.is_active)
        else "",
    ]
    page.append(title_row)

    archived_banner = render.archived_project_banner(project, "Actions are disabled.")
    if archived_banner:
        page.append(archived_banner)
    # The project's settings are what the guidance is about, so offer it to whoever can change them
    guidance = (await interaction.first_release_guidance(project.key, session.uid)) if can_edit_policy else None
    if (guidance == "project") and (guidance_banner := render.first_release_banner(guidance, project)):
        page.append(guidance_banner)

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
    if can_edit_metadata and project.is_synced_from_asf_yaml:
        page.append(_render_asf_yaml_notice(project, can_export=can_manage_project_actions))
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
        scope,
    )
    if project.is_active and (not can_edit_policy):
        page.append(
            htm.p(".text-muted")[
                "You are not a participant of this committee, so the metadata below is read only."
                " You cannot edit or submit changes."
            ]
        )
    page.append(tabs)

    content = page.collect()

    javascripts = ["copy-variable", "distribution-namespace-toggle", "template-url-toggle", "version-scheme-toggle"]
    if can_edit_policy and (quart.request.args.get("tab") in {"vote", "finish"}):
        javascripts.append("cc-bcc-exclusive")
        javascripts.append("publish-location-preview")
    return await template.blank(
        title=f"{project.display_name}",
        description=f"Information regarding {project.display_name}.",
        content=content,
        javascripts=javascripts,
    )


def _append_cycle_overrides(
    body: htm.Block,
    project: sql.Project,
    scope: _SettingsScope,
    tab: str,
    fields: dict[str, tuple[str, sql.RecipientAction | None]],
    readonly: bool,
) -> None:
    """Add the forms that clear what the cycle in scope overrides on this tab."""
    current = scope.current
    if (current is None) or readonly:
        return
    # A form can't contain another form, so each badge's button submits one of these by its id
    for form_field, (policy_field, action) in fields.items():
        if not current.overrides(policy_field, action):
            continue
        body.append(
            htm.form(f"#clear-{form_field}.d-none", method="post", action=_view_action(project, tab, scope.cycle))[
                form.csrf_input(),
                htpy.input(type="hidden", name="project_key", value=str(project.key)),
                htpy.input(type="hidden", name="variant", value="clear_cycle_override"),
                htpy.input(type="hidden", name="cycle", value=scope.cycle),
                htpy.input(type="hidden", name="field", value=policy_field),
                htpy.input(type="hidden", name="action", value=action.value if action else ""),
            ]
        )


def _approval_link(project: sql.Project, action: sql.ApprovalAction) -> htm.Element:
    match action:
        case sql.ApprovalAction.ARCHIVE:
            route, classes = archive, ".btn.btn-sm.btn-outline-secondary"
        case sql.ApprovalAction.DELETE:
            route, classes = delete, ".btn.btn-sm.btn-outline-danger"
        case sql.ApprovalAction.EMAIL_VOTING | sql.ApprovalAction.TRUSTED_VOTING:
            raise ValueError(f"{action.value} is not a project action")
        case sql.ApprovalAction.UNARCHIVE:
            route, classes = unarchive, ".btn.btn-sm.btn-outline-secondary"
    return htm.a(classes, href=util.as_url(route, project_key=str(project.key)))[f"Request {_approval_noun(action)}"]


def _approval_notice(action: sql.ApprovalAction) -> str:
    match action:
        case sql.ApprovalAction.ARCHIVE:
            effect = ", which deletes any draft releases and marks the project dormant"
        case sql.ApprovalAction.DELETE:
            effect = ""
        case sql.ApprovalAction.EMAIL_VOTING | sql.ApprovalAction.TRUSTED_VOTING:
            raise ValueError(f"{action.value} is not a project action")
        case sql.ApprovalAction.UNARCHIVE:
            effect = ", which makes the project active again. Its archived releases stay archived"
    return (
        "This starts a binding CAP approval vote by the committee PMC. ATR will mark the project ready to"
        f" {action.value} only if the vote passes, and you must then return to complete it{effect}."
    )


def _approval_noun(action: sql.ApprovalAction) -> str:
    match action:
        case sql.ApprovalAction.ARCHIVE:
            return "archival"
        case sql.ApprovalAction.DELETE:
            return "deletion"
        case sql.ApprovalAction.EMAIL_VOTING | sql.ApprovalAction.TRUSTED_VOTING:
            raise ValueError(f"{action.value} is not a project action")
        case sql.ApprovalAction.UNARCHIVE:
            return "unarchival"


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


def _asf_yaml_cycles(project: sql.Project, overrides: Sequence[sql.CycleOverrides]) -> dict[str, object]:
    """Each cycle's settings, keyed by cycle. strictyaml serialises only strings, so the rest go out as text."""
    recipient_keys = {"vote": "vote_recipients", "announce": "announce_recipients"}
    block: dict[str, object] = {}
    for row in sorted(overrides, key=lambda row: row.cycle):
        settings: dict[str, object] = {}
        for field, value in sorted(row.settings.items()):
            if field == "recipient_defaults":
                for action, recipients in sorted(value.items()):
                    given = {key: item for key, item in recipients.items() if item}
                    settings[recipient_keys[action]] = given or _YAML_BLANKS["{}"]
            elif field == "min_hours":
                # No value is the default for the committee, which the file can only give as a number
                settings[field] = str(project.policy_default_min_hours if (value is None) else value)
            elif isinstance(value, bool):
                settings[field] = "true" if value else "false"
            elif isinstance(value, list):
                settings[field] = list(value) or _YAML_BLANKS["[]"]
            else:
                settings[field] = str(value) or _YAML_BLANKS['""']
        if settings:
            block[row.cycle] = settings
    return block


def _asf_yaml_distributions(expected: Sequence[sql.ExpectedDistribution]) -> list[dict[str, object]]:
    # One entry for the packages that share a platform, namespace and version
    groups: dict[tuple[str, str, str], list[str]] = {}
    for row in sorted(expected, key=lambda row: (row.platform.name, row.owner_namespace, row.package)):
        groups.setdefault((row.platform.name, row.owner_namespace, row.version_pattern), []).append(row.package)
    distributions: list[dict[str, object]] = []
    for (platform, namespace, version_pattern), packages in groups.items():
        entry: dict[str, object] = {"platform": platform.lower()}
        if namespace:
            entry["owner_namespace"] = namespace
        entry["packages"] = packages
        if version_pattern != "{{VERSION}}":
            entry["version"] = version_pattern
        distributions.append(entry)
    return distributions


def _asf_yaml_export(
    project: sql.Project,
    expected: Sequence[sql.ExpectedDistribution] = (),
    overrides: Sequence[sql.CycleOverrides] = (),
    *,
    full: bool = False,
) -> str:
    block: dict[str, object] = {"metadata": _asf_yaml_metadata(project)}
    policy = _asf_yaml_policy(project)
    if policy:
        block["policy"] = policy
    cycle_settings = _asf_yaml_cycles(project, overrides)
    if cycle_settings:
        block["cycles"] = cycle_settings
    distributions = _asf_yaml_distributions(expected)
    if distributions:
        block["distributions"] = distributions
    # atr_sync defaults to on, so we emit it as a visible reminder the sync exists
    features: dict[str, str] = {"atr_sync": "true"}
    if full:
        features["atr_sync_mode"] = "full"
    block["features"] = features
    text = strictyaml.as_document({"project": block}).as_yaml()
    for blank, marker in _YAML_BLANKS.items():
        text = text.replace(marker, blank)
    return text


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
    # only strings, so the int and bools go out as text.
    extras: dict[str, object] = {}
    if release_policy.auto_archive_prior_release:
        extras["auto_archive_prior_release"] = "true"
    if release_policy.file_tag_mappings:
        extras["file_tag_mappings"] = {tag: list(paths) for tag, paths in release_policy.file_tag_mappings.items()}
    if release_policy.license_check_mode != sql.LicenseCheckMode.BOTH:
        extras["license_check_mode"] = release_policy.license_check_mode.value
    if release_policy.min_hours is not None:
        extras["min_hours"] = str(release_policy.min_hours)
    if release_policy.require_version_in_filenames:
        extras["require_version_in_filenames"] = "true"
    return extras


def _asf_yaml_policy_fields(release_policy: sql.ReleasePolicy) -> dict[str, object]:
    # Read release_policy directly, not the policy_* properties - those fall back to the
    # default templates, and we only want fields that have actually been set.
    # The trusted publishing settings are left out: .asf.yaml can't set them, so a
    # file that carried them would fail validation on the next push.
    fields: dict[str, object] = {}
    for field in (
        "announce_release_subject",
        "announce_release_template",
        "announce_release_template_url",
        "start_vote_subject",
        "start_vote_template",
        "start_vote_template_url",
        "finish_vote_template",
        "finish_vote_template_url",
        "vote_comment_template",
        "vote_comment_template_url",
        "release_checklist",
        "download_path_suffix",
        "rat_excludes_url",
    ):
        value = getattr(release_policy, field)
        if value:
            fields[field] = value
    for field in (
        "binary_artifact_paths",
        "source_artifact_paths",
        "source_excludes_lightweight",
        "source_excludes_rat",
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


def _cycle_notes(
    scope: _SettingsScope, fields: dict[str, tuple[str, sql.RecipientAction | None]], *, readonly: bool
) -> dict[str, htm.Element] | None:
    """A note for each field of a cycle's form that the cycle overrides, with a control to clear it."""
    current = scope.current
    if current is None:
        return None
    notes: dict[str, htm.Element] = {}
    for form_field, (policy_field, action) in fields.items():
        if not current.overrides(policy_field, action):
            continue
        badge = htm.span(".badge.text-bg-primary")[f"Set for {scope.cycle}"]
        if readonly:
            notes[form_field] = htm.div(".mt-1")[badge]
            continue
        notes[form_field] = htm.div(".mt-1.d-inline-flex.gap-1")[
            badge,
            htpy.button(
                ".badge.text-bg-danger.border-0.atr-cursor-pointer",
                type="submit",
                form=f"clear-{form_field}",
                title="Use the default setting",
            )[htpy.i(".bi.bi-x-lg.me-1"), "Clear"],
        ]
    return notes


def _cycle_selector_notes(project: sql.Project, scope: _SettingsScope, *, is_new: bool) -> list[htm.Element]:
    """The lines under the cycle selector, which say what the settings shown apply to."""
    if not project.cycle_match:
        return [
            htm.p(".text-muted.small.mt-2.mb-0")[
                "Every release of this project uses these settings. To give a cycle settings of its own, set a"
                " cycle match under ",
                htm.a(href=_settings_url(project, "lifecycle"))["Version scheme"],
                ".",
            ]
        ]
    if not scope.cycle:
        return []
    notes = [
        htm.p(".text-muted.small.mt-2.mb-0")[
            f"Releases in {scope.cycle} use these settings."
            f" Anything not set for {scope.cycle} uses the default settings."
        ]
    ]
    if is_new:
        notes.append(
            htm.p(".text-muted.small.mb-0")[
                f"No release is in {scope.cycle} yet. Settings saved for it apply once a version is in that cycle."
            ]
        )
    current = scope.current
    if (current is not None) and current.updated:
        notes.append(
            htm.p(".text-muted.small.mb-0")[
                f"Last updated {current.updated.strftime('%Y-%m-%d')} by {current.updated_by_display}"
            ]
        )
    return notes


def _cycle_settings_badge(overrides: sql.CycleOverrides | None) -> htm.Element | None:
    if overrides is None:
        return None
    count = util.plural(overrides.override_count, "setting", include_count=True)
    return htm.span(".badge.text-bg-primary")[count]


async def _project_approval_element(project: sql.Project) -> htm.Element | None:
    async with db.session() as data:
        approval = await data.approval_request(
            project_key=str(project.key),
            status_in=[sql.ApprovalStatus.PENDING, sql.ApprovalStatus.APPROVED],
        ).get()
    if approval is not None:
        return await _approval_request_element(approval)
    if project.status == sql.ProjectStatus.DORMANT:
        return _approval_link(project, sql.ApprovalAction.UNARCHIVE)
    releases = project.releases_including_embargoed
    if not releases:
        return _approval_link(project, sql.ApprovalAction.DELETE)
    if not any((r.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT) and (not r.is_archived) for r in releases):
        return _approval_link(project, sql.ApprovalAction.ARCHIVE)
    return None


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
    scope: _SettingsScope,
) -> htpy.Element:
    readonly = not can_edit_policy
    releases_tab = htm.Tab(
        key="releases",
        label="Releases",
        render=lambda: _render_releases_tab(
            project,
            project_cycles,
            releases,
            scope,
            can_edit_policy=can_edit_policy,
            can_start_release=can_start_release,
        ),
    )
    project_tabs: list[htm.Tab] = [
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
    settings_tabs: list[htm.Tab] = []
    if project.is_active:
        project_tabs.append(
            htm.Tab(
                "lifecycle",
                "Version scheme",
                lambda: _render_version_scheme_form(project, readonly=readonly),
            )
        )
        settings_tabs = [
            htm.Tab("compose", "Compose", lambda: _render_compose_form(project, scope, readonly=readonly)),
            htm.Tab("vote", "Vote", lambda: _render_vote_form(project, scope, readonly=readonly)),
            htm.Tab("finish", "Finish", lambda: _render_finish_form(project, scope, readonly=readonly)),
            htm.Tab(
                "third-party-distribution",
                "Third-party distribution",
                lambda: _render_expected_distributions_tab(project, readonly=readonly),
            ),
        ]

    groups: list[tuple[str, list[htm.Tab]]] = [("Releases", [releases_tab]), ("Project", project_tabs)]
    if settings_tabs:
        groups.append(("Release settings", settings_tabs))
    requested = quart.request.args.get("tab", releases_tab.key)
    # An unknown tab falls back to the first one, as a tab strip does
    active_group, active_tab = next(
        ((tabs, tab) for _label, tabs in groups for tab in tabs if (tab.key == requested)),
        (groups[0][1], releases_tab),
    )

    block = htm.Block(htm.div)
    block.append(
        _tab_nav(
            [(tabs[0].key, label, tabs is active_group) for label, tabs in groups],
            lambda key: _settings_url(project, key, scope.cycle if (key == "compose") else ""),
            ".nav.nav-tabs.mb-3",
        )
    )
    in_settings = active_group is settings_tabs
    if in_settings:
        block.append(_render_cycle_selector(project, scope, active_tab.key, readonly=readonly))
        if scope.cycle in scope.unmatched:
            block.append(_render_cycle_unmatched(project, scope, active_tab.key, readonly=readonly))
    if len(active_group) > 1:
        block.append(
            _tab_nav(
                [(tab.key, tab.label, tab is active_tab) for tab in active_group],
                lambda key: _settings_url(project, key, scope.cycle if in_settings else ""),
                ".nav.nav-pills.mb-3",
            )
        )
    if in_settings and scope.cycle and (active_tab.key not in _CYCLE_TABS):
        block.p(".text-muted")["These settings apply to every cycle."]
    content = active_tab.render()
    if inspect.isawaitable(content):
        content = await content
    block.append(content)
    return block.collect()


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


def _planned_cycle(project: sql.Project, entered: str) -> tuple[str, str]:
    """The cycle for the text that was typed, or else why it can't be one."""
    if cycle := cycles.planned_cycle(project, entered):
        return cycle, ""
    return "", f"{entered} is not a cycle that the project's version scheme allows."


async def _publish_location_notes(
    project: sql.Project, settings: sql.PolicySettings, *, readonly: bool
) -> dict[str, htm.Element]:
    committee = project.committee
    if readonly or (committee is None):
        return {}
    previous = await interaction.latest_catalogued_directory(project.key, "")
    version = previous[0] if previous else ""
    suffix = settings.policy_download_path_suffix
    location = construct.publish_location_preview(
        project, committee, version, suffix, settings, at_start=False, at_policy=True
    )
    mismatch = await construct.publish_location_mismatch(project.key, version, location, include_version=True)
    note = render.publish_location_note(
        project.safe_key,
        committee,
        suffix,
        construct.publish_location_pattern(project, committee, suffix),
        at_start=False,
        at_policy=True,
        version=version,
        mismatch=mismatch,
    )
    return {"download_path_suffix": note}


def _recipient_grid_widget(
    project: sql.Project,
    settings: sql.PolicySettings,
    action: sql.RecipientAction,
    *,
    readonly: bool,
    dev_lists: Sequence[str] = (),
) -> htm.Element:
    committee = project.committee
    list_domain = committee.list_domain if (committee is not None) else f"{project.key}.apache.org"
    is_podling = bool(committee is not None and committee.is_podling)
    options = util.configurable_recipients(
        action,
        list_domain,
        is_podling=is_podling,
        mail_addresses=committee.mail_addresses if (committee is not None) else [],
        dev_lists=dev_lists,
    )
    stored_to, stored_cc, stored_bcc = settings.policy_recipients(action)
    # Include any already-stored recipients (eg set via .asf.yaml) so a saved
    # value remains a selectable option even when it's outside the committee
    # defaults.
    stored = (stored_to,) if readonly else (stored_to, *stored_cc, *stored_bcc)
    for address in stored:
        if address and (address not in options):
            options.append(address)
    default_to = stored_to if (stored_to in options) else (options[0] if options else None)
    to_radios = render.html_recipients_to_radios(options, default_to=default_to)
    if readonly:
        return htm.div("#email_to")[
            to_radios,
            htm.p(".text-muted.mt-2.mb-0")[f"{len(stored_cc)} CC, {len(stored_bcc)} BCC"],
        ]
    return htm.div("#email_to")[
        to_radios,
        htpy.details(".mt-2")[
            htpy.summary["Select CC and BCC recipients"],
            render.html_recipients_cc_bcc_table(options, selected_cc=set(stored_cc), selected_bcc=set(stored_bcc)),
        ],
    ]


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
        # A dormant project is offered unarchival, which the sole-project rule doesn't limit
        if (project.is_active and (not is_sole_active_project)) or (project.status == sql.ProjectStatus.DORMANT):
            approval_element = await _project_approval_element(project)
            if approval_element:
                body.append(approval_element)

        if project.committee:
            if (project.committee.key in session.member_committees) or is_privileged:
                body.append(
                    htm.a(
                        ".btn.btn-sm.btn-outline-primary",
                        href=util.as_url(add_project, committee_key=project.committee.key),
                    )["Create a new sub-project"]
                )
                body.append(
                    htm.a(
                        ".btn.btn-sm.btn-outline-secondary",
                        href=util.as_url(project_yaml, project_key=project.key),
                    )["Export .asf.yaml"]
                )
                body.append(
                    htm.a(
                        ".btn.btn-sm.btn-outline-secondary",
                        href=util.as_url(advanced, project_key=project.key),
                    )["Advanced settings"]
                )
    return card.collect()


def _render_asf_yaml_mode_choice(*, full: bool) -> htm.Element:
    # The file is shown in whichever mode the project last synced in, and the
    # script swaps the text when the choice changes
    radios = []
    for value, label in (("merge", "Merge"), ("full", "Full")):
        radio_id = f"atr-sync-mode-{value}"
        radios.append(
            htm.div(".form-check.form-check-inline")[
                htpy.input(
                    type="radio",
                    class_="form-check-input",
                    name="atr_sync_mode",
                    id=radio_id,
                    value=value,
                    checked=(value == "full") == full,
                ),
                htpy.label(class_="form-check-label", for_=radio_id)[label],
            ]
        )
    return htm.div(".mb-3")[
        htm.div[*radios],
        htm.div(".form-text")[
            "Merge updates only the fields the file sets, and the rest keep their values in ATR.",
            " Full also resets every field the file leaves out to its default, so the file is the whole configuration.",
        ],
    ]


def _render_asf_yaml_notice(project: sql.Project, *, can_export: bool) -> htm.Element:
    export_link = (
        htm.a(".alert-link.ms-1", href=util.as_url(project_yaml, project_key=project.key))["Export .asf.yaml"]
        if can_export
        else ""
    )
    notice = (
        strings.ASF_YAML_FULL_SYNC_NOTICE
        if (project.update_type == sql.UpdateType.ASFYAML_FULL)
        else strings.ASF_YAML_SYNC_NOTICE
    )
    return htm.div(".alert.alert-info", role="alert")[notice, export_link]


def _render_categories_section(project: sql.Project, *, readonly: bool) -> htm.Element:
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
            if (not readonly) and (cat not in registry.FORBIDDEN_PROJECT_CATEGORIES)
            else ""
        )
        badge = htm.div(".badge.bg-primary.d-inline-flex.align-items-center.px-2.py-1")[
            htm.span[cat],
            remove_button,
        ]
        category_badges.append(badge)

    with card.block(htm.div, classes=".card-body") as card_body:
        if not readonly:
            card_body.append(_taxonomy_add_form(project, "add_category", "category_to_add", "New category"))
        if category_badges:
            card_body.append(htm.div(".d-flex.flex-wrap.gap-2.align-items-center.mt-3")[*category_badges])
        elif readonly:
            card_body.append(htm.div(".text-muted.fst-italic")["No categories set."])
    return card.collect()


async def _render_compose_form(project: sql.Project, scope: _SettingsScope, *, readonly: bool) -> htm.Element:
    settings = scope.settings
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")[_settings_heading("Release policy - Compose options", scope)]
    ]

    with card.block(htm.div, classes=".card-body") as card_body:
        _append_cycle_overrides(card_body, project, scope, "compose", _COMPOSE_OVERRIDES, readonly)
        await form.render_block(
            card_body,
            model_cls=shared.projects.ComposePolicyForm,
            action=_view_action(project, "compose", scope.cycle),
            submit_label="Save",
            defaults={
                "project_key": str(project.key),
                "cycle": scope.cycle,
                "license_check_mode": settings.policy_license_check_mode,
                "source_excludes_lightweight": "\n".join(settings.policy_source_excludes_lightweight),
                "source_excludes_rat": "\n".join(settings.policy_source_excludes_rat),
                "rat_excludes_url": settings.policy_rat_excludes_url,
            },
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            # wider_widgets=True,
            textarea_rows=5,
            notes=_cycle_notes(scope, _COMPOSE_OVERRIDES, readonly=readonly),
            readonly=readonly,
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


def _render_cycle_heading(project: sql.Project, cycle: sql.ProjectCycle, scope: _SettingsScope) -> htm.Element:
    """A cycle's heading on the Releases tab, with a link to its release settings where it can have them."""
    settings: htm.Element | str = ""
    if scope.selectable and project.is_active and (cycle.cycle in scope.overridable):
        settings = htm.span(".fs-6.d-flex.align-items-center.gap-3")[
            _cycle_settings_badge(scope.overrides.get(cycle.cycle)),
            htm.a(href=_settings_url(project, "compose", cycle.cycle))["Release settings"],
        ]
    return htm.h2(".mt-4.mb-3.d-flex.flex-wrap.justify-content-between.align-items-center.gap-2")[
        htm.span[cycles.display_name(cycle.cycle)], settings
    ]


def _render_cycle_selector(project: sql.Project, scope: _SettingsScope, tab: str, *, readonly: bool) -> htm.Element:
    options: list[htm.Element] = [htpy.option(value="", selected=(not scope.cycle))["Default settings"]]
    for cycle in [*scope.overridable, *scope.unmatched]:
        label = cycle
        overrides = scope.overrides.get(cycle)
        if overrides is not None:
            count = util.plural(overrides.override_count, "setting", include_count=True)
            unmatched = ", no matching cycle" if (cycle not in scope.matched) else ""
            label = f"{label} ({count}{unmatched})"
        options.append(htpy.option(value=cycle, selected=(cycle == scope.cycle))[label])
    is_new = bool(scope.cycle) and (scope.cycle not in scope.matched) and (scope.current is None)
    if is_new:
        options.append(htpy.option(value=scope.cycle, selected=True)[f"{scope.cycle} (no release yet)"])

    forms: list[htm.Element] = [
        htm.form(
            ".d-flex.flex-wrap.align-items-center.gap-3.m-0",
            method="get",
            action=util.as_url(view, project_key=str(project.key)),
        )[
            htpy.input(type="hidden", name="tab", value=tab),
            htpy.label(".fw-semibold.mb-0", for_="cycle-select")["Applies to"],
            htpy.select("#cycle-select.form-select.w-auto", name="cycle")[*options],
            htpy.button(".btn.btn-sm.btn-outline-primary", type="submit")["Show"],
        ]
    ]
    can_create = bool(project.cycle_match) and (not readonly)
    if can_create:
        forms.append(_render_new_cycle_form(project, scope, tab))

    # Collapsed to a link unless the project has overrides, or the viewer has chosen or typed a cycle
    is_open = bool(scope.overrides or scope.cycle or scope.entered)
    toggle = {
        "data_bs_toggle": "collapse",
        "data_bs_target": "#cycle-settings-link, #cycle-settings-card",
        "aria_expanded": "true" if is_open else "false",
    }
    link = htm.div(f"#cycle-settings-link.collapse{'' if is_open else '.show'}")[
        htm.a(".atr-text-small.d-inline-block.mb-3", href="#cycle-settings-card", role="button", **toggle)[
            "Per-cycle settings", htpy.i(".bi.bi-arrow-down.ms-1")
        ]
    ]
    card = htm.Block(htm.div, classes=f"#cycle-settings-card.card.mb-3.collapse{'.show' if is_open else ''}")
    with card.block(htm.div, classes=".card-body") as body:
        body.div(".d-flex.justify-content-between.align-items-start.gap-3")[
            htm.div(".d-flex.flex-wrap.align-items-center.column-gap-5.row-gap-3")[*forms],
            htpy.button(
                ".btn.btn-sm.btn-outline-secondary", type="button", aria_label="Hide per-cycle settings", **toggle
            )[htpy.i(".bi.bi-arrow-up")],
        ]
        if can_create and scope.problem:
            body.div(".text-danger.small.mt-2")[scope.problem]
        for note in _cycle_selector_notes(project, scope, is_new=is_new):
            body.append(note)
    return htm.div[link, card.collect()]


def _render_cycle_unmatched(project: sql.Project, scope: _SettingsScope, tab: str, *, readonly: bool) -> htm.Element:
    """Say that the overrides in scope apply to nothing, with controls to move or remove them."""
    alert = htm.Block(htm.div, classes=".alert.alert-warning")
    with alert.block(htm.p, classes=".mb-2") as text:
        text.strong[f"No release is in a cycle called {scope.cycle}."]
        text.text(" These settings apply to nothing until a version matches.")
        if project.cycle_match:
            text.text(" The cycle pattern is ")
            text.code[project.cycle_match]
            text.text(".")
    if readonly:
        return alert.collect()
    action = _view_action(project, tab, scope.cycle)
    hidden = [
        form.csrf_input(),
        htpy.input(type="hidden", name="project_key", value=str(project.key)),
        htpy.input(type="hidden", name="cycle", value=scope.cycle),
    ]
    targets = [cycle for cycle in scope.overridable if cycle not in scope.overrides]
    controls: list[htm.Element] = []
    if targets:
        controls.append(
            htm.form(".d-flex.flex-wrap.align-items-center.gap-2.m-0", method="post", action=action)[
                *hidden,
                htpy.input(type="hidden", name="variant", value="move_cycle_overrides"),
                htpy.label(".mb-0", for_="target")["Move these settings to"],
                htpy.select("#target.form-select.form-select-sm.w-auto", name="target")[
                    *[htpy.option(value=cycle)[cycle] for cycle in targets]
                ],
                htpy.button(".btn.btn-sm.btn-primary", type="submit")["Move"],
            ]
        )
    controls.append(
        htm.form(".m-0", method="post", action=action)[
            *hidden,
            htpy.input(type="hidden", name="variant", value="remove_cycle_overrides"),
            htpy.button(".btn.btn-sm.btn-outline-danger", type="submit")["Remove settings"],
        ]
    )
    alert.div(".d-flex.flex-wrap.align-items-center.gap-3")[*controls]
    return alert.collect()


async def _render_expected_distributions_add(
    project: sql.Project, latest: sql.Release | None, missing: list[sql.ExpectedDistribution]
) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Add expected distributions"]
    ]
    action = _view_action(project, "third-party-distribution")
    with card.block(htm.div, classes=".card-body") as card_body:
        card_body.append(shared.distribution.html_platforms_without_namespace())
        await form.render_block(
            card_body,
            model_cls=shared.projects.AddExpectedDistributionsForm,
            action=action,
            submit_label="Add",
            defaults={"project_key": str(project.key)},
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            textarea_rows=5,
        )
        if (latest is not None) and missing:
            packages = util.plural(len(missing), "package", include_count=True)
            verb = "is" if (len(missing) == 1) else "are"
            card_body.p(".mt-4.mb-1")[f"Release {latest.version} has {packages} that {verb} not in this list."]
            card_body.append(
                htm.form(".m-0", method="post", action=action)[
                    form.csrf_input(),
                    htpy.input(type="hidden", name="project_key", value=str(project.key)),
                    htpy.input(type="hidden", name="variant", value="seed_expected_distributions"),
                    htpy.button(".btn.btn-secondary", type="submit")["Add them"],
                ]
            )
    return card.collect()


def _render_expected_distributions_group(
    project: sql.Project, rows: list[sql.ExpectedDistribution], *, readonly: bool, is_open: bool
) -> htm.Element:
    first = rows[0]
    table_rows = []
    for row in rows:
        remove_form = (
            ""
            if readonly
            else htm.form(".d-inline.m-0", method="post", action=_view_action(project, "third-party-distribution"))[
                form.csrf_input(),
                htpy.input(type="hidden", name="project_key", value=str(project.key)),
                htpy.input(type="hidden", name="variant", value="remove_expected_distribution"),
                htpy.input(
                    type="hidden",
                    name="platform",
                    value=shared.distribution.DistributionPlatform.from_sql(row.platform).value,
                ),
                htpy.input(type="hidden", name="owner_namespace", value=row.owner_namespace),
                htpy.input(type="hidden", name="package", value=row.package),
                htpy.input(type="hidden", name="version_pattern", value=row.version_pattern),
                htpy.button(".btn.btn-sm.btn-outline-danger", type="submit")["Remove"],
            ]
        )
        version = "Same as the release" if (row.version_pattern == "{{VERSION}}") else htm.code[row.version_pattern]
        package = purl.expected(row) or row.package
        table_rows.append(htm.tr[htm.td[htm.code[package]], htm.td[version], htm.td(".text-end")[remove_form]])
    summary = htm.summary[
        htm.span(".badge.bg-secondary.me-2.font-monospace")[str(len(rows))],
        first.platform.value.name,
        (" ", htm.code[first.owner_namespace]) if first.owner_namespace else "",
    ]
    table = htm.table(".table.table-sm.table-bordered.table-striped.mt-2")[
        htm.thead[htm.tr[htm.th["Package"], htm.th["Version"], htm.th[""]]],
        htm.tbody[*table_rows],
    ]
    if is_open:
        return htm.details(".mb-3.rounded", open=True)[summary, table]
    return htm.details(".mb-3.rounded")[summary, table]


async def _render_expected_distributions_tab(project: sql.Project, *, readonly: bool) -> htm.Element:
    async with db.session() as data:
        expected = await data.expected_distribution(project_key=str(project.key)).all()
        recorded = await data.distribution(project_key=str(project.key), _with_release=True).all()
    latest, missing = shared.distribution.expected_from_latest_release(recorded, expected)

    groups: dict[tuple[str, str], list[sql.ExpectedDistribution]] = {}
    for row in sorted(expected, key=lambda row: (row.platform.value.name, row.owner_namespace, row.package)):
        groups.setdefault((row.platform.value.name, row.owner_namespace), []).append(row)

    tab = htm.Block(htm.div)
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")["Expected distributions"]
    ]
    with card.block(htm.div, classes=".card-body") as card_body:
        if not expected:
            card_body.p(".mb-0")["No packages are expected for this project's releases."]
        else:
            packages = util.plural(len(expected), "package", include_count=True)
            verb = "is" if (len(expected) == 1) else "are"
            card_body.p[
                f"{packages} {verb} expected for each release."
                " A release can be announced once each one is found or ignored."
            ]
        for index, rows in enumerate(groups.values()):
            card_body.append(
                _render_expected_distributions_group(project, rows, readonly=readonly, is_open=(index == 0))
            )
    tab.append(card)
    if not readonly:
        tab.append(await _render_expected_distributions_add(project, latest, missing))
    return tab.collect()


async def _render_finish_form(project: sql.Project, scope: _SettingsScope, *, readonly: bool) -> htm.Element:
    settings = scope.settings
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")[_settings_heading("Release policy - Finish options", scope)]
    ]

    flash_error_data = {} if readonly else await sessions.form_error_pop(quart.request.path)
    directory = construct.publish_location_directory(project.committee)

    announce_release_subject_widget = _input_with_variables(
        field_name="announce_release_subject",
        value=_flash_or_stored(
            flash_error_data, "announce_release_subject", settings.policy_announce_release_subject or ""
        ),
        template_variables=construct.announce_subject_template_variables(),
        documentation="Subject line template for announcement emails.",
    )

    announce_release_template_widget = _textarea_with_variables(
        field_name="announce_release_template",
        value=_flash_or_stored(
            flash_error_data, "announce_release_template", settings.policy_announce_release_template or ""
        ),
        template_variables=construct.announce_template_variables(),
        rows=18,
        documentation="Email template for messages to announce a finished release.",
    )

    # A cycle that sets its own publish location gets its badge above the location preview
    notes = _cycle_notes(scope, _FINISH_OVERRIDES, readonly=readonly) or {}
    for field, note in (await _publish_location_notes(project, settings, readonly=readonly)).items():
        notes[field] = htm.div[notes[field], note] if (field in notes) else note

    with card.block(htm.div, classes=".card-body") as card_body:
        _append_cycle_overrides(card_body, project, scope, "finish", _FINISH_OVERRIDES, readonly)
        await form.render_block(
            card_body,
            model_cls=shared.projects.FinishPolicyForm,
            action=_view_action(project, "finish", scope.cycle),
            submit_label="Save",
            defaults={
                "project_key": project.key,
                "cycle": scope.cycle,
                "announce_release_subject": settings.policy_announce_release_subject or "",
                "announce_release_template": settings.policy_announce_release_template or "",
                "announce_release_template_url": settings.policy_announce_release_template_url,
                "archive_prior_release": settings.policy_auto_archive_prior_release,
                "download_path_suffix": settings.policy_download_path_suffix,
            },
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            # wider_widgets=True,
            textarea_rows=10,
            custom={
                "announce_release_subject": announce_release_subject_widget,
                "announce_release_template": announce_release_template_widget,
                "email_to": _recipient_grid_widget(project, settings, sql.RecipientAction.ANNOUNCE, readonly=readonly),
            },
            notes=notes,
            prefixes={"download_path_suffix": directory},
            skip=["email_cc", "email_bcc"],
            flash_error_data=flash_error_data,
            readonly=readonly,
        )
    return card.collect()


async def _render_header_cards(
    project: sql.Project,
    can_manage_project_actions: bool,
    session: web.Committer,
    is_sole_active_project: bool,
    is_privileged: bool,
) -> htm.Element:
    block = htm.Block(htm.div, classes=".row.row-cols-1.row-cols-md-2.g-4")
    block.div(".col")[_render_project_card(project)]
    if can_manage_project_actions:
        block.div(".col")[await _render_actions_card(project, session, is_sole_active_project, is_privileged)]
    return block.collect()


def _render_languages_section(project: sql.Project, *, readonly: bool) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Programming languages"]]

    current_languages = project.programming_languages.split(", ") if project.programming_languages else []
    language_badges = []
    for lang in current_languages:
        # Manual form as badges are not handled by the form system
        remove_button = (
            htm.form(
                ".d-inline.m-0",
                method="post",
                action=_view_action(project, "metadata"),
            )[
                form.csrf_input(),
                htpy.input(type="hidden", name="project_key", value=str(project.key)),
                htpy.input(type="hidden", name="variant", value="remove_language"),
                htpy.input(type="hidden", name="language_to_remove", value=lang),
                htpy.button(
                    ".btn-close.btn-close-white.ms-1.page-remove-tag", type="submit", aria_label=f"Remove {lang}"
                ),
            ]
            if not readonly
            else ""
        )
        badge = htm.div(".badge.bg-success.d-inline-flex.align-items-center.px-2.py-1")[
            htm.span[lang],
            remove_button,
        ]
        language_badges.append(badge)

    with card.block(htm.div, classes=".card-body") as card_body:
        if not readonly:
            card_body.append(_taxonomy_add_form(project, "add_language", "language_to_add", "New language"))
        if language_badges:
            card_body.append(htm.div(".d-flex.flex-wrap.gap-2.align-items-center.mt-3")[*language_badges])
        elif readonly:
            card_body.append(htm.div(".text-muted.fst-italic")["No programming languages set."])
    return card.collect()


def _metadata_uri(url: str, allowed_schemes: frozenset[str]) -> htm.Element | str:
    # Only link a URI whose scheme is on the field's allowlist; anything else renders as plain text,
    # so a stored javascript: or data: URI that reached us by another route (e.g. a DOAP import) can
    # never become a clickable href
    # A download page with version variables is a different page for each release, so there is no one page to link
    if validation.uri_scheme_allowed(url, allowed_schemes) and ("{{" not in url):
        return htm.a(href=url, target="_blank", rel="noopener")[url]
    return url


def _render_metadata_card(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Reference metadata"]]

    rows: list[htm.Element] = []
    for label, text in [
        ("Name", project.name),
        ("Description", project.description),
        ("Short description", project.short_description),
    ]:
        if text:
            rows.append(
                htm.tr[
                    htm.th(".border-0.w-25")[label],
                    htm.td(".text-break.border-0")[text],
                ]
            )

    for label, url in [
        ("Homepage", project.homepage),
        ("Lifecycle page", project.lifecycle_page),
        ("Download page", validation.download_page_variables_unescaped(project.download_page or "")),
        ("Bug database", project.bug_database),
        ("Mailing lists", project.mailing_lists),
    ]:
        if url:
            rows.append(
                htm.tr[
                    htm.th(".border-0.w-25")[label],
                    htm.td(".text-break.border-0")[_metadata_uri(url, validation.STANDARD_URI_SCHEMES)],
                ]
            )

    for label, urls, schemes in [
        ("Repositories", project.repositories, validation.REPOSITORY_URI_SCHEMES),
        ("Relevant standards", project.standards, validation.STANDARD_URI_SCHEMES),
    ]:
        if urls:
            rows.append(
                htm.tr[
                    htm.th(".border-0.w-25")[label],
                    htm.td(".text-break.border-0")[
                        htm.ul(".mb-0.ps-3")[*[htm.li[_metadata_uri(u, schemes)] for u in urls]],
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
    notes: dict[str, htm.Element] = {}
    if project.committee and project.committee.github_oidc_enabled:
        notes["repositories"] = htm.div(".text-muted.mt-1.form-text")[
            "GitHub Actions can upload from those under github.com/apache."
        ]
    with card.block(htm.div, classes=".card-body") as body:
        await form.render_block(
            body,
            model_cls=shared.projects.EditMetadataForm,
            action=_view_action(project, "metadata"),
            submit_label="Save",
            defaults=shared.projects.edit_metadata_defaults(project),
            notes=notes,
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
    block.append(_render_categories_section(project, readonly=not can_manage_taxonomy))
    block.append(_render_languages_section(project, readonly=not can_manage_taxonomy))
    return block.collect()


def _render_new_cycle_form(project: sql.Project, scope: _SettingsScope, tab: str) -> htm.Element:
    """The form that opens a cycle by its text. A cycle with no release yet is reached this way."""
    return htm.form(
        ".d-flex.flex-wrap.align-items-center.gap-3.m-0",
        method="get",
        action=util.as_url(view, project_key=str(project.key)),
    )[
        htpy.input(type="hidden", name="tab", value=tab),
        htpy.label(".fw-semibold.mb-0", for_="new-cycle")["Add settings for cycle"],
        htpy.input(
            "#new-cycle.form-control.w-auto",
            type="text",
            name="new_cycle",
            value=scope.entered if scope.problem else "",
            required=True,
        ),
        htpy.button(".btn.btn-sm.btn-outline-primary", type="submit")["Add"],
    ]


def _render_project_card(project: sql.Project) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light")[htm.h3(".mb-2")["Project"]]
    if project.committee:
        pmc: htm.Element | str = htm.a(href=util.as_url(committees.view, name=project.committee.key))[
            project.committee.display_name
        ]
    else:
        pmc = "No committee"
    card.div(".card-body")[
        htm.div(".mb-1")[htm.span(".fw-semibold.me-2")["Key"], htm.code(".fs-6")[str(project.key)]],
        htm.div[htm.span(".fw-semibold.me-2")["PMC"], pmc],
    ]
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
            file_count = len(await shared.published.release_files(rel))
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
    scope: _SettingsScope,
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

        # Stay flat for projects with only the implicit "default" cycle. Once cycles
        # get used, headings surface automatically.
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

            # A cycle with no releases is hidden, so this stays a list of releases and not of empty
            # versions. Archived releases still count, so a cycle whose releases have all been archived
            # keeps its place and its dates rather than vanishing
            cycle_has_releases = bool(cycle_drafts or cycle_candidates or cycle_previews or cycle_released)
            if not cycle_has_releases:
                continue

            if show_cycle_heading:
                body.append(_render_cycle_heading(project, cycle, scope))
            # The default cycle is the catch-all for versions with no cycle of their own, and has no dates
            if (cycle.cycle != cycles.DEFAULT_CYCLE) and can_edit_policy:
                body.append(await _render_cycle_dates_form(project, cycle))
            elif (cycle.cycle != cycles.DEFAULT_CYCLE) and _cycle_has_dates(cycle):
                body.append(_render_cycle_dates_card(cycle))

            body.append(
                await _render_releases_sections(
                    project, cycle_drafts, cycle_candidates, cycle_previews, cycle_full, nested=show_cycle_heading
                )
            )

        if can_edit_policy:
            for cycle_text in scope.unmatched:
                body.div(".alert.alert-warning.mt-3.mb-0")[
                    f"{cycle_text} has release settings of its own, and no release is in that cycle. ",
                    htm.a(".alert-link", href=_settings_url(project, "compose", cycle_text))["Review them"],
                    ".",
                ]

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


async def _render_version_scheme_form(project: sql.Project, *, readonly: bool) -> htm.Element:
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
                "require_version_in_filenames": project.policy_require_version_in_filenames,
            },
            readonly=readonly,
        )
    return card.collect()


async def _render_vote_form(project: sql.Project, scope: _SettingsScope, *, readonly: bool) -> htm.Element:
    settings = scope.settings
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.bg-light.d-flex.justify-content-between.align-items-center")[
        htm.h3(".mb-0")[_settings_heading("Release policy - Vote options", scope)]
    ]

    flash_error_data = {} if readonly else await sessions.form_error_pop(quart.request.path)
    dev_lists = (await interaction.committee_dev_lists(project.committee)) if project.committee else []

    defaults_dict = {
        "project_key": str(project.key),
        "cycle": scope.cycle,
        "min_hours": settings.policy_min_hours,
        "release_checklist": settings.policy_release_checklist or "",
        "vote_comment_template": settings.policy_vote_comment_template or "",
        "vote_comment_template_url": settings.policy_vote_comment_template_url,
        "start_vote_subject": settings.policy_start_vote_subject or "",
        "start_vote_template": settings.policy_start_vote_template or "",
        "start_vote_template_url": settings.policy_start_vote_template_url,
        "finish_vote_template": settings.policy_finish_vote_template or "",
        "finish_vote_template_url": settings.policy_finish_vote_template_url,
    }

    release_checklist_widget = _textarea_with_variables(
        field_name="release_checklist",
        value=_flash_or_stored(flash_error_data, "release_checklist", settings.policy_release_checklist or ""),
        template_variables=construct.checklist_template_variables(),
        rows=3,
        documentation="Markdown text describing how to test release candidates.",
    )

    start_vote_subject_widget = _input_with_variables(
        field_name="start_vote_subject",
        value=_flash_or_stored(flash_error_data, "start_vote_subject", settings.policy_start_vote_subject or ""),
        template_variables=construct.vote_subject_template_variables(),
        documentation="Subject line template for vote emails.",
    )

    start_vote_template_widget = _textarea_with_variables(
        field_name="start_vote_template",
        value=_flash_or_stored(flash_error_data, "start_vote_template", settings.policy_start_vote_template or ""),
        template_variables=construct.vote_template_variables(),
        rows=18,
        documentation="Email template for messages to start a vote on a release.",
    )
    finish_vote_template_widget = _textarea_with_variables(
        field_name="finish_vote_template",
        value=_flash_or_stored(flash_error_data, "finish_vote_template", settings.policy_finish_vote_template or ""),
        template_variables=construct.finish_vote_template_variables(),
        rows=18,
        documentation="Email template for vote resolution messages.",
    )
    with card.block(htm.div, classes=".card-body") as card_body:
        if project.policy_vote_mode == sql.VoteMode.TRUSTED:
            card_body.p[
                "Streamlined voting is ",
                htm.strong["on"],
                ". This is set for all of the committee's projects on the ",
                htm.a(href=util.as_url(committees.view, name=str(project.committee_key)) + "#voting")["committee page"],
                ".",
            ]
        _append_cycle_overrides(card_body, project, scope, "vote", _VOTE_OVERRIDES, readonly)
        await form.render_block(
            card_body,
            model_cls=shared.projects.VotePolicyForm,
            action=_view_action(project, "vote", scope.cycle),
            submit_label="Save",
            defaults=defaults_dict,
            form_classes=".atr-canary.py-4.px-5",
            border=True,
            # wider_widgets=True,
            textarea_rows=10,
            custom={
                "email_to": _recipient_grid_widget(
                    project, settings, sql.RecipientAction.VOTE, readonly=readonly, dev_lists=dev_lists
                ),
                "release_checklist": release_checklist_widget,
                "start_vote_subject": start_vote_subject_widget,
                "start_vote_template": start_vote_template_widget,
                "finish_vote_template": finish_vote_template_widget,
            },
            skip=["email_cc", "email_bcc"],
            notes=_cycle_notes(scope, _VOTE_OVERRIDES, readonly=readonly),
            flash_error_data=flash_error_data,
            readonly=readonly,
        )
    return card.collect()


async def _request_approval_page(
    session: web.Committer, project_key: safe.ProjectKey, action: sql.ApprovalAction
) -> web.WerkzeugResponse | str:
    if not config.get().CAP_ROLE_ACCOUNT_TOKEN:
        return await session.redirect(projects, error=strings.CAP_NOT_CONFIGURED)
    async with db.session() as data:
        match await interaction.project_ready_for_approval(project_key, action, data):
            case str() as error:
                return await session.redirect(projects, error=error)
            case (project, committee):
                pass
    if not (user.is_committee_member(committee, session.uid) or session.is_admin):
        return await session.redirect(
            projects, error=f"Only members of the {committee.key} PMC can request this for '{project_key}'."
        )

    match action:
        case sql.ApprovalAction.ARCHIVE:
            post_route = post.projects.archive
        case sql.ApprovalAction.DELETE:
            post_route = post.projects.delete
        case sql.ApprovalAction.EMAIL_VOTING | sql.ApprovalAction.TRUSTED_VOTING:
            raise ValueError(f"{action.value} is not a project action")
        case sql.ApprovalAction.UNARCHIVE:
            post_route = post.projects.unarchive
    project_path = util.as_url(view, project_key=str(project_key))
    title = f"Request {_approval_noun(action)} of {project.display_name}"

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=project_path)["← Back to project"]]
    page.h1[title]
    page.p[_approval_notice(action)]

    await form.render_block(
        page,
        model_cls=shared.projects.RequestApprovalForm,
        action=util.as_url(post_route, project_key=str(project_key)),
        submit_label="Start approval vote",
        cancel_url=project_path,
        defaults={"vote": util.cap_approval_title(action, project_key)},
    )
    return await template.blank(title=title, description=_approval_notice(action), content=page.collect())


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


def _settings_heading(title: str, scope: _SettingsScope) -> str:
    return f"{title} for {scope.cycle}" if scope.cycle else title


def _settings_scope(
    project: sql.Project,
    project_cycles: list[sql.ProjectCycle],
    visible_cycle_keys: set[str],
    released_cycle_keys: set[str],
    all_overrides: dict[str, sql.CycleOverrides],
) -> _SettingsScope:
    in_use = [cycle.cycle for cycle in project_cycles if cycle.cycle_key in visible_cycle_keys]
    matched = frozenset(key.removeprefix(f"{project.key}-") for key in released_cycle_keys)
    # A project with one cycle has nothing to choose between, unless it has overrides
    selectable = bool(project.cycle_match) or bool(all_overrides)
    cycle = quart.request.args.get("cycle", "") if selectable else ""
    known = (cycles.can_override(cycle) and (cycle in in_use)) or (cycle in all_overrides)
    if not (known or (cycle and (cycles.planned_cycle(project, cycle) == cycle))):
        cycle = ""
    entered = quart.request.args.get("new_cycle", "").strip() if project.cycle_match else ""
    problem = ""
    if (cycles.can_override(entered) and (entered in in_use)) or (entered in all_overrides):
        cycle = entered
    elif entered:
        cycle, problem = _planned_cycle(project, entered)
    settings: sql.PolicySettings = project
    if cycle:
        settings = cycles.ReleaseSettings(project, all_overrides.get(cycle))
    return _SettingsScope(
        cycle=cycle,
        settings=settings,
        overrides=all_overrides,
        in_use=in_use,
        matched=matched,
        selectable=selectable,
        entered=entered,
        problem=problem,
    )


def _settings_url(project: sql.Project, tab: str, cycle: str = "") -> str:
    query = {"tab": tab, "cycle": cycle} if cycle else {"tab": tab}
    return util.as_url(view, project_key=str(project.key)) + "?" + urllib.parse.urlencode(query)


def _tab_nav(items: list[tuple[str, str, bool]], href: Callable[[str], str], classes: str) -> htm.Element:
    """A strip of tab links, each a key, a label and whether it is the active one."""
    links = []
    for key, label, active in items:
        link_classes = ".nav-link.active" if active else ".nav-link"
        links.append(htm.li(".nav-item", role="presentation")[htm.a(link_classes, href=href(key), role="tab")[label]])
    return htm.ul(classes, role="tablist")[*links]


def _taxonomy_add_form(project: sql.Project, variant: str, field_name: str, placeholder: str) -> htm.Element:
    return htm.form(
        ".mb-3",
        method="post",
        action=_view_action(project, "metadata"),
    )[
        form.csrf_input(),
        htpy.input(type="hidden", name="project_key", value=str(project.key)),
        htpy.input(type="hidden", name="variant", value=variant),
        htm.div(".d-flex.align-items-center")[
            htpy.input(".form-control.form-control-sm.me-2", type="text", name=field_name, placeholder=placeholder),
            htpy.button(".btn.btn-sm.btn-success.text-nowrap.pe-3", type="submit")[htpy.i(".bi.bi-plus"), " Add"],
        ],
    ]


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


def _upload_restriction_defaults(options: sql.ProjectSecurityOptions | None) -> dict[str, object]:
    if options is None:
        return {"ref_type": shared.projects.UploadRefType.ANY.value}
    ref_types = {stored: ref_type for ref_type, stored in shared.projects.UPLOAD_REF_TYPES.items()}
    return {
        "workflow_paths": "\n".join(options.github_upload_workflow_paths),
        "ref_type": ref_types[options.github_upload_ref_type].value,
        "ref_patterns": "\n".join(options.github_upload_ref_patterns),
    }


def _view_action(project: sql.Project, tab: str, cycle: str = "") -> str:
    # The cycle goes in the address as well as the form, so that a rejected form comes back to the same cycle
    query = {"tab": tab, "cycle": cycle} if cycle else {"tab": tab}
    return util.as_url(post.projects.view, name=str(project.key)) + "?" + urllib.parse.urlencode(query)
