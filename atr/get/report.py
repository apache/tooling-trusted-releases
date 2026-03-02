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

import datetime
from typing import Literal

import aiofiles.os
import asfquart.base as base

import atr.blueprints.get as get
import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.paths as paths
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected_path(
    session: web.Committer,
    _report: Literal["report"],
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    rel_path: unsafe.Path,
) -> str:
    """
    URL: /report/<project_name>/<version_name>/<rel_path>
    Show the report for a specific file.
    """
    # has_post = await session.has_post_access(project_name)
    # open page w/o a form
    validated_path = form.to_relpath(rel_path)
    if validated_path is None:
        raise base.ASFQuartException("Invalid file path", errorcode=400)

    # If the draft is not found, we try to get the release candidate
    try:
        release = await session.release(
            str(project_name),
            str(version_name),
            with_committee=True,
            with_release_policy=True,
            with_project_release_policy=True,
        )
    except base.ASFQuartException:
        release = await session.release(
            str(project_name),
            str(version_name),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE,
            with_committee=True,
            with_release_policy=True,
            with_project_release_policy=True,
        )

    if release.committee is None:
        raise base.ASFQuartException("Release has no committee", errorcode=500)

    # TODO: When we do more than one thing in a dir, we should use the revision directory directly
    abs_path = paths.release_directory(release) / validated_path
    if release.latest_revision_number is None:
        raise base.ASFQuartException("Release has no revision", errorcode=500)

    # Check that the file exists
    if not await aiofiles.os.path.exists(abs_path):
        raise base.ASFQuartException("File does not exist", errorcode=404)

    modified = int(await aiofiles.os.path.getmtime(abs_path))
    file_size = await aiofiles.os.path.getsize(abs_path)

    # Get all check results for this file
    async with storage.read() as read:
        ragp = read.as_general_public()
        check_results = await ragp.checks.by_release_path(release, validated_path)

    file_data = {
        "filename": validated_path.name,
        "bytes_size": file_size,
        "uploaded": datetime.datetime.fromtimestamp(modified, tz=datetime.UTC),
    }

    return await template.render(
        "report-selected-path.html",
        project_name=str(project_name),
        version_name=str(version_name),
        rel_path=str(validated_path),
        package=file_data,
        release=release,
        primary_results=check_results.primary_results_list,
        member_results=check_results.member_results_list,
        ignored_results=check_results.ignored_checks,
        format_file_size=util.format_file_size,
    )
