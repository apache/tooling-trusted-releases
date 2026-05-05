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

import quart

import atr.blueprints.post as post
import atr.errors as errors
import atr.log as log
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


@post.typed
async def selected(
    session: web.Committer,
    _finish: Literal["finish"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    finish_form: shared.finish.FinishForm,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    """
    URL: /finish/<project_key>/<version_key>
    """
    respond = _respond_helper(session, project_key, version_key)

    match finish_form:
        case shared.finish.DeleteEmptyDirectoryForm() as delete_form:
            return await _delete_empty_directory(delete_form, session, project_key, version_key, respond)
        case shared.finish.RemoveRCTagsForm():
            return await _remove_rc_tags(session, project_key, version_key, respond)


async def _delete_empty_directory(
    delete_form: shared.finish.DeleteEmptyDirectoryForm,
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    respond: shared.finish.Respond,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    dir_to_delete_rel = delete_form.directory_to_delete
    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_member(project_key)
            creation_error = await wacp.release.delete_empty_directory(
                project_key, version_key, dir_to_delete_rel.as_path()
            )
    except Exception as e:
        log.exception(f"Unexpected error deleting directory {dir_to_delete_rel} for {project_key}/{version_key}")
        return await _server_error(respond, e, "An unexpected error occurred.")

    if creation_error is not None:
        return await respond(400, creation_error)
    return await respond(200, f"Deleted empty directory '{dir_to_delete_rel}'.")


async def _remove_rc_tags(
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    respond: shared.finish.Respond,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_member(project_key)
            creation_error, renamed_count, error_messages = await wacp.release.remove_rc_tags(project_key, version_key)

        if creation_error is not None:
            return await respond(409, creation_error)

        if error_messages:
            status_ok = renamed_count > 0
            # TODO: Ideally HTTP would have a general mixed status, like 207 but for anything
            http_status = 200 if status_ok else 500
            msg = f"RC tags removed for {util.plural(renamed_count, 'item')}"
            msg += f" with some errors: {'; '.join(error_messages)}"
            return await respond(http_status, msg)

        if renamed_count > 0:
            return await respond(200, f"Successfully removed RC tags from {util.plural(renamed_count, 'item')}.")

        return await respond(200, "No items required RC tag removal or no changes were made.")

    except Exception as e:
        log.exception(f"Unexpected error removing RC tags for {project_key}/{version_key}")
        return await _server_error(respond, e, f"Unexpected error: {e!s}")


def _respond_helper(
    session: web.Committer, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> shared.finish.Respond:
    """Create a response helper function for the finish route."""
    import atr.get as get

    async def respond(
        http_status: int,
        msg: str,
    ) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
        ok = http_status < 300
        if web.wants_json_response():
            return quart.jsonify(ok=ok, message=msg), http_status
        await quart.flash(msg, "success" if ok else "error")
        return await session.redirect(get.finish.selected, project_key=str(project_key), version_key=str(version_key))

    return respond


async def _server_error(
    respond: shared.finish.Respond,
    error: BaseException,
    summary: str,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    if web.wants_json_response():
        return errors.action_error_response(error, summary=summary, status=500)
    return await respond(500, summary)
