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

import re
from typing import Annotated, Literal

import pydantic

import atr.form as form
import atr.models.safe as safe

SET_REVISION = Literal["set_revision"]
SET_TAG = Literal["set_tag"]


class SetRevisionForm(form.Form):
    variant: SET_REVISION = form.value(SET_REVISION)
    revision_number: safe.RevisionNumber = form.label("Revision number", widget=form.Widget.HIDDEN)


class SetTagForm(form.Form):
    variant: SET_TAG = form.value(SET_TAG)
    revision_number: str = form.label("Revision number", widget=form.Widget.HIDDEN)
    tag: str = form.label("Tag", "An identifier for this revision")

    @pydantic.field_validator("tag", mode="after")
    @classmethod
    def validate_tag(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if not re.match(r"^[a-zA-Z0-9+_.-]+$", value):
            raise ValueError("Tag must contain only letters, numbers, plus, underscore, dot, or hyphen")
        if len(value.encode("utf-8")) > 256:
            raise ValueError("Tag must be at most 256 bytes")
        return value


type RevisionForm = Annotated[
    SetRevisionForm | SetTagForm,
    form.DISCRIMINATOR,
]
