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

import enum
from collections.abc import Sequence
from typing import Literal, NamedTuple

import pydantic

import atr.config as config
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.distribution as distribution
import atr.util as util


class AnnounceForm(form.Form):
    """Form for announcing a release preview."""

    revision_number: safe.RevisionNumber = form.label("Revision number", widget=form.Widget.HIDDEN)
    email_to: str = form.label("To", widget=form.Widget.CUSTOM, required=True)
    email_cc: form.StrList = form.label("CC")
    email_bcc: form.StrList = form.label("BCC")
    subject: str = form.label("Subject", widget=form.Widget.CUSTOM, required=True)
    subject_template_hash: str = form.label("Subject template hash", widget=form.Widget.HIDDEN)
    body: str = form.label("Body", widget=form.Widget.CUSTOM, max_length=100_000, required=True)
    download_page: form.OptionalURL = form.label(
        "Download page URL",
        "URL of the project's official download page. It is checked for a 200 response and then"
        " saved to the project metadata.",
    )
    auto_archive: form.Bool = form.label(
        "Auto archive prior release",
        "If set, the release shown below will be auto-archived",
        widget=form.Widget.CHECKBOX,
        default=False,
    )
    auto_archive_release: str = form.label("Version to archive", widget=form.Widget.STATIC, default="")
    announce_unreachable: form.Bool = form.label(
        "Announce despite an unreachable download server",
        "If set, the announcement proceeds even though the download server could not be checked.",
        widget=form.Widget.CHECKBOX,
        default=False,
    )
    confirm_announce: Literal["CONFIRM"] = form.label(
        "Confirm",
        "Type CONFIRM (in capitals) to enable the submit button.",
        required=True,
    )

    @pydantic.field_validator("download_page")
    @classmethod
    def _validate_download_page(cls, value: pydantic.HttpUrl | None) -> pydantic.HttpUrl | None:
        if value is None:
            return None
        if error := util.download_page_url_error(str(value)):
            raise ValueError(error)
        return value

    @pydantic.model_validator(mode="after")
    def _validate_recipients(self) -> AnnounceForm:
        util.validate_email_recipients(self)
        return self


class AnnounceStep(NamedTuple):
    # blocked is the gate message while the step is not done, and detail is shown on the finish page
    key: Literal["svn", "downloads", "distributions"]
    label: str
    state: StepState
    detail: str
    blocked: str

    @property
    def done(self) -> bool:
        return self.state is StepState.DONE


class StepState(enum.Enum):
    DONE = "done"
    IN_PROGRESS = "in_progress"
    TO_DO = "to_do"


async def blocked_message(release: sql.Release) -> str:
    """Why the release cannot be announced yet, or an empty string."""
    return first_blocked(await steps(release))


def first_blocked(announce_steps: Sequence[AnnounceStep]) -> str:
    """The message of the first step not yet done, or an empty string."""
    for step in announce_steps:
        if not step.done:
            return step.blocked
    return ""


async def steps(release: sql.Release) -> list[AnnounceStep]:
    """The conditions for announcing the release, in order."""
    local = config.svn_publish_kind() is config.SvnPublishKind.LOCAL_REPOSITORY
    publication = await interaction.release_completed_svn_publish_task_for_revision(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    )
    svn_blocked = (
        "Cannot announce until SVN publication is complete."
        if local
        else "Cannot announce until SVN and download area publications are complete."
    )
    if publication is not None:
        svn_state, svn_detail = StepState.DONE, ""
    elif await interaction.release_in_flight_svn_publish_task(
        release.safe_project_key, release.safe_version_key, release.safe_latest_revision_number
    ):
        svn_state, svn_detail = StepState.IN_PROGRESS, "ATR is publishing the files now."
    else:
        svn_state, svn_detail = StepState.TO_DO, ""
    result = [AnnounceStep("svn", "Release files published to SVN dist", svn_state, svn_detail, svn_blocked)]
    if not local:
        result.append(await _downloads_step(publication))
    outstanding = distribution.outstanding_message(release.distributions)
    result.append(
        AnnounceStep(
            "distributions",
            "Third-party distributions found or ignored",
            StepState.IN_PROGRESS if outstanding else StepState.DONE,
            distribution.summary(release.distributions),
            outstanding,
        )
    )
    return result


async def _downloads_available(publish_task_id: int) -> bool:
    async with db.session() as data:
        monitor = await data.task(
            task_type=sql.TaskType.DOWNLOADS_CHECK,
            task_args=args.DownloadsCheck(publish_task_id=publish_task_id).model_dump(),
        ).get()
    return (
        (monitor is not None)
        and (monitor.status is sql.TaskStatus.COMPLETED)
        and isinstance(monitor.result, results.DownloadsCheck)
        and monitor.result.available
    )


async def _downloads_step(publication: sql.Task | None) -> AnnounceStep:
    if publication is None:
        state, detail = StepState.TO_DO, ""
    elif await _downloads_available(publication.id):
        state, detail = StepState.DONE, ""
    else:
        state, detail = StepState.IN_PROGRESS, "ATR is waiting for the files to reach the download servers."
    return AnnounceStep(
        "downloads",
        "Release files available in the download area",
        state,
        detail,
        "Published to SVN dist. Cannot announce until the files appear in the download area.",
    )
