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
import atr.models.sql as sql


class CancelSubmitForm(form.Form):
    email_body: str = form.label("Email body", widget=form.Widget.TEXTAREA, max_length=100_000)
    vote_result: Literal["Cancelled"] = form.label("Vote result", default="Cancelled", widget=form.Widget.HIDDEN)
    vote_mode: sql.VoteMode | None = form.label("Vote mode", default=None, widget=form.Widget.HIDDEN)
    vote_seq: int | None = form.label("Vote serial", default=None, widget=form.Widget.HIDDEN)

    @pydantic.field_validator("vote_mode", "vote_seq", mode="before")
    @classmethod
    def empty_hidden_value(cls, value: object) -> object:
        if value == "":
            return None
        return value


class SubmitForm(form.Form):
    email_body: str = form.label("Email body", widget=form.Widget.TEXTAREA, max_length=100_000)
    vote_result: Literal["Passed", "Failed", "Cancelled"] = form.label("Vote result", widget=form.Widget.RADIO)
    vote_mode: sql.VoteMode | None = form.label("Vote mode", default=None, widget=form.Widget.HIDDEN)
    vote_seq: int | None = form.label("Vote serial", default=None, widget=form.Widget.HIDDEN)

    @pydantic.field_validator("vote_mode", "vote_seq", mode="before")
    @classmethod
    def empty_hidden_value(cls, value: object) -> object:
        if value == "":
            return None
        return value
