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
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@post.typed
async def selected(
    session: web.Committer,
    _finish: Literal["finish"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    publish_form: shared.finish.PublishToSvnForm,
) -> tuple[web.QuartResponse, int] | web.WerkzeugResponse:
    """
    URL: /finish/<project_key>/<version_key>
    """
    respond = _respond_helper(session, project_key, version_key)
    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            await wacp.release.publish_to_svn(
                project_key, version_key, publish_form.revision_number, publish_form.download_path_suffix
            )
    except storage.AccessError as e:
        return await respond(e.status or 409, str(e))
    return await respond(200, "SVN publish task queued.")


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
