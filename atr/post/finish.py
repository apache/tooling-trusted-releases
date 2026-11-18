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
import atr.form as form
import atr.get as get
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.shared as shared
import atr.storage as storage
import atr.util as util
import atr.web as web


@post.typed
async def download_page(
    session: web.Committer,
    _finish: Literal["finish"],
    _download_page: Literal["download-page"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    _form: form.Empty,
) -> web.WerkzeugResponse:
    await session.prevent_confusing_ui_display(project_key, flash_admin_warning=False)
    release = await session.release(project_key, version_key, phase=sql.ReleasePhase.RELEASE_PREVIEW)
    check = await _download_page_check(release)
    quart.session[f"{shared.finish.DOWNLOAD_PAGE_CHECK_SESSION_KEY}:{release.key}"] = str(check)
    return await session.redirect(get.finish.selected, project_key=str(project_key), version_key=str(version_key))


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
            warm = await write.as_project_release_manager(project_key)
            await warm.release.publish_to_svn(
                project_key, version_key, publish_form.revision_number, publish_form.download_path_suffix
            )
    except storage.AccessError as e:
        return await respond(e.status or 409, str(e))
    return await respond(200, "SVN publish task queued.")


async def _download_page_check(release: sql.Release) -> htm.Element:
    page_url = release.project.download_page
    if not page_url:
        metadata_url = util.as_url(get.projects.view, project_key=release.project.key) + "?tab=metadata"
        return htm.div(".alert.alert-warning", role="alert")[
            "This project has no download page URL to check. ",
            htm.a(href=metadata_url)["Add it to the project metadata"],
            ".",
        ]
    if release.committee is None:
        raise ValueError(errors.RELEASE_NO_COMMITTEE)
    async with db.session() as data:
        artifacts = await data.artifact(release_key=release.key, managed=True, _unannounced=True).all()
    if not artifacts:
        return htm.div(".alert.alert-warning", role="alert")[
            "ATR has no record of the files published for this release, so it cannot check the download page yet."
        ]
    try:
        final_url, page = await util.download_page_fetch(page_url)
    except util.FetchError as e:
        return htm.div(".alert.alert-warning", role="alert")[str(e)]
    committee_dir = str(paths.committee_dist_relpath(release.committee))
    problems = util.download_page_problems(final_url, page, committee_dir, artifacts)
    link = htm.a(href=final_url)[final_url]
    if not problems:
        return htm.div(".alert.alert-success", role="alert")[
            "The download page at ", link, " links to this release correctly."
        ]
    return htm.div(".alert.alert-warning", role="alert")[
        htm.p(".mb-1")["The download page at ", link, " has these problems:"],
        htm.ul(".mb-0")[(htm.li[problem] for problem in problems)],
    ]


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
