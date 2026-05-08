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

from typing import TYPE_CHECKING, Any, Final, Literal

import quart

import atr.blueprints.post as post
import atr.get as get
import atr.models.safe as safe
import atr.models.unsafe as unsafe
import atr.shared as shared
import atr.storage as storage
import atr.web as web

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@post.typed
async def add_project(
    session: web.Committer,
    _project_add: Literal["project/add"],
    committee_key: safe.CommitteeKey,
    project_form: shared.projects.AddProjectForm,
) -> web.WerkzeugResponse:
    """
    URL: /project/add/<committee_key>
    """
    display_name = project_form.display_name
    label = project_form.label

    if committee_key != project_form.committee_key:
        raise ValueError(f"Invalid committee key: {committee_key}")

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(safe.ProjectKey(str(committee_key)))
        try:
            await wacm.project.create(committee_key, display_name, label)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.add_project, committee_key=str(committee_key), error=f"Error adding project: {e}"
            )

    return await session.redirect(
        get.projects.view, project_key=label, success=f"Project '{display_name}' added successfully."
    )


@post.typed
async def delete(
    session: web.Committer,
    _project_delete: Literal["project/delete"],
    delete_selected_project_form: shared.projects.DeleteSelectedProject,
) -> web.WerkzeugResponse:
    """
    URL: /project/delete
    Delete a project created by the user.
    """
    project_key = delete_selected_project_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.project.delete(project_key)
        except storage.AccessError as e:
            # TODO: Redirect to committees
            return await session.redirect(get.projects.projects, error=f"Error deleting project: {e}")

    # TODO: Redirect to committees
    return await session.redirect(get.projects.projects, success=f"Project '{project_key}' deleted successfully.")


@post.typed
async def view(
    session: web.Committer,
    _projects: Literal["projects"],
    name: unsafe.UnsafeStr,
    project_form: shared.projects.ProjectViewForm,
) -> web.WerkzeugResponse:
    """
    URL: /projects/<name>
    """
    return await _VIEW_HANDLERS[type(project_form)](session, project_form)


async def _metadata_category_add(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, category_to_add: str
) -> bool:
    try:
        return await wacm.project.category_add(project_key, category_to_add.strip())
    except storage.AccessError as e:
        await quart.flash(f"Error adding category: {e}", "error")
        return False


async def _metadata_category_remove(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, action_value: str
) -> bool:
    try:
        return await wacm.project.category_remove(project_key, action_value)
    except storage.AccessError as e:
        await quart.flash(f"Error removing category: {e}", "error")
        return False


async def _metadata_language_add(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, language_to_add: str
) -> bool:
    try:
        return await wacm.project.language_add(project_key, language_to_add)
    except storage.AccessError as e:
        await quart.flash(f"Error adding language: {e}", "error")
        return False


async def _metadata_language_remove(
    wacm: storage.WriteAsCommitteeMember, project_key: safe.ProjectKey, action_value: str
) -> bool:
    try:
        return await wacm.project.language_remove(project_key, action_value)
    except storage.AccessError as e:
        await quart.flash(f"Error removing language: {e}", "error")
        return False


async def _process_add_category(
    session: web.Committer, add_category_form: shared.projects.AddCategoryForm
) -> web.WerkzeugResponse:
    project_key = add_category_form.project_key
    category_to_add = add_category_form.category_to_add.strip()

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_category_add(wacm, project_key, category_to_add)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=str(project_key),
            tab="metadata",
            success=f"Category '{category_to_add}' added.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=str(project_key),
        tab="metadata",
        error=f"Category '{category_to_add}' already exists.",
    )


async def _process_add_language(
    session: web.Committer, add_language_form: shared.projects.AddLanguageForm
) -> web.WerkzeugResponse:
    project_key = add_language_form.project_key
    language_to_add = add_language_form.language_to_add.strip()

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_language_add(wacm, project_key, language_to_add)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=str(project_key),
            tab="metadata",
            success=f"Language '{language_to_add}' added.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=str(project_key),
        tab="metadata",
        error=f"Language '{language_to_add}' already exists.",
    )


async def _process_compose_form(
    session: web.Committer, compose_form: shared.projects.ComposePolicyForm
) -> web.WerkzeugResponse:
    project_key = compose_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.policy.edit_compose(compose_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="compose",
                error=f"Error editing compose policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="compose",
        success="Compose options saved successfully.",
    )


async def _process_delete_project(
    session: web.Committer, delete_form: shared.projects.DeleteProjectForm
) -> web.WerkzeugResponse:
    project_key = delete_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.project.delete(project_key)
        except storage.AccessError as e:
            return await session.redirect(get.projects.projects, error=f"Error deleting project: {e}")

    return await session.redirect(get.projects.projects, success=f"Project '{project_key}' deleted successfully.")


