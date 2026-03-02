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


from typing import Literal

import atr.blueprints.post as post
import atr.get as get
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@post.typed
async def ignores(
    session: web.Committer,
    _ignores: Literal["ignores"],
    project_name: safe.ProjectName,
    ignore_form: shared.ignores.IgnoreForm,
) -> web.WerkzeugResponse:
    """
    URL: /ignores/<project_name>
    Handle forms on the ignores page.
    """
    await session.check_access(project_name)
    match ignore_form:
        case shared.ignores.AddIgnoreForm() as add_form:
            return await _add_ignore(session, add_form, project_name)

        case shared.ignores.DeleteIgnoreForm() as delete_form:
            return await _delete_ignore(session, delete_form, project_name)

        case shared.ignores.UpdateIgnoreForm() as update_form:
            return await _update_ignore(session, update_form, project_name)


async def _add_ignore(
    session: web.Committer, add_form: shared.ignores.AddIgnoreForm, project_name: safe.ProjectName
) -> web.WerkzeugResponse:
    """Add a new ignore."""
    status = shared.ignores.ignore_status_to_sql(add_form.status)  # pyright: ignore[reportArgumentType]

    async with storage.write() as write:
        wacm = await write.as_project_committee_member(project_name)
        await wacm.checks.ignore_add(
            project_name=project_name,
            release_glob=add_form.release_glob or None,
            revision_number=add_form.revision_number or None,
            checker_glob=add_form.checker_glob or None,
            primary_rel_path_glob=add_form.primary_rel_path_glob or None,
            member_rel_path_glob=add_form.member_rel_path_glob or None,
            status=status,
            message_glob=add_form.message_glob or None,
        )

    return await session.redirect(
        get.ignores.ignores,
        project_name=str(project_name),
        success="Ignore added",
    )


async def _delete_ignore(
    session: web.Committer, delete_form: shared.ignores.DeleteIgnoreForm, project_name: safe.ProjectName
) -> web.WerkzeugResponse:
    """Delete an ignore."""
    async with storage.write() as write:
        wacm = await write.as_project_committee_member(project_name)
        await wacm.checks.ignore_delete(id=delete_form.id)

    return await session.redirect(
        get.ignores.ignores,
        project_name=str(project_name),
        success="Ignore deleted",
    )


async def _update_ignore(
    session: web.Committer, update_form: shared.ignores.UpdateIgnoreForm, project_name: safe.ProjectName
) -> web.WerkzeugResponse:
    """Update an ignore."""
    status = shared.ignores.ignore_status_to_sql(update_form.status)  # pyright: ignore[reportArgumentType]

    async with storage.write() as write:
        wacm = await write.as_project_committee_member(project_name)
        await wacm.checks.ignore_update(
            id=update_form.id,
            release_glob=update_form.release_glob or None,
            revision_number=update_form.revision_number or None,
            checker_glob=update_form.checker_glob or None,
            primary_rel_path_glob=update_form.primary_rel_path_glob or None,
            member_rel_path_glob=update_form.member_rel_path_glob or None,
            status=status,
            message_glob=update_form.message_glob or None,
        )

    return await session.redirect(
        get.ignores.ignores,
        project_name=str(project_name),
        success="Ignore updated",
    )
