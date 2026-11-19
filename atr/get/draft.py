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

import datetime
from typing import Literal

import aiofiles.os

import atr.attestable as attestable
import atr.blueprints.get as get
import atr.classify as classify
import atr.errors as errors
import atr.form as form
import atr.models.attestable
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.post as post
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def tools(
    session: web.Committer,
    _draft_tools: Literal["draft/tools"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
) -> str:
    """
    URL: /draft/tools/<project_key>/<version_key>/<path:file_path>
    Show the tools for a specific file.
    """
    await session.prevent_confusing_ui_display(project_key)
    validated_path = file_path.as_path()

    release = await session.release(project_key, version_key)
    full_path = str(paths.release_directory(release) / validated_path)

    # Check that the file exists
    if not await aiofiles.os.path.exists(full_path):
        raise errors.ASFQuartException(errors.FILE_NOT_FOUND, errorcode=404)

    modified = int(await aiofiles.os.path.getmtime(full_path))
    file_size = await aiofiles.os.path.getsize(full_path)

    file_data = {
        "filename": validated_path.name,
        "bytes_size": file_size,
        "uploaded": datetime.datetime.fromtimestamp(modified, tz=datetime.UTC),
    }

    hashgen_action = util.as_url(
        post.draft.hashgen,
        project_key=str(project_key),
        version_key=str(version_key),
        file_path=str(file_path),
    )
    sha512_form = await form.render(
        model_cls=shared.draft.HashGen,
        action=hashgen_action,
        submit_label="Generate SHA512",
        submit_classes="btn-outline-secondary",
        empty=True,
    )
    entry = await _path_entry(release, file_path)
    declared = entry.source if (entry is not None) else None
    source_form = None
    candidates = sorted(release.project.github_repositories)
    if (len(candidates) > 1) and (entry is not None) and (entry.classification == classify.FileType.SOURCE.value):
        source_form = await _render_source_form(
            project_key, version_key, file_path, candidates, declared, release.safe_latest_revision_number
        )
    return await template.render(
        "draft-tools.html",
        asf_id=session.uid,
        project_key=str(project_key),
        version_key=str(version_key),
        file_path=str(file_path),
        file_data=file_data,
        release=release,
        format_file_size=util.format_file_size,
        sha512_form=sha512_form,
        source_form=source_form,
        source_declared=declared,
    )


async def _path_entry(release: sql.Release, file_path: safe.RelPath) -> atr.models.attestable.PathEntryV2 | None:
    if release.latest_revision_number is None:
        return None
    recorded = await attestable.load(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    )
    if not isinstance(recorded, atr.models.attestable.AttestableV2):
        return None
    return recorded.paths.get(str(file_path))


async def _render_source_form(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    file_path: safe.RelPath,
    candidates: list[str],
    declared: atr.models.attestable.ArchiveSourceV2 | None,
    revision_number: safe.RevisionNumber,
) -> str:
    # The select can't be told which option is current, so that one goes first
    if (declared is not None) and (declared.repository in candidates):
        candidates = [declared.repository, *(c for c in candidates if (c != declared.repository))]
    rendered = await form.render(
        model_cls=shared.draft.ArchiveSourceForm,
        action=util.as_url(
            post.draft.archive_source,
            project_key=str(project_key),
            version_key=str(version_key),
            file_path=str(file_path),
        ),
        submit_label="Declare source",
        submit_classes="btn-outline-primary",
        defaults={
            "repository": [(candidate, candidate) for candidate in candidates],
            "commit": declared.commit if (declared is not None) else "",
            "rendered_revision": revision_number,
        },
    )
    return str(rendered)
