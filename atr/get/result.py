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

import json
from typing import Literal

import asfquart.base as base

import atr.blueprints.get as get
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.web as web


@get.typed
async def data(
    session: web.Committer,
    _result_data: Literal["result/data"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    check_id: int,
) -> web.TextResponse:
    """
    URL: /result/data/<project_name>/<version_name>/<check_id>
    Show a check result as formatted JSON.
    """
    await session.check_access(project_name)
    async with db.session() as data:
        release = await data.release(
            project_name=str(project_name),
            version=str(version_name),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            _committee=True,
        ).get()

        if release is None:
            release = await session.release(str(project_name), str(version_name), with_committee=True)

        if release.committee is None:
            raise base.ASFQuartException("Release has no committee", errorcode=500)
        if release.latest_revision_number is None:
            raise base.ASFQuartException("Release has no revision", errorcode=500)

        check_result = await data.check_result(
            id=check_id,
        ).demand(base.ASFQuartException("Check result not found", errorcode=404))

    payload = check_result.model_dump(mode="json", exclude={"release"})
    body = json.dumps(payload, indent=2, sort_keys=True)
    # audit_guidance TextResponse is being used intentionally because browsers display JSON well
    return web.TextResponse(f"{body}\n")
