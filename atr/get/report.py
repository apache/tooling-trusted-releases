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

import collections
import datetime
from typing import Literal

import aiofiles.os
import asfquart.base as base

import atr.attestable as attestable
import atr.blueprints.get as get
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected_path(
    _session: web.Public,
    _report: Literal["report"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    rel_path: safe.RelPath,
) -> str:
    """
    URL: /report/<project_key>/<version_key>/<rel_path>
    Show the report for a specific file.
    """
    validated_path = rel_path.as_path()

    # If the draft is not found, we try to get the release candidate
    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
            phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            _committee=True,
            _release_policy=True,
            _project_release_policy=True,
        ).get()
        if release is None:
            release = await data.release(
                project_key=str(project_key),
                version=str(version_key),
                phase=sql.ReleasePhase.RELEASE_CANDIDATE,
                _committee=True,
                _release_policy=True,
                _project_release_policy=True,
            ).demand(base.ASFQuartException("Release does not exist", errorcode=404))

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

    swhid_dir = None
    attestable_data = await attestable.load(project_key, version_key, release.safe_latest_revision_number)
    if attestable_data is not None:
        swhid_dir = attestable.path_swhid_dir(attestable_data, str(rel_path))

    # Get all check results for this file
    async with storage.read() as read:
        ragp = read.as_general_public()
        check_results = await ragp.checks.by_release_path(release, validated_path)

    file_data = {
        "filename": validated_path.name,
        "bytes_size": file_size,
        "uploaded": datetime.datetime.fromtimestamp(modified, tz=datetime.UTC),
        "swhid": swhid_dir,
    }

    return await template.render(
        "report-selected-path.html",
        project_key=str(project_key),
        version_key=str(version_key),
        rel_path=str(rel_path),
        package=file_data,
        release=release,
        reconciliation=_reconciliation(check_results.primary_results_list, check_results.member_results_list),
        primary_results=check_results.primary_results_list,
        member_results=check_results.member_results_list,
        format_file_size=util.format_file_size,
    )


def _reconciliation(primary_results: list[sql.CheckResult], member_results: dict[str, list[sql.CheckResult]]) -> str:
    primary_counts = collections.Counter(result.status for result in primary_results)
    member_counts = collections.Counter(result.status for results in member_results.values() for result in results)
    sentences: list[str] = []
    for status in (
        sql.CheckResultStatus.BLOCKER,
        sql.CheckResultStatus.EXCEPTION,
        sql.CheckResultStatus.CONCERN,
        sql.CheckResultStatus.SUGGESTION,
    ):
        member_count = member_counts[status]
        if member_count == 0:
            continue
        primary_count = primary_counts[status]
        total = util.plural(primary_count + member_count, status.value)
        sentences.append(f"{total}: {primary_count} on this file, {member_count} on files inside this archive.")
    return " ".join(sentences)
