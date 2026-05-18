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

import pydantic

import atr.form as form
import atr.models.safe as safe
import atr.util as util


class AnnounceForm(form.Form):
    """Form for announcing a release preview."""

    revision_number: safe.RevisionNumber = form.label("Revision number", widget=form.Widget.HIDDEN)
    email_to: str = form.label("To", widget=form.Widget.CUSTOM)
    email_cc: form.StrList = form.label("CC")
    email_bcc: form.StrList = form.label("BCC")
    subject: str = form.label("Subject", widget=form.Widget.CUSTOM)
    subject_template_hash: str = form.label("Subject template hash", widget=form.Widget.HIDDEN)
    body: str = form.label("Body", widget=form.Widget.CUSTOM, max_length=100_000)
    download_path_suffix: safe.OptionalRelPath = form.label("Download path suffix", widget=form.Widget.CUSTOM)
    auto_archive: form.Bool = form.label(
        "Auto archive prior release",
        "If set, the release shown below will be auto-archived",
        widget=form.Widget.CHECKBOX,
        default=False,
    )
    auto_archive_release: str = form.label("Version to archive", widget=form.Widget.STATIC, default="")
    confirm_announce: Literal["CONFIRM"] = form.label(
        "Confirm",
        "Type CONFIRM (in capitals) to enable the submit button.",
    )

    @pydantic.model_validator(mode="after")
    def _validate_recipients(self) -> "AnnounceForm":
        util.validate_email_recipients(self)
        return self
