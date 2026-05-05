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
import atr.db as db
import atr.errors as errors
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


@post.typed
async def selected(
    session: web.Committer,
    _upload: Literal["upload"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    upload_form: shared.upload.UploadForm,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    """
    URL: /upload/<project_key>/<version_key>
    """
    wants_json = quart.request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json"

    match upload_form:
        case shared.upload.AddFilesForm() as add_form:
            return await _add_files(session, add_form, project_key, version_key, wants_json=wants_json)

        case shared.upload.SvnImportForm() as svn_form:
            return await _svn_import(session, svn_form, project_key, version_key)


async def _add_files(
    session: web.Committer,
    add_form: shared.upload.AddFilesForm,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    *,
    wants_json: bool,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    try:
        file_data = add_form.file_data

        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            creation_error, number_of_files, was_quarantined = await wacp.release.upload_files(
                project_key, version_key, file_data
            )

        if creation_error is not None:
            if wants_json:
                return quart.jsonify(ok=False, message=creation_error), 400
            await quart.flash(creation_error, "error")
            return await session.redirect(
                get.upload.selected,
                project_key=project_key,
                version_key=version_key,
            )

        next_url = util.as_url(
            get.compose.selected,
            project_key=str(project_key),
            version_key=str(version_key),
        )

        if was_quarantined:
            if wants_json:
                return quart.jsonify(
                    ok=True,
                    message="Upload received. Archive validation in progress.",
                    next_url=next_url,
                ), 202
            return await session.redirect(
                get.compose.selected,
                success="Upload received. Archive validation in progress.",
                project_key=project_key,
                version_key=version_key,
            )

        if wants_json:
            return quart.jsonify(
                ok=True,
                message=f"{util.plural(number_of_files, 'file')} added successfully",
                next_url=next_url,
            ), 200
        return await session.redirect(
            get.compose.selected,
            success=f"{util.plural(number_of_files, 'file')} added successfully",
            project_key=project_key,
            version_key=version_key,
        )
    except storage.AccessError as e:
        status = errors.response_status_code(e)
        _log_access_error(e, status)
        if wants_json:
            return errors.action_error_response(e, status=status)
        await quart.flash(str(e), "error")
        return await session.redirect(
            get.upload.selected,
            project_key=project_key,
            version_key=version_key,
        )
    except Exception as e:
        log.exception("Error adding file:")
        if wants_json:
            return errors.action_error_response(e, summary=f"Error adding file: {e!s}", status=500)
        await quart.flash(f"Error adding file: {e!s}", "error")
        return await session.redirect(
            get.upload.selected,
            project_key=project_key,
            version_key=version_key,
        )


def _construct_svn_url(
    committee_key: str, area: shared.upload.SvnArea, path: safe.RelPath, *, is_podling: bool
) -> safe.RelPath:
    if is_podling:
        return path.prepend(f"{area.value}/incubator/{committee_key}")
    return path.prepend(f"{area.value}/{committee_key}")


def _log_access_error(error: storage.AccessError, status: int) -> None:
    if status >= 500:
        log.error("Upload storage access server error", exc_info=(type(error), error, error.__traceback__))
        return
    log.warning(f"Upload access error: {error!s}")


async def _svn_import(
    session: web.Committer,
    svn_form: shared.upload.SvnImportForm,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse:
    # audit_guidance any file uploads are from known and managed repositories so file size is not an issue
    try:
        target_subdirectory = svn_form.target_subdirectory if svn_form.target_subdirectory else None
        svn_area = shared.upload.SvnArea.DEV
        svn_path = svn_form.svn_path

        async with db.session() as data:
            release = await session.release(project_key, version_key, data=data)
            is_podling = (release.project.committee is not None) and release.project.committee.is_podling
            committee_key = release.project.committee_key or str(project_key)

        svn_url = _construct_svn_url(
            committee_key,
            svn_area,  # pyright: ignore[reportArgumentType]
            svn_path,
            is_podling=is_podling,
        )
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            await wacp.release.import_from_svn(
                project_key,
                version_key,
                svn_url,
                svn_form.revision,
                target_subdirectory,
            )

        return await session.redirect(
            get.compose.selected,
            success="SVN import task queued successfully",
            project_key=project_key,
            version_key=version_key,
        )
    except Exception:
        log.exception("Error queueing SVN import task:")
        return await session.redirect(
            get.upload.selected,
            error="Error queueing SVN import task",
            project_key=project_key,
            version_key=version_key,
        )
