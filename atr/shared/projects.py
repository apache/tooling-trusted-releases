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

import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util

type COMPOSE = Literal["compose"]
type FINISH = Literal["finish"]
type TRUSTED_PUBLISHING = Literal["trusted_publishing"]
type VOTE = Literal["vote"]
type ADD_CATEGORY = Literal["add_category"]
type REMOVE_CATEGORY = Literal["remove_category"]
type ADD_LANGUAGE = Literal["add_language"]
type REMOVE_LANGUAGE = Literal["remove_language"]
type DELETE_PROJECT = Literal["delete_project"]


class AddProjectForm(form.Form):
    committee_key: safe.CommitteeKey = form.label("Committee name", widget=form.Widget.HIDDEN)
    display_name: str = form.label(
        "Display name",
        'For example, "Apache Example" or "Apache Example Components". '
        'You must start with "Apache " and you must use title case.',
    )
    label: str = form.label(
        "Label",
        'For example, "example" or "example-components". '
        "You must start with your committee label, and you must use lower case.",
    )

    @pydantic.model_validator(mode="after")
    def validate_fields(self) -> AddProjectForm:
        committee_key = str(self.committee_key)
        display_name = self.display_name.strip()
        label = self.label.strip()

        # Normalise spaces in the display name
        display_name = re.sub(r"  +", " ", display_name)

        # We must use object.__setattr__ to avoid calling the model validator again
        object.__setattr__(self, "display_name", display_name)

        # Validate display name starts with "Apache"
        display_name_words = display_name.split(" ")
        if display_name_words[0] != "Apache":
            raise ValueError("The first display name word must be 'Apache'.")

        # Validate display name has at least two words
        if not display_name_words[1:]:
            raise ValueError("The display name must have at least two words.")

        # Validate display name uses correct case
        allowed_irregular_words = {".NET", "C++", "Empire-db", "Lucene.NET", "for", "jclouds"}
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
                raise ValueError("Display name words must be in PascalCase, camelCase, or mod_ case.")

        # Validate display name is alphanumeric with spaces, dots, and plus signs
        if not display_name.replace(" ", "").replace(".", "").replace("+", "").isalnum():
            raise ValueError("Display name must be alphanumeric and may include spaces or dots or plus signs.")

        # Validate label starts with committee name
        if not (label.startswith(committee_key + "-") or (label == committee_key)):
            raise ValueError(f"Label must be '{committee_key}' or start with '{committee_key}-'.")

        # Validate label is lowercase
        if not label.islower():
            raise ValueError("Label must be all lower case.")

        # Validate label is alphanumeric with hyphens
        if not label.replace("-", "").isalnum():
            raise ValueError("Label must be alphanumeric and may include hyphens.")

        return self


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
        " from lightweight license header checks on source artifacts.",
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
    mailto_addresses: form.Email = form.label(
        "Email",
        f"The mailing list where vote emails are sent. This is usually your dev list. "
        f"ATR will currently only send test announcement emails to {util.USER_TESTS_ADDRESS}.",
    )
    manual_vote: form.Bool = form.label(
        "Manual voting process",
        "If this is set then the vote will be completely manual and following policy is ignored.",
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

    @pydantic.model_validator(mode="after")
    def validate_vote_fields(self) -> VotePolicyForm:
        min_hours = self.min_hours
        if (min_hours != 0) and ((min_hours < 72) or (min_hours > 144)):
            raise ValueError("Minimum voting period must be 0 or between 72 and 144 hours inclusive.")

        return self


class FinishPolicyForm(form.Form):
    variant: FINISH = form.value(FINISH)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)
    announce_release_subject: str = form.label(
        "Announce release subject",
        widget=form.Widget.CUSTOM,
    )
    announce_release_template: str = form.label(
        "Announce release template",
        widget=form.Widget.CUSTOM,
    )
    preserve_download_files: form.Bool = form.label(
        "Preserve download files",
        "If enabled, existing download files will not be overwritten.",
    )


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
        "GitHub workflow paths for the compose phase, including the .github/workflows/ prefix.",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )
    github_vote_workflow_path: str = form.label(
        "Vote workflow paths",
        "GitHub workflow paths for the vote phase, including the .github/workflows/ prefix.",
        widget=form.Widget.TEXTAREA,
        rows=3,
    )
    github_finish_workflow_path: str = form.label(
        "Finish workflow paths",
        "GitHub workflow paths for the finish phase, including the .github/workflows/ prefix.",
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


class DeleteProjectForm(form.Form):
    variant: DELETE_PROJECT = form.value(DELETE_PROJECT)
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)


class DeleteSelectedProject(form.Form):
    project_key: safe.ProjectKey = form.label("Project name", widget=form.Widget.HIDDEN)


type ProjectViewForm = Annotated[
    ComposePolicyForm
    | FinishPolicyForm
    | TrustedPublishingPolicyForm
    | VotePolicyForm
    | AddCategoryForm
    | RemoveCategoryForm
    | AddLanguageForm
    | RemoveLanguageForm
    | DeleteProjectForm,
    form.DISCRIMINATOR,
]
