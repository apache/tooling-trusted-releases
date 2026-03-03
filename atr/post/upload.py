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

import asyncio
import json
import pathlib
from typing import Final, Literal

import aiofiles
import aiofiles.os
import aioshutil
import quart
import werkzeug.wrappers.response as response

import atr.blueprints.post as post
import atr.db as db
import atr.form as form
import atr.get as get
import atr.log as log
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.shared as shared
import atr.storage as storage
import atr.storage.types as types
import atr.util as util
import atr.web as web

_SVN_BASE_URL: Final[str] = "https://dist.apache.org/repos/dist"


@post.typed
async def finalise(
    session: web.Committer,
    _upload_finalise: Literal["upload/finalise"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    upload_session: str,
) -> web.WerkzeugResponse:
    """
    URL: /upload/finalise/<project_name>/<version_name>/<upload_session>
    """

    try:
        staging_dir = paths.get_upload_staging_dir(upload_session)
    except ValueError:
        return _json_error("Invalid session token", 400)

    if not await aiofiles.os.path.isdir(staging_dir):
        return _json_error("No staged files found", 400)

    try:
        staged_files = await aiofiles.os.listdir(staging_dir)
    except OSError:
        return _json_error("Error reading staging directory", 500)

    staged_files = [f for f in staged_files if f not in (".", "..")]
    if not staged_files:
        return _json_error("No staged files found", 400)

    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(str(project_name))
            number_of_files = len(staged_files)
            description = f"Upload of {util.plural(number_of_files, 'file')} through web interface"

            async def modify(path: pathlib.Path, _old_rev: sql.Revision | None) -> None:
                for filename in staged_files:
                    src = staging_dir / filename
                    dst = path / filename
                    await aioshutil.move(str(src), str(dst))

            await wacp.revision.create_revision(
                str(project_name), str(version_name), session.uid, description=description, modify=modify
            )

        await aioshutil.rmtree(staging_dir)

        return await session.redirect(
            get.compose.selected,
            success=f"{util.plural(number_of_files, 'file')} added successfully",
            project_name=str(project_name),
            version_name=str(version_name),
        )
    except types.FailedError as e:
        await aioshutil.rmtree(staging_dir)
        await quart.flash(str(e), "error")
        return await session.redirect(
            get.upload.selected,
            project_name=str(project_name),
            version_name=str(version_name),
        )
    except Exception as e:
        log.exception("Error finalising upload:")
        return _json_error(f"Error finalising upload: {e!s}", 500)


@post.typed
async def selected(
    session: web.Committer,
    _upload: Literal["upload"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    upload_form: shared.upload.UploadForm,
) -> web.WerkzeugResponse:
    """
    URL: /upload/<project_name>/<version_name>
    """

    match upload_form:
        case shared.upload.AddFilesForm() as add_form:
            return await _add_files(session, add_form, str(project_name), str(version_name))

        case shared.upload.SvnImportForm() as svn_form:
            return await _svn_import(session, svn_form, str(project_name), str(version_name))


@post.typed
async def stage(
    _session: web.Committer,
    _upload_stage: Literal["upload/stage"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    upload_session: str,
) -> web.WerkzeugResponse:
    """
    URL: /upload/stage/<project_name>/<version_name>/<upload_session>
    """

    try:
        staging_dir = paths.get_upload_staging_dir(upload_session)
    except ValueError:
        return _json_error("Invalid session token", 400)

    files = await quart.request.files
    file = files.get("file")
    if (not file) or (not file.filename):
        return _json_error("No file provided", 400)

    # Extract basename and validate
    basename = pathlib.Path(file.filename).name
    validated_filename = form.to_filename(basename)
    if validated_filename is None:
        return _json_error("Invalid filename", 400)
    filename = str(validated_filename)

    await aiofiles.os.makedirs(staging_dir, exist_ok=True)

    target_path = staging_dir / filename
    if await aiofiles.os.path.exists(target_path):
        return _json_error("File already exists in staging", 409)

    try:
        async with aiofiles.open(target_path, "wb") as f:
            # 1 MiB chunks
            while chunk := await asyncio.to_thread(file.stream.read, 1024 * 1024):
                await f.write(chunk)
    except Exception as e:
        log.exception("Error staging file:")
        if await aiofiles.os.path.exists(target_path):
            await aiofiles.os.remove(target_path)
        return _json_error(f"Error staging file: {e!s}", 500)

    return _json_success({"status": "staged", "filename": filename})


async def _add_files(
    session: web.Committer, add_form: shared.upload.AddFilesForm, project_name: str, version_name: str
) -> web.WerkzeugResponse:
    try:
        file_name = add_form.file_name
        file_data = add_form.file_data

        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_name)
            creation_error, number_of_files = await wacp.release.upload_files(
                project_name, version_name, file_name, file_data
            )

        if creation_error is not None:
            await quart.flash(creation_error, "error")
            return await session.redirect(
                get.upload.selected,
                project_name=project_name,
                version_name=version_name,
            )

        return await session.redirect(
            get.compose.selected,
            success=f"{util.plural(number_of_files, 'file')} added successfully",
            project_name=project_name,
            version_name=version_name,
        )
    except Exception as e:
        log.exception("Error adding file:")
        await quart.flash(f"Error adding file: {e!s}", "error")
        return await session.redirect(
            get.upload.selected,
            project_name=project_name,
            version_name=version_name,
        )


def _construct_svn_url(committee_name: str, area: shared.upload.SvnArea, path: str, *, is_podling: bool) -> str:
    if is_podling:
        return f"{_SVN_BASE_URL}/{area.value}/incubator/{committee_name}/{path}"
    return f"{_SVN_BASE_URL}/{area.value}/{committee_name}/{path}"


def _json_error(message: str, status: int) -> web.WerkzeugResponse:
    return response.Response(json.dumps({"error": message}), status=status, mimetype="application/json")


def _json_success(data: dict[str, str], status: int = 200) -> web.WerkzeugResponse:
    return response.Response(json.dumps(data), status=status, mimetype="application/json")


async def _svn_import(
    session: web.Committer, svn_form: shared.upload.SvnImportForm, project_name: str, version_name: str
) -> web.WerkzeugResponse:
    # audit_guidance any file uploads are from known and managed repositories so file size is not an issue
    try:
        target_subdirectory = str(svn_form.target_subdirectory) if svn_form.target_subdirectory else None
        svn_area = shared.upload.SvnArea.DEV
        svn_path = svn_form.svn_path or ""

        async with db.session() as data:
            release = await session.release(project_name, version_name, data=data)
            is_podling = (release.project.committee is not None) and release.project.committee.is_podling
            committee_name = release.project.committee_name or project_name

        svn_url = _construct_svn_url(
            committee_name,
            svn_area,  # pyright: ignore[reportArgumentType]
            svn_path,
            is_podling=is_podling,
        )
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_name)
            await wacp.release.import_from_svn(
                project_name,
                version_name,
                svn_url,
                svn_form.revision,
                target_subdirectory,
            )

        return await session.redirect(
            get.compose.selected,
            success="SVN import task queued successfully",
            project_name=project_name,
            version_name=version_name,
        )
    except Exception:
        log.exception("Error queueing SVN import task:")
        return await session.redirect(
            get.upload.selected,
            error="Error queueing SVN import task",
            project_name=project_name,
            version_name=version_name,
        )
