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

import atr.form as form
import atr.models.safe as safe


class AnnounceForm(form.Form):
    """Form for announcing a release preview."""

    revision_number: safe.RevisionNumber = form.label("Revision number", widget=form.Widget.HIDDEN)
    mailing_list: str = form.label(
        "Send vote email to",
        widget=form.Widget.CUSTOM,
    )
    subject: str = form.label("Subject", widget=form.Widget.CUSTOM)
    subject_template_hash: str = form.label("Subject template hash", widget=form.Widget.HIDDEN)
    body: str = form.label("Body", widget=form.Widget.CUSTOM)
    download_path_suffix: safe.OptionalRelPath = form.label("Download path suffix", widget=form.Widget.CUSTOM)
    confirm_announce: Literal["CONFIRM"] = form.label(
        "Confirm",
        "Type CONFIRM (in capitals) to enable the submit button.",
    )