async def _process_edit_cycle_dates_form(
    session: web.Committer, edit_form: shared.projects.EditCycleDatesForm
) -> web.WerkzeugResponse:
    project_key = edit_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.policy.edit_cycle_dates(edit_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=str(project_key),
                tab="lifecycle",
                error=f"Error saving cycle dates: {e}",
            )
        except ValueError as e:
            return await session.redirect(
                get.projects.view, project_key=str(project_key), tab="lifecycle", error=str(e)
            )

    return await session.redirect(
        get.projects.view, project_key=str(project_key), tab="lifecycle", success="Cycle dates saved."
    )


async def _process_edit_metadata_form(
    session: web.Committer, edit_form: shared.projects.EditMetadataForm
) -> web.WerkzeugResponse:
    project_key = edit_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.project.edit_metadata(edit_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=str(project_key),
                tab="metadata",
                error=f"Error saving metadata: {e}",
            )
        except ValueError as e:
            return await session.redirect(get.projects.view, project_key=str(project_key), tab="metadata", error=str(e))

    return await session.redirect(
        get.projects.view, project_key=str(project_key), tab="metadata", success="Metadata saved."
    )


async def _process_edit_version_scheme_form(
    session: web.Committer, edit_form: shared.projects.EditVersionSchemeForm
) -> web.WerkzeugResponse:
    project_key = edit_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.policy.edit_version_scheme(edit_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=str(project_key),
                tab="lifecycle",
                error=f"Error saving version scheme: {e}",
            )
        except ValueError as e:
            return await session.redirect(
                get.projects.view, project_key=str(project_key), tab="lifecycle", error=str(e)
            )

    return await session.redirect(
        get.projects.view, project_key=str(project_key), tab="lifecycle", success="Version scheme saved."
    )


async def _process_finish_form(
    session: web.Committer, finish_form: shared.projects.FinishPolicyForm
) -> web.WerkzeugResponse:
    project_key = finish_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.policy.edit_finish(finish_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="finish",
                error=f"Error editing finish policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="finish",
        success="Finish options saved successfully.",
    )


async def _process_remove_category(
    session: web.Committer, remove_form: shared.projects.RemoveCategoryForm
) -> web.WerkzeugResponse:
    project_key = remove_form.project_key
    category_to_remove = remove_form.category_to_remove

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_category_remove(wacm, project_key, category_to_remove)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=str(project_key),
            tab="metadata",
            success=f"Category '{category_to_remove}' removed.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=str(project_key),
        tab="metadata",
        error=f"Category '{category_to_remove}' does not exist.",
    )


async def _process_remove_language(
    session: web.Committer, remove_form: shared.projects.RemoveLanguageForm
) -> web.WerkzeugResponse:
    project_key = remove_form.project_key
    language_to_remove = remove_form.language_to_remove

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        modified = await _metadata_language_remove(wacm, project_key, language_to_remove)

    if modified:
        return await session.redirect(
            get.projects.view,
            project_key=project_key,
            tab="metadata",
            success=f"Language '{language_to_remove}' removed.",
        )
    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="metadata",
        error=f"Language '{language_to_remove}' does not exist.",
    )


async def _process_trusted_publishing_form(
    session: web.Committer, tp_form: shared.projects.TrustedPublishingPolicyForm
) -> web.WerkzeugResponse:
    project_key = tp_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.policy.edit_trusted_publishing(tp_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="trusted-publishing",
                error=f"Error editing Trusted Publishing policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="trusted-publishing",
        success="Trusted Publishing options saved successfully.",
    )


async def _process_vote_form(session: web.Committer, vote_form: shared.projects.VotePolicyForm) -> web.WerkzeugResponse:
    project_key = vote_form.project_key

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_key)
        try:
            await wacm.policy.edit_vote(vote_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view,
                project_key=project_key,
                tab="vote",
                error=f"Error editing vote policy: {e}",
            )

    return await session.redirect(
        get.projects.view,
        project_key=project_key,
        tab="vote",
        success="Vote options saved successfully.",
    )


_VIEW_HANDLERS: Final[dict[type, Callable[[web.Committer, Any], Awaitable[web.WerkzeugResponse]]]] = {
    shared.projects.AddCategoryForm: _process_add_category,
    shared.projects.AddLanguageForm: _process_add_language,
    shared.projects.ComposePolicyForm: _process_compose_form,
    shared.projects.DeleteProjectForm: _process_delete_project,
    shared.projects.EditCycleDatesForm: _process_edit_cycle_dates_form,
    shared.projects.EditMetadataForm: _process_edit_metadata_form,
    shared.projects.EditVersionSchemeForm: _process_edit_version_scheme_form,
    shared.projects.FinishPolicyForm: _process_finish_form,
    shared.projects.TrustedPublishingPolicyForm: _process_trusted_publishing_form,
    shared.projects.RemoveCategoryForm: _process_remove_category,
    shared.projects.RemoveLanguageForm: _process_remove_language,
    shared.projects.VotePolicyForm: _process_vote_form,
}
