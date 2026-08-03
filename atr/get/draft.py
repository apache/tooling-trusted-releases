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
import asfquart.base as base

import atr.blueprints.get as get
import atr.form as form
import atr.models.safe as safe
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
        raise base.ASFQuartException("File does not exist", errorcode=404)

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
    )
