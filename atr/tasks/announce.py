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

from typing import Final

import atr.db as db
import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.notify as notify
import atr.shared.announce as announce
import atr.storage as storage
import atr.tasks.checks as checks
import atr.tasks.task as task

_RETRY_SECONDS: Final[int] = 60


@checks.with_model(args.AnnounceWhenReady)
async def when_ready(task_args: args.AnnounceWhenReady) -> results.AnnounceWhenReady:
    async with db.session() as data:
        release = await data.release(
            project_key=str(task_args.project_key),
            version=str(task_args.version_key),
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
            latest_revision_number=str(task_args.revision_number),
            _distributions=True,
        ).get()
        if release is None:
            return await _give_up(task_args, "The release changed after it was queued")
        waiting = await announce.blocked_message(release)
    if waiting:
        raise task.DeferredError(seconds=_RETRY_SECONDS, result=results.AnnounceWhenReady(message=waiting))
    try:
        async with storage.write(task_args.asf_uid) as write:
            warm = await write.as_project_release_manager(task_args.project_key)
            await warm.announce.release(
                project_key=task_args.project_key,
                version_key=task_args.version_key,
                preview_revision_number=task_args.revision_number,
                email_to=task_args.email_to,
                body=task_args.body,
                fullname=task_args.fullname,
                subject_template_hash=task_args.subject_template_hash,
                email_cc=task_args.email_cc,
                email_bcc=task_args.email_bcc,
                auto_archive_prior=task_args.auto_archive_prior,
            )
    except storage.PropagationUnreachableError as e:
        # Nobody is here to say the announcement should go out anyway, so this counts as not ready
        raise task.DeferredError(seconds=_RETRY_SECONDS, result=results.AnnounceWhenReady(message=str(e))) from e
    except storage.AccessError as e:
        return await _give_up(task_args, str(e))
    message = f"The queued announcement of {task_args.project_key} {task_args.version_key} was sent."
    await notify.user(task_args.asf_uid, message, sql.NotificationLevel.INFO)
    return results.AnnounceWhenReady(announced=True, message=message)


async def _give_up(task_args: args.AnnounceWhenReady, reason: str) -> results.AnnounceWhenReady:
    message = f"The queued announcement of {task_args.project_key} {task_args.version_key} was not sent: {reason}"
    await notify.user(
        task_args.asf_uid,
        message,
        sql.NotificationLevel.ERROR,
        link=f"/finish/{task_args.project_key}/{task_args.version_key}",
        link_text="Go to the release",
    )
    return results.AnnounceWhenReady(message=message)
