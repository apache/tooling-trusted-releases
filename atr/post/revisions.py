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

import atr.blueprints.post as post
import atr.db as db
import atr.get as get
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@post.typed
async def selected_post(
    session: web.Committer,
    _revisions: Literal["revisions"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_form: shared.revisions.RevisionForm,
) -> web.WerkzeugResponse:
    """
    URL: /revisions/<project_key>/<version_key>
    """
    match revision_form:
        case shared.revisions.SetRevisionForm():
            return await _set_revision(session, revision_form, project_key, version_key)
        case shared.revisions.SetTagForm():
            return await _set_tag(session, revision_form, project_key, version_key)


async def _set_revision(
    session: web.Committer,
    set_revision_form: shared.revisions.SetRevisionForm,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse:
    """Set a specific revision as the latest for a candidate draft."""
    selected_revision_number = set_revision_form.revision_number

    async with db.session() as data:
        release = await session.release(project_key, version_key, phase=None, data=data)
        if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            raise base.ASFQuartException("Cannot set revision for non-draft release", errorcode=400)

        await data.revision(release_key=release.key, number=str(selected_revision_number)).demand(
            base.ASFQuartException(f"Revision {selected_revision_number} not found", errorcode=404)
        )

    description = f"Copy of revision {selected_revision_number} through web interface"
    async with storage.write(session) as write:
        wacp = await write.as_project_committee_participant(project_key)
        result = await wacp.revision.create_revision_with_quarantine(
            project_key,
            version_key,
            session.uid,
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            description=description,
            clone_from=selected_revision_number,
        )
        if isinstance(result, sql.Quarantined):
            success = f"Revision copy from {selected_revision_number} received. Archive validation in progress."
        else:
            success = f"Copied revision {selected_revision_number} to new latest revision, {result.number}"
        return await session.redirect(
            get.revisions.selected,
            success=success,
            project_key=str(project_key),
            version_key=str(version_key),
        )


async def _set_tag(
    session: web.Committer,
    set_tag_form: shared.revisions.SetTagForm,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse:
    """Set a tag on a specific revision."""
    revision_number = set_tag_form.revision_number
    tag = set_tag_form.tag

    try:
        async with storage.write(session) as write:
            wacp = await write.as_project_committee_participant(project_key)
            await wacp.revision.set_tag(project_key, version_key, revision_number, tag)
    except storage.AccessError as e:
        return await session.redirect(
            get.revisions.selected,
            error=str(e),
            project_key=str(project_key),
            version_key=str(version_key),
        )

    return await session.redirect(
        get.revisions.selected,
        success=f"Tag '{tag}' set for revision {revision_number}",
        project_key=str(project_key),
        version_key=str(version_key),
    )
