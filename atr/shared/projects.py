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

import re
from typing import Annotated, Literal

import pydantic

import atr.calver as calver
import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.util as util

type COMPOSE = Literal["compose"]
type EDIT_CYCLE_DATES = Literal["edit_cycle_dates"]
type EDIT_METADATA = Literal["edit_metadata"]
type EDIT_VERSION_SCHEME = Literal["edit_version_scheme"]
type FINISH = Literal["finish"]
type SECURITY = Literal["security"]
type TRUSTED_PUBLISHING = Literal["trusted_publishing"]
type VOTE = Literal["vote"]
type ADD_CATEGORY = Literal["add_category"]
type REMOVE_CATEGORY = Literal["remove_category"]
type ADD_LANGUAGE = Literal["add_language"]
type REMOVE_LANGUAGE = Literal["remove_language"]
type ARCHIVE_SELECTED_RELEASE = Literal["archive_selected_release"]
type CONFIRM_RELEASE_ARCHIVAL = Literal["confirm_release_archival"]


def _strip_whitespace(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _validate_display_name(val: str) -> str:
    """Apply the Apache project display name rules. Returns the normalised value.

    The form's "Apache" prefix is added automatically, so callers may submit
    either "Example Components" or "Apache Example Components" — both yield
    "Apache Example Components". The leading "Apache" is stripped
    case-insensitively, so "apache example" and "APACHE Example" also work.
    Raises ValueError on failure. Shared between AddProjectForm and
    EditMetadataForm so the two stay in lockstep.
    """
    display_name = val.strip()
    # Normalise spaces in the display name
    display_name = re.sub(r"  +", " ", display_name)

    if not display_name:
        raise ValueError("Name is required.")

    # Strip a leading "Apache" (any case) so users who type the prefix out of
    # habit aren't punished. The canonical "Apache " is reapplied below.
    leading_apache = re.match(r"^[Aa][Pp][Aa][Cc][Hh][Ee](?:\s+|$)", display_name)
    if leading_apache:
        display_name = display_name[leading_apache.end() :]

    if not display_name:
        raise ValueError("Name must have at least two words.")

    display_name = f"Apache {display_name}"

    display_name_words = display_name.split(" ")

    # Validate display name uses correct case. Each non-Apache word must either
    # be on the irregular-word allow-list or match one of the structural regexes.
    # This check is sufficient: every regex restricts to alphanumerics, so any
    # string that passes here is already character-safe by construction.
    allowed_irregular_words = {
        ".NET",
        "(APR)",
        "C++",
        "Empire-db",
        "Lucene.NET",
        "Lucene.Net",
        "Lucene.net",
        "for",
        "jclouds",
    }
    r_pascal_case = re.compile(r"^([A-Z][0-9a-z]*)+$")
    r_camel_case = re.compile(r"^[a-z]*([A-Z][0-9a-z]*)+$")
    r_mod_case = re.compile(r"^mod(_[0-9a-z]+)+$")
    for display_name_word in display_name_words[1:]:
        if display_name_word in allowed_irregular_words:
            continue
        is_pascal_case = r_pascal_case.match(display_name_word)
        is_camel_case = r_camel_case.match(display_name_word)
        is_mod_case = r_mod_case.match(display_name_word)
        if not (is_pascal_case or is_camel_case or is_mod_case):
            raise ValueError(f"Name word {display_name_word!r} must be in PascalCase, camelCase, or mod_ case.")

    return display_name


class AddProjectForm(form.Form):
    committee_key: safe.CommitteeKey = form.label("Committee key", widget=form.Widget.HIDDEN)
    committee_key_display: str = form.label(description="Committee key", widget=form.Widget.STATIC, default="")
    display_name: str = form.label(
        "Project name",
        'For example, "Example" or "Example Components". Use title case; the "Apache" prefix is added for you.',
        prefix="Apache",
    )
    key: Annotated[safe.ProjectKey, pydantic.BeforeValidator(_strip_whitespace)] = form.label(
        "Project key",
        'For example, "example" or "example-components". '
        "You must start with your committee key (above), and you must use lower case.",
    )

    @pydantic.field_validator("display_name", mode="before")
    @classmethod
    def validate_display_name(cls, val: str) -> str:
        return _validate_display_name(val)

    @pydantic.field_validator("key", mode="after")
    @classmethod
    def validate_fields(cls, val: safe.ProjectKey, info: pydantic.ValidationInfo) -> safe.ProjectKey:
        committee_key = str(info.data.get("committee_key"))
        key = str(val)

        if not (key.startswith(committee_key + "-") or (key == committee_key)):
            raise ValueError(f"Key must be '{committee_key}' or start with '{committee_key}-'.")

        return val


class ComposePolicyForm(form.Form):
    variant: COMPOSE = form.value(COMPOSE)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    license_check_mode: form.Enum[sql.LicenseCheckMode] = form.label(
        "Source artifact license checker",
        "Only affects source artifacts. Lightweight checks ALWAYS RUN on binary artifacts.",
        widget=form.Widget.RADIO,
    )
    source_excludes_lightweight: str = form.label(
        "Lightweight source excludes",
        "Patterns using .gitignore syntax for files to exclude"
        " from lightweight license header checks on source artifacts."
        " Prefix with /archive-pattern/ to scope a pattern to one archive (globs allowed).",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )
    source_excludes_rat: str = form.label(
        "RAT source excludes",
        "RAT exclude file contents for source artifacts. Used only when no .rat-excludes file exists in the archive.",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )
    file_tag_mappings: str = form.label(
        "Tagging spec",
        "Spec for which files should be tagged for release in specific distribution types, YAML format",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )


class VotePolicyForm(form.Form):
    variant: VOTE = form.value(VOTE)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    email_to: str = form.label(
        "Default vote recipient",
        "The mailing list vote emails default to. Release managers can still override this when starting a vote.",
        widget=form.Widget.CUSTOM,
        default="",
    )
    email_cc: form.StrList = form.label("Default CC")
    email_bcc: form.StrList = form.label("Default BCC")
    vote_mode: sql.VoteMode = form.label(
        "Vote mode",
        widget=form.Widget.CUSTOM,
    )
    min_hours: form.Int = form.label(
        "Minimum voting period",
        "The minimum time to run the vote, in hours. Must be 0 or between 72 and 144 inclusive. "
        "If 0, then wait until 3 +1 votes and more +1 than -1.",
        default=72,
    )
    release_checklist: str = form.label(
        "Release checklist",
        widget=form.Widget.CUSTOM,
    )
    vote_comment_template: str = form.label(
        "Vote comment template",
        "Plain text template for vote comments. Voters can edit before submitting.",
        widget=form.Widget.TEXTAREA,
        rows=6,
    )
    start_vote_subject: str = form.label(
        "Start vote subject",
        widget=form.Widget.CUSTOM,
    )
    start_vote_template: str = form.label(
        "Start vote template",
        widget=form.Widget.CUSTOM,
    )
    finish_vote_template: str = form.label(
        "Finish vote template",
        widget=form.Widget.CUSTOM,
    )

    @pydantic.model_validator(mode="after")
    def validate_vote_fields(self) -> VotePolicyForm:
        validation.validate_policy_min_hours(self.min_hours)
        return self


class SecurityForm(form.Form):
    variant: SECURITY = form.value(SECURITY)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    security_contact: str = form.label(
        "Security contact",
        "Where security vulnerability reports for this project should be sent.",
        widget=form.Widget.CUSTOM,
        default="",
    )
    threat_model_link: form.OptionalURL = form.label(
        "Threat model",
        "URL of the project's published, human-readable threat model.",
    )
    threat_model_src_link: form.OptionalURL = form.label(
        "Threat model source",
        "URL of the threat model's plain-text source document.",
    )


class EditCycleDatesForm(form.Form):
    variant: EDIT_CYCLE_DATES = form.value(EDIT_CYCLE_DATES)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    cycle_key: str = form.label("Cycle key", widget=form.Widget.HIDDEN)
    eod: form.OptionalDate = form.label(
        "End of development",
        "When active development on this cycle is planned to stop.",
    )
    eos: form.OptionalDate = form.label(
        "End of support",
        "When this cycle is planned to stop receiving support.",
    )
    eol: form.OptionalDate = form.label(
        "End of life",
        "When this cycle is planned to be retired entirely.",
    )
    lts: form.Bool = form.label(
        "Long-term support",
        "Mark this cycle as a long-term support line.",
    )


class EditMetadataForm(form.Form):
    variant: EDIT_METADATA = form.value(EDIT_METADATA)
    project_key: safe.ProjectKey = form.label("Project key", widget=form.Widget.HIDDEN)
    display_name: str = form.label(
        "Project name",
        'For example, "Example" or "Example Components". Use title case; the "Apache" prefix is added for you.',
        prefix="Apache",
    )
    description: str = form.label(description="Project description", widget=form.Widget.TEXTAREA)
    short_description: str = form.label(description="Short description", widget=form.Widget.TEXT)
    homepage: form.OptionalURL = form.label(
        "Homepage",
        "Project website URL.",
    )
    lifecycle_page: form.OptionalURL = form.label(
        "Lifecycle page",
        "URL of the page describing this project's release support and lifecycle plans.",
    )
    download_page: form.OptionalURL = form.label(
        "Download page",
        "URL of the project's official download page.",
    )
    bug_database: form.OptionalURL = form.label(
        "Bug database",
        "URL of the project's issue tracker (Bugzilla, JIRA, GitHub Issues, etc).",
    )
    mailing_lists: form.OptionalURL = form.label(
        "Mailing lists page",
        "URL of the page on the project website that lists its mailing lists.",
    )
    repositories: form.URIList = form.label(
        "Repositories",
        "Repository URLs, one per line.",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )
    standards: form.URIList = form.label(
        "Standards",
        "URLs of standards this project implements, one per line.",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )

    @pydantic.field_validator("display_name", mode="before")
    @classmethod
    def validate_display_name(cls, val: str) -> str:
        return _validate_display_name(val)


class EditVersionSchemeForm(form.Form):
    variant: EDIT_VERSION_SCHEME = form.value(EDIT_VERSION_SCHEME)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    version_method: form.Enum[sql.VersionMethod] = form.label(
        "Version method",
        "How this project numbers releases. Determines how cycles are derived from versions.",
        widget=form.Widget.RADIO,
    )
    version_pattern: str = form.label(
        "Version pattern",
        "Optional regex that release versions must match. Leave empty to accept any version.",
    )
    cycle_match: str = form.label(
        "Cycle match",
        "For semver projects: a regex applied to the version string; capture group 1 is the cycle name."
        r" For example, (\d+)\..* maps version 2.0.1 into cycle 2."
        " Leave empty for projects that keep a single default cycle.",
    )
    calver_format: str = form.label(
        "Cycle format",
        "For calver projects: a date format describing the version shape."
        " Tokens YYYY/YY, MM/M, DD/D and N (a serial), with literal separators."
        " Wrap the part that names the cycle in parentheses, e.g. (YY.MM).N."
        " Leave empty for projects that keep a single default cycle.",
    )
    branch_template: str = form.label(
        "Branch template",
        "Optional naming hint for source branches per cycle. Not currently enforced.",
    )

    @pydantic.field_validator("version_pattern", mode="before")
    @classmethod
    def validate_version_scheme(cls, val: str) -> str:
        version_pattern = val.strip()
        if version_pattern:
            try:
                re.compile(version_pattern)
            except re.error as e:
                raise ValueError(f"Version pattern is not a valid regex: {e}")
        return val

    @pydantic.field_validator("cycle_match", mode="before")
    @classmethod
    def validate_cycle_match(cls, val: str) -> str:
        cycle_match = val.strip()
        if cycle_match:
            try:
                compiled = re.compile(cycle_match)
            except re.error as e:
                raise ValueError(f"Cycle match is not a valid regex: {e}")
            # capture group 1 is the cycle name
            if compiled.groups < 1:
                raise ValueError("Cycle match must contain at least one capture group for the cycle name.")

        return val

    @pydantic.field_validator("calver_format", mode="before")
    @classmethod
    def validate_calver_format(cls, val: str) -> str:
        calver_format = val.strip()
        if calver_format:
            calver.validate(calver_format)
        return val


class FinishPolicyForm(form.Form):
    variant: FINISH = form.value(FINISH)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    archive_prior_release: form.Bool = form.label(
        "Allow auto-archive",
        "If enabled, allows a new release to auto archive the prior release in the cycle",
        widget=form.Widget.CHECKBOX,
        default=False,
    )
    announce_release_subject: str = form.label(
        "Announce release subject",
        widget=form.Widget.CUSTOM,
    )
    announce_release_template: str = form.label(
        "Announce release template",
        widget=form.Widget.CUSTOM,
    )
    email_to: str = form.label(
        "Default announce recipient",
        "The mailing list announcements default to. Release managers can still override this when announcing.",
        widget=form.Widget.CUSTOM,
        default="",
    )
    email_cc: form.StrList = form.label("Default CC")
    email_bcc: form.StrList = form.label("Default BCC")
    download_path_suffix: str = form.label(
        "Default download path suffix",
        "Pre-fills the SVN publish path. May use {{MAJOR_VERSION}}, {{PROJECT_KEY}}, and {{VERSION}}."
        " Leave empty for the default (project root for a top-level project,"
        " {{PROJECT_KEY}}-{{VERSION}} for a subproject).",
        default="",
    )

    @pydantic.field_validator("download_path_suffix", mode="before")
    @classmethod
    def validate_download_path_suffix(cls, val: str) -> str:
        validation.validate_download_path_suffix(val)
        return val


class TrustedPublishingPolicyForm(form.Form):
    variant: TRUSTED_PUBLISHING = form.value(TRUSTED_PUBLISHING)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    github_repository_name: str = form.label(
        "GitHub repository name",
        "The name of the GitHub repository to use for the release, excluding the apache/ prefix.",
    )
    github_repository_branch: str = form.label(
        "GitHub repository branch",
        "Branch used for release builds (for example, main or 2.5.x). Optional.",
    )
    # TODO: We should think about making these a list[RelUrlPath]
    # But note that they contain .github, so that will be awkward
    github_compose_workflow_path: str = form.label(
        "Compose workflow paths",
        "Workflows permitted to perform compose-phase operations for this project. One path per line,"
        " each starting with .github/workflows/ (for example, .github/workflows/release-compose.yml).",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )
    github_vote_workflow_path: str = form.label(
        "Vote workflow paths",
        "Workflows permitted to perform vote-phase operations for this project. One path per line,"
        " each starting with .github/workflows/.",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )
    github_finish_workflow_path: str = form.label(
        "Finish workflow paths",
        "Workflows permitted to perform finish-phase operations for this project. One path per line,"
        " each starting with .github/workflows/.",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )

    @pydantic.model_validator(mode="after")
    def validate_trusted_publishing_fields(self) -> TrustedPublishingPolicyForm:
        github_repository_name = self.github_repository_name.strip()
        github_repository_branch = self.github_repository_branch.strip()

        all_paths: list[str] = []
        for raw in (
            self.github_compose_workflow_path,
            self.github_vote_workflow_path,
            self.github_finish_workflow_path,
        ):
            all_paths.extend(p.strip() for p in (raw or "").split("\n") if p.strip())

        util.validate_trusted_publishing_constraints(github_repository_name, github_repository_branch, all_paths)

        return self


class AddCategoryForm(form.Form):
    variant: ADD_CATEGORY = form.value(ADD_CATEGORY)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    category_to_add: str = form.label("New category name")


class RemoveCategoryForm(form.Form):
    variant: REMOVE_CATEGORY = form.value(REMOVE_CATEGORY)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    category_to_remove: str = form.label("Category to remove", widget=form.Widget.HIDDEN)


class AddLanguageForm(form.Form):
    variant: ADD_LANGUAGE = form.value(ADD_LANGUAGE)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    language_to_add: str = form.label("New language name")


class RemoveLanguageForm(form.Form):
    variant: REMOVE_LANGUAGE = form.value(REMOVE_LANGUAGE)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    language_to_remove: str = form.label("Language to remove", widget=form.Widget.HIDDEN)


class ArchiveSelectedProject(form.Form):
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)


