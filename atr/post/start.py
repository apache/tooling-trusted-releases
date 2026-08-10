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

import asfquart.base as base
import quart

import atr.blueprints.post as post
import atr.db as db
import atr.get as get
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@post.typed
async def metadata(
    session: web.Committer,
    _start: Literal["start"],
    project_key: safe.ProjectKey,
    _metadata: Literal["metadata"],
    edit_metadata_form: shared.projects.EditMetadataForm,
) -> web.WerkzeugResponse:
    """
    URL: /start/<project_key>/metadata

    Save the metadata submitted from the start page, then return there so the
    release can be started.
    """
    if str(edit_metadata_form.project_key) != str(project_key):
        raise ValueError(f"Project key mismatch: {edit_metadata_form.project_key} != {project_key}")

    try:
        async with storage.write(session) as write:
            warm = await write.as_project_release_manager(project_key)
            await warm.project.edit_metadata(edit_metadata_form)
    except (web.FlashError, base.ASFQuartException, storage.AccessError) as e:
        await quart.flash(str(e), "error")
        return await session.redirect(get.start.selected, project_key=str(project_key))

    return await session.redirect(get.start.selected, project_key=str(project_key), success="Metadata saved.")


@post.typed
async def selected(
    session: web.Committer,
    _start: Literal["start"],
    project_key: safe.ProjectKey,
    start_release_form: shared.start.StartReleaseForm,
) -> web.WerkzeugResponse:
    """
    URL: /start/<project_key>
    """

    async with db.session() as data:
        project = await data.project(key=str(project_key), _committee=True).demand(
            base.ASFQuartException(f"Project {project_key} not found", errorcode=404)
        )
    if shared.start.missing_release_metadata(project):
        return await session.redirect(get.start.selected, project_key=str(project_key))

    try:
        version = safe.VersionKey(start_release_form.version_key)
        async with storage.write(session) as write:
            if start_release_form.expedited:
                wacm = await write.as_project_committee_member(project_key)
                new_release, _project = await wacm.release.start_expedited(
                    project_key,
                    version,
                    start_release_form.auto_archive_prior,
                    download_path_suffix=start_release_form.download_path_suffix,
                )
            else:
                wacp = await write.as_project_committee_participant(project_key)
                new_release, _project = await wacp.release.start(
                    project_key,
                    version,
                    start_release_form.auto_archive_prior,
                    download_path_suffix=start_release_form.download_path_suffix,
                )

        return await session.redirect(
            get.compose.selected,
            project_key=str(project_key),
            version_key=new_release.version,
            success="Release candidate draft created successfully",
        )
    except (web.FlashError, base.ASFQuartException, storage.AccessError) as e:
        await quart.flash(str(e), "error")
        return await session.redirect(get.start.selected, project_key=str(project_key))
