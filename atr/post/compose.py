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
import atr.web as web


@post.typed
async def selected(
    session: web.Committer,
    _compose: Literal["compose"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    move_form: shared.compose.MoveFileForm,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    """
    URL: /compose/<project_key>/<version_key>
    """
    respond = _respond_helper(session, project_key, version_key)

    return await _move_file_to_revision(move_form, session, project_key, version_key, respond)


async def _move_file_to_revision(
    move_form: shared.compose.MoveFileForm,
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    respond: shared.compose.Respond,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    source_files_rel = move_form.source_files
    target_dir_rel = move_form.target_directory
    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_member(project_key)
            creation_error, moved_files_names, skipped_files_names = await wacp.release.move_file(
                project_key, version_key, source_files_rel, target_dir_rel
            )

        if creation_error is not None:
            return await respond(409, creation_error)

        response_messages = []
        if moved_files_names:
            response_messages.append(f"Moved {', '.join(moved_files_names)}")
        if skipped_files_names:
            response_messages.append(f"Skipped {', '.join(skipped_files_names)} (already in target directory)")

        if not response_messages:
            if not source_files_rel:
                return await respond(400, "No source files specified for move.")
            msg = f"No files were moved. {', '.join(skipped_files_names)} already in '{target_dir_rel}'."
            return await respond(200, msg)

        return await respond(200, ". ".join(response_messages) + ".")

    except FileNotFoundError:
        log.exception("File not found during move operation in new revision")
        return await respond(400, "Error: Source file not found during move operation.")
    except OSError as e:
        log.exception("Error moving file in new revision")
        return await _server_error(respond, e, f"Error moving file: {e}")
    except Exception as e:
        log.exception("Unexpected error during file move")
        return await _server_error(respond, e, f"ERROR: {e!s}")


def _respond_helper(
    session: web.Committer, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> shared.compose.Respond:
    """Create a response helper function for the compose route."""
    import atr.get as get

    async def respond(
        http_status: int,
        msg: str,
    ) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
        ok = http_status < 300
        if web.wants_json_response():
            return quart.jsonify(ok=ok, message=msg), http_status
        await quart.flash(msg, "success" if ok else "error")
        return await session.redirect(get.compose.selected, project_key=str(project_key), version_key=str(version_key))

    return respond


async def _server_error(
    respond: shared.compose.Respond,
    error: BaseException,
    summary: str,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    if web.wants_json_response():
        return errors.action_error_response(error, summary=summary, status=500)
    return await respond(500, summary)
