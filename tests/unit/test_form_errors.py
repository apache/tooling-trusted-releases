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

import pydantic
import pytest
import quart

import atr.blueprints.common as common
import atr.form as form


class NameForm(form.Form):
    name: str = form.label("Name")


def test_flash_error_text() -> None:
    flash_data = {
        "name": {"label": "Name", "original": None, "kind": "missing", "msg": "Field required"},
        "other": {"label": "*", "original": None, "kind": "value_error", "msg": "Form is invalid"},
        "!extra": {"original": "kept"},
    }
    assert form.flash_error_text(flash_data) == "Name: Field required; Form is invalid"


@pytest.mark.asyncio
async def test_flash_form_error_json() -> None:
    app = quart.Quart(__name__)
    with pytest.raises(pydantic.ValidationError) as exc_info:
        form.validate(NameForm, {"csrf_token": "x"})
    async with app.test_request_context("/compose/example/0.1", method="POST", headers={"Accept": "application/json"}):
        response, status = await common.flash_form_error(NameForm, exc_info.value)
    assert status == 400
    assert await response.get_json() == {"ok": False, "message": "Name: Field required"}
