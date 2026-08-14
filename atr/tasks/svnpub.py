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

import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.notify as notify
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.tasks.checks as checks
import atr.tasks.task as task
import atr.util as util


@checks.with_model(args.ReleaseFinalise)
async def finalise(task_args: args.ReleaseFinalise) -> results.Results | None:
    async with storage.write_as_system(storage.WriteAsReleaseFinaliseService) as warfs:
        return await warfs.release_finalise_published(task_args)


@checks.with_model(args.SvnPublish)
async def publish(task_args: args.SvnPublish) -> results.Results | None:
    async with storage.write(task_args.asf_uid) as write:
        warm = await write.as_project_release_manager(task_args.project_key)
        return await warm.release.publish_to_svn_execute(task_args)


@checks.with_model(args.SvnUnpublish)
async def unpublish(task_args: args.SvnUnpublish) -> results.Results | None:
    try:
        async with storage.write_as_system(storage.WriteAsReleaseUnpublishService) as waru:
            result = await waru.release_unpublish_from_svn(task_args)
    except datatypes.RetryableError as exc:
        # A transient SVN failure - a concurrent commit moved the tree, or a connection wobble.
        # Send the task back to the queue; a later run re-reads the head and usually settles.
        raise task.DeferredError(str(exc)) from exc
    except datatypes.FailedError:
        # The removal failed for good, so the release is archived in the record but its files
        # are still in dist. Put it back to current so the two agree, and tell the archiver.
        async with storage.write_as_system(storage.WriteAsReleaseUnpublishService) as waru:
            await waru.revert_failed_archive(task_args.project_key, task_args.version_key)
        message = (
            f"Could not archive {task_args.project_key!s}-{task_args.version_key!s}: its files could not be"
            " removed from the distribution area, you may wish to retry archiving this release."
        )
        await notify.user(task_args.asf_uid, message, sql.NotificationLevel.WARNING)
        raise
    if result.leftover:
        # Tell whoever archived it that these files may need a manual tidy-up.
        shown = ", ".join(result.leftover[:2])
        if len(result.leftover) > 2:
            shown += f" and {len(result.leftover) - 2} more"
        message = (
            f"Archived {task_args.project_key!s}-{task_args.version_key!s}, but "
            f"{util.plural(len(result.leftover), 'file')} may need manual cleanup in the "
            f"distribution area: {shown}"
        )
        await notify.user(task_args.asf_uid, message, sql.NotificationLevel.WARNING)
    return result
