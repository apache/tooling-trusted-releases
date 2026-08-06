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

from typing import Annotated, Final, Literal

import pydantic

import atr.form as form

PAT_EXPIRY_DAYS: Final[int] = 180
# Changing this value does not affect the validation of any existing PATs
# This is a provisional value pending a decision being made by Infra and Security
PAT_NOISY_SECRET_DOMAIN: Final[bytes] = b"apache.org"

type ADD_TOKEN = Literal["add_token"]
type DELETE_TOKEN = Literal["delete_token"]


class AddTokenForm(form.Form):
    variant: ADD_TOKEN = form.value(ADD_TOKEN)
    label: str = form.label("Label", required=True)

    @pydantic.field_validator("label", mode="after")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Label is required")
        if len(value) > 100:
            raise ValueError("Label must be 100 characters or less")
        return value


class DeleteTokenForm(form.Form):
    variant: DELETE_TOKEN = form.value(DELETE_TOKEN)
    token_id: form.Int = form.label("Token ID", widget=form.Widget.HIDDEN)


class IssueForm(form.Form):
    pat: str = form.label("PAT", widget=form.Widget.TEXT, required=True)


type TokenForm = Annotated[
    AddTokenForm | DeleteTokenForm,
    form.DISCRIMINATOR,
]
