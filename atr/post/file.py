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

import atr.blueprints.post as blueprints_post
import atr.get as get
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@blueprints_post.typed
async def post(
    session: web.Committer,
    _file: Literal["file"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _archival_form: shared.projects.ConfirmReleaseArchival,
) -> web.WerkzeugResponse:
    """
    URL: /file/<project_key>/<version_key>
    """
    # The writer checks the release can be archived under the write lock
    async with storage.write(session) as write:
        try:
            wacm = await write.as_project_committee_member(project_key)
            await wacm.release.archive(project_key, version_key)
        except storage.AccessError as e:
            return await _redirect_to_release(session, project_key, version_key, error=f"Error archiving: {e}")

    return await _redirect_to_release(
        session, project_key, version_key, success=f"Release {project_key} {version_key} archived."
    )


async def _redirect_to_release(
    session: web.Committer,
    project_key: safe.ProjectKey,
    release_version: safe.VersionKey,
    **kwargs: str,
) -> web.WerkzeugResponse:
    return await session.redirect(
        get.file.selected, project_key=str(project_key), version_key=str(release_version), **kwargs
    )