class ArchiveSelectedRelease(form.Form):
    variant: ARCHIVE_SELECTED_RELEASE = form.value(ARCHIVE_SELECTED_RELEASE)
    confirm_archive: Literal["ARCHIVE"] = form.label("Confirmation", "Type ARCHIVE to confirm.")


class CompleteApprovalRequest(form.Form):
    approval_request_id: form.Int = form.label("Approval request ID", widget=form.Widget.HIDDEN)


class ConfirmReleaseArchival(form.Form):
    variant: CONFIRM_RELEASE_ARCHIVAL = form.value(CONFIRM_RELEASE_ARCHIVAL)
    confirm_archive: Literal["ARCHIVE"] = form.label("Confirmation", "Type ARCHIVE to confirm.")


class DeleteSelectedProject(form.Form):
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)


type FileViewForm = Annotated[
    ArchiveSelectedRelease | ConfirmReleaseArchival,
    form.DISCRIMINATOR,
]


type ProjectViewForm = Annotated[
    ComposePolicyForm
    | EditCycleDatesForm
    | EditMetadataForm
    | EditVersionSchemeForm
    | FinishPolicyForm
    | SecurityForm
    | TrustedPublishingPolicyForm
    | VotePolicyForm
    | AddCategoryForm
    | RemoveCategoryForm
    | AddLanguageForm
    | RemoveLanguageForm,
    form.DISCRIMINATOR,
]
