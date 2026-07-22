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

import pydantic

import atr.form as form
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.util as util


class StartVotingForm(form.Form):
    email_to: str = form.label("To (mailing list)", widget=form.Widget.CUSTOM)
    email_cc: form.StrList = form.label("CC")
    email_bcc: form.StrList = form.label("BCC")
    second_round_email_to: str | None = form.label(
        "Second round mailing list",
        widget=form.Widget.CUSTOM,
        default=None,
    )
    vote_duration: form.Int = form.label(
        "Minimum vote duration",
        "Minimum number of hours the vote will be open for.",
        default=72,
    )
    subject: str = form.label("Subject", widget=form.Widget.CUSTOM)
    subject_template_hash: str = form.label("Subject template hash", widget=form.Widget.HIDDEN)
    body: str = form.label("Body", widget=form.Widget.CUSTOM, max_length=100_000)
    notify_when_finished: form.Bool = form.label(
        "Email reminder",
        widget=form.Widget.CUSTOM,
        default=False,
    )
    automatic_resolve_when_finished: form.Bool = form.label(
        "Automatically resolve this vote",
        widget=form.Widget.CUSTOM,
        default=False,
    )
    automatic_publish_when_resolved: form.Bool = form.label(
        "Automatically publish to SVN when this vote resolves",
        widget=form.Widget.CUSTOM,
        default=False,
    )
    download_path_suffix: safe.OptionalRelPath = form.label(
        "Download path suffix",
        "Optional path under the committee distribution area.",
        default=None,
    )
    concerns_noted: form.StrList = form.label("Concerns noted", widget=form.Widget.CUSTOM)
    vote_mode: sql.VoteMode = form.label("Vote mode", widget=form.Widget.HIDDEN)
    rendered_revision: safe.RevisionNumber = form.label("Rendered revision", widget=form.Widget.HIDDEN)

    @pydantic.field_validator("vote_duration")
    @classmethod
    def validate_vote_duration(cls, field):
        util.validate_vote_duration(field)
        return field

    @pydantic.model_validator(mode="after")
    def _validate_recipients(self) -> StartVotingForm:
        util.validate_email_recipients(self)
        return self


async def concern_groups_for_release(
    read: storage.ReadAsGeneralPublic,
    release: sql.Release,
) -> list[util.ConcernGroup]:
    """Return the concern groups shown on the compose page for this release."""
    base_path = paths.release_directory(release)
    all_paths = sorted([path async for path in util.paths_recursive(base_path)])
    info = await read.releases.path_info(release, all_paths)
    return util.concern_groups(info)
