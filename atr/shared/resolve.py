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

from typing import Annotated, Literal, Self

import pydantic

import atr.form as form
import atr.models.sql as sql
import atr.util as util

type CANCEL_SUBMIT = Literal["cancel_submit"]
type SUBMIT = Literal["submit"]


class ResolveFormBase(form.Form):
    result_email_to: str = form.label("To", widget=form.Widget.TEXT, default="", readonly=True)
    email_cc: form.StrList = form.label("Additional recipients", widget=form.Widget.CUSTOM)
    email_bcc: form.StrList = form.label("BCC")
    vote_mode: sql.VoteMode | None = form.label("Vote mode", default=None, widget=form.Widget.HIDDEN)
    vote_seq: int | None = form.label("Vote serial", default=None, widget=form.Widget.HIDDEN)

    @pydantic.field_validator("vote_mode", "vote_seq", mode="before")
    @classmethod
    def empty_hidden_value(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @pydantic.model_validator(mode="after")
    def _validate_recipients(self) -> Self:
        util.validate_no_duplicate_recipients([*self.email_cc, *self.email_bcc])
        return self


class CancelSubmitForm(ResolveFormBase):
    variant: CANCEL_SUBMIT = form.value(CANCEL_SUBMIT)
    email_body: str = form.label("Email body", widget=form.Widget.TEXTAREA, max_length=100_000, required=True)
    confirm_cancel: Literal["CONFIRM"] = form.label(
        "Confirm",
        "Type CONFIRM (in capitals) to cancel this vote.",
        required=True,
    )
    vote_result: Literal["Cancelled"] = form.label("Vote result", default="Cancelled", widget=form.Widget.HIDDEN)


class SubmitForm(ResolveFormBase):
    variant: SUBMIT = form.value(SUBMIT)
    result_subject: str = form.label("Subject", widget=form.Widget.TEXT, default="", readonly=True)
    email_body: str = form.label(
        "Body",
        "{{OUTCOME}} is a placeholder - it will be replaced with the vote result you select when the email is sent.",
        widget=form.Widget.TEXTAREA,
        max_length=100_000,
        required=True,
    )
    vote_result: Literal["Passed", "Failed", "Cancelled"] = form.label(
        "Vote result", widget=form.Widget.RADIO, required=True
    )
    automatic_resolve_when_finished: form.Bool = form.label(
        "Automatically resolve the second round vote",
        widget=form.Widget.CUSTOM,
        default=False,
    )
    notify_when_finished: form.Bool = form.label(
        "Notify me when the second round vote ends",
        widget=form.Widget.CUSTOM,
        default=False,
    )


type ResolveForm = Annotated[
    CancelSubmitForm | SubmitForm,
    form.DISCRIMINATOR,
]
