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

from typing import Any

import atr.analysis as analysis
import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.paths as paths
import atr.tasks.checks as checks
import atr.tasks.task as task
import atr.util as util


@checks.with_model(args.DownloadsCheck)
async def check(task_args: args.DownloadsCheck, *, task_id: int) -> results.DownloadsCheck:
    if config.svn_publish_kind() is not config.SvnPublishKind.ASF_DISTRIBUTION:
        return results.DownloadsCheck(message="Local SVN repository in use. Download checking is disabled.")
    async with db.session() as data:
        publication = await data.task(
            id=task_args.publish_task_id, task_type=sql.TaskType.SVN_PUBLISH, status=sql.TaskStatus.COMPLETED
        ).get()
        if publication is None:
            return results.DownloadsCheck(message="The publication is no longer available to monitor.")
        identity = args.SvnPublish.model_validate(publication.task_args)
        release = await data.release(
            project_key=str(identity.project_key),
            version=str(identity.version_key),
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
            latest_revision_number=str(identity.revision_number),
        ).get()
        if (release is None) or (release.project.status is not sql.ProjectStatus.ACTIVE):
            return results.DownloadsCheck(message="This release preview is no longer current.")
        current = await data.task(id=task_id).demand(RuntimeError("Download monitoring task not found"))
        progress = current.result if isinstance(current.result, results.DownloadsCheck) else None
    committee = release.committee
    if committee is None:
        raise ValueError("Release has no committee")
    # Files published to dist/atr never reach the download area, so check wherever they were published
    public_url = util.publication_check_url(committee, identity.download_path_suffix, util.DownloadFile.METADATA)
    rel_paths = sorted(
        [
            str(rel)
            async for rel in util.paths_recursive(paths.release_directory(release))
            if analysis.is_artifact(str(rel))
        ]
    )
    return await _check_paths(public_url, rel_paths, progress)


def queue(data: db.Session, publish_task_id: int, task_args: dict[str, Any]) -> None:
    if config.svn_publish_kind() is not config.SvnPublishKind.ASF_DISTRIBUTION:
        return
    identity = args.SvnPublish.model_validate(task_args)
    data.add(
        sql.Task(
            task_type=sql.TaskType.DOWNLOADS_CHECK,
            task_args=args.DownloadsCheck(publish_task_id=publish_task_id).model_dump(),
            asf_uid=constants.SYSTEM_SERVICE_UID,
            project_key=str(identity.project_key),
            version_key=str(identity.version_key),
            revision_number=str(identity.revision_number),
        )
    )


async def _check_paths(
    public_url: str, rel_paths: list[str], progress: results.DownloadsCheck | None
) -> results.DownloadsCheck:
    if not rel_paths:
        return results.DownloadsCheck(message="No artifacts were found to check.")
    if progress is None:
        progress = results.DownloadsCheck(waiting_for=rel_paths[0], total=len(rel_paths))
    if progress.waiting_for is not None:
        await _probe(public_url, [progress.waiting_for], progress)
        progress.waiting_for = None
    batch = rel_paths[progress.cursor : progress.cursor + util.MAX_PROPAGATION_ARTIFACTS]
    await _probe(public_url, batch, progress)
    progress.cursor += len(batch)
    if progress.cursor < progress.total:
        progress.message = f"Checking download availability at {public_url}."
        raise task.DeferredError(seconds=0, result=progress)
    progress.available = True
    progress.message = (
        f"All {progress.total} artifact URLs were available at {public_url} when checked."
        " Download availability is checked again when the form is submitted."
    )
    return progress


async def _probe(public_url: str, rel_paths: list[str], progress: results.DownloadsCheck) -> None:
    summary = await util.check_propagation(util.svn_publish_target(), public_url, rel_paths)
    failed = next((outcome for outcome in summary.outcomes if not outcome.ok), None)
    if failed is None:
        return
    progress.cursor = 0
    progress.waiting_for = failed.rel_path
    progress.message = f"Waiting for {failed.public_url} ({failed.error}). Checking again in 30 seconds."
    raise task.DeferredError(seconds=30, result=progress)
