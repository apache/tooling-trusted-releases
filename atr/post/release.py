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
import atr.form as form
import atr.get as get
import atr.mapping as mapping
import atr.models.safe as safe
import atr.storage as storage
import atr.web as web


@post.typed
async def activity(
    session: web.Committer,
    _release: Literal["release"],
    _activity: Literal["activity"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    """
    URL: /release/activity/<project_key>/<version_key>
    """
    return await _bump_activity(session, project_key, version_key)


async def _bump_activity(
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse:
    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            release = await wacp.release.bump_activity(project_key, version_key)
    except storage.AccessError as e:
        return await session.redirect(get.root.index, error=str(e))
    await quart.flash("Inactivity clock reset", "success")
    return await mapping.release_as_redirect(session, release)
