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

from typing import Literal

import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.db as db
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@post.typed
async def add_project(
    session: web.Committer,
    _project_add: Literal["project/add"],
    committee_name: safe.CommitteeKey,
    project_form: shared.projects.AddProjectForm,
) -> web.WerkzeugResponse:
    """
    URL: /project/add/<committee_name>
    """
    display_name = project_form.display_name
    label = project_form.label

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(safe.ProjectName(str(committee_name)))
        try:
            await wacm.project.create(committee_name, display_name, label)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.add_project, committee_name=str(committee_name), error=f"Error adding project: {e}"
            )

    return await session.redirect(
        get.projects.view, project_name=label, success=f"Project '{display_name}' added successfully."
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
    project_name = delete_selected_project_form.project_name

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        try:
            await wacm.project.delete(project_name)
        except storage.AccessError as e:
            # TODO: Redirect to committees
            return await session.redirect(get.projects.projects, error=f"Error deleting project: {e}")

    # TODO: Redirect to committees
    return await session.redirect(get.projects.projects, success=f"Project '{project_name}' deleted successfully.")


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
    match project_form:
        case shared.projects.AddCategoryForm() as add_category_form:
            return await _process_add_category(session, add_category_form)

        case shared.projects.AddLanguageForm() as add_language_form:
            return await _process_add_language(session, add_language_form)

        case shared.projects.ComposePolicyForm() as compose_form:
            return await _process_compose_form(session, compose_form)

        case shared.projects.DeleteProjectForm() as delete_form:
            return await _process_delete_project(session, delete_form)

        case shared.projects.FinishPolicyForm() as finish_form:
            return await _process_finish_form(session, finish_form)

        case shared.projects.RemoveCategoryForm() as remove_form:
            return await _process_remove_category(session, remove_form)

        case shared.projects.RemoveLanguageForm() as remove_form:
            return await _process_remove_language(session, remove_form)

        case shared.projects.VotePolicyForm() as vote_form:
            return await _process_vote_form(session, vote_form)


async def _metadata_category_add(
    wacm: storage.WriteAsCommitteeMember, project: sql.Project, category_to_add: str
) -> bool:
    try:
        return await wacm.project.category_add(project, category_to_add.strip())
    except storage.AccessError as e:
        await quart.flash(f"Error adding category: {e}", "error")
        return False


async def _metadata_category_remove(
    wacm: storage.WriteAsCommitteeMember, project: sql.Project, action_value: str
) -> bool:
    try:
        return await wacm.project.category_remove(project, action_value)
    except storage.AccessError as e:
        await quart.flash(f"Error removing category: {e}", "error")
        return False


async def _metadata_language_add(
    wacm: storage.WriteAsCommitteeMember, project: sql.Project, language_to_add: str
) -> bool:
    try:
        return await wacm.project.language_add(project, language_to_add)
    except storage.AccessError as e:
        await quart.flash(f"Error adding language: {e}", "error")
        return False


async def _metadata_language_remove(
    wacm: storage.WriteAsCommitteeMember, project: sql.Project, action_value: str
) -> bool:
    try:
        return await wacm.project.language_remove(project, action_value)
    except storage.AccessError as e:
        await quart.flash(f"Error removing language: {e}", "error")
        return False


async def _process_add_category(
    session: web.Committer, add_category_form: shared.projects.AddCategoryForm
) -> web.WerkzeugResponse:
    project_name = add_category_form.project_name
    category_to_add = add_category_form.category_to_add.strip()

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        async with db.session() as data:
            project = await data.project(name=str(project_name)).demand(
                base.ASFQuartException(f"Project {project_name} not found", errorcode=404)
            )
        modified = await _metadata_category_add(wacm, project, category_to_add)

    if modified:
        return await session.redirect(
            get.projects.view, project_name=str(project_name), success=f"Category '{category_to_add}' added."
        )
    return await session.redirect(
        get.projects.view, project_name=str(project_name), error=f"Category '{category_to_add}' already exists."
    )


async def _process_add_language(
    session: web.Committer, add_language_form: shared.projects.AddLanguageForm
) -> web.WerkzeugResponse:
    project_name = add_language_form.project_name
    language_to_add = add_language_form.language_to_add.strip()

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        async with db.session() as data:
            project = await data.project(name=str(project_name)).demand(
                base.ASFQuartException(f"Project {project_name} not found", errorcode=404)
            )
        modified = await _metadata_language_add(wacm, project, language_to_add)

    if modified:
        return await session.redirect(
            get.projects.view, project_name=str(project_name), success=f"Language '{language_to_add}' added."
        )
    return await session.redirect(
        get.projects.view, project_name=str(project_name), error=f"Language '{language_to_add}' already exists."
    )


async def _process_compose_form(
    session: web.Committer, compose_form: shared.projects.ComposePolicyForm
) -> web.WerkzeugResponse:
    project_name = compose_form.project_name

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        try:
            await wacm.policy.edit_compose(compose_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view, project_name=project_name, error=f"Error editing compose policy: {e}"
            )

    return await session.redirect(
        get.projects.view, project_name=project_name, success="Compose options saved successfully."
    )


async def _process_delete_project(
    session: web.Committer, delete_form: shared.projects.DeleteProjectForm
) -> web.WerkzeugResponse:
    project_name = delete_form.project_name

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        try:
            await wacm.project.delete(project_name)
        except storage.AccessError as e:
            return await session.redirect(get.projects.projects, error=f"Error deleting project: {e}")

    return await session.redirect(get.projects.projects, success=f"Project '{project_name}' deleted successfully.")


async def _process_finish_form(
    session: web.Committer, finish_form: shared.projects.FinishPolicyForm
) -> web.WerkzeugResponse:
    project_name = finish_form.project_name

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        try:
            await wacm.policy.edit_finish(finish_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view, project_name=project_name, error=f"Error editing finish policy: {e}"
            )

    return await session.redirect(
        get.projects.view, project_name=project_name, success="Finish options saved successfully."
    )


async def _process_remove_category(
    session: web.Committer, remove_form: shared.projects.RemoveCategoryForm
) -> web.WerkzeugResponse:
    project_name = remove_form.project_name
    category_to_remove = remove_form.category_to_remove

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        async with db.session() as data:
            project = await data.project(name=str(project_name)).demand(
                base.ASFQuartException(f"Project {project_name} not found", errorcode=404)
            )
        modified = await _metadata_category_remove(wacm, project, category_to_remove)

    if modified:
        return await session.redirect(
            get.projects.view, project_name=str(project_name), success=f"Category '{category_to_remove}' removed."
        )
    return await session.redirect(
        get.projects.view, project_name=str(project_name), error=f"Category '{category_to_remove}' does not exist."
    )


async def _process_remove_language(
    session: web.Committer, remove_form: shared.projects.RemoveLanguageForm
) -> web.WerkzeugResponse:
    project_name = remove_form.project_name
    language_to_remove = remove_form.language_to_remove

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        async with db.session() as data:
            project = await data.project(name=str(project_name)).demand(
                base.ASFQuartException(f"Project {project_name} not found", errorcode=404)
            )
        modified = await _metadata_language_remove(wacm, project, language_to_remove)

    if modified:
        return await session.redirect(
            get.projects.view, project_name=project_name, success=f"Language '{language_to_remove}' removed."
        )
    return await session.redirect(
        get.projects.view, project_name=project_name, error=f"Language '{language_to_remove}' does not exist."
    )


async def _process_vote_form(session: web.Committer, vote_form: shared.projects.VotePolicyForm) -> web.WerkzeugResponse:
    project_name = vote_form.project_name

    async with storage.write(session) as write:
        wacm = await write.as_project_committee_member(project_name)
        try:
            await wacm.policy.edit_vote(vote_form)
        except storage.AccessError as e:
            return await session.redirect(
                get.projects.view, project_name=project_name, error=f"Error editing vote policy: {e}"
            )

    return await session.redirect(
        get.projects.view, project_name=project_name, success="Vote options saved successfully."
    )
