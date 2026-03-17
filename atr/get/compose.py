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

import atr.blueprints.get as get
import atr.db as db
import atr.mapping as mapping
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.web as web


@get.typed
async def selected(
    session: web.Committer,
    _compose: Literal["compose"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse | str:
    """
    URL: /compose/<project_key>/<version_key>
    Show the contents of the release candidate draft.
    """
    await session.check_access(project_key)
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            _committee=True,
            _project_release_policy=True,
        ).demand(base.ASFQuartException("Release does not exist", errorcode=404))
    if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        return await mapping.release_as_redirect(session, release)
    return await shared.web.check(session, release)
