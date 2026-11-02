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

from unittest import mock

import pytest
import quart

import atr.form as form
import atr.sessions as sessions


@pytest.fixture
def app(monkeypatch):
    # Rendering pops flashed errors from the user session, which wants the real asfquart app
    monkeypatch.setattr(sessions, "form_error_pop", mock.AsyncMock(return_value={}))
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    # The rendered form carries a CSRF token, which wants a signing key
    app.config["SECRET_KEY"] = "test"
    return app


async def _render(app: quart.Quart, confirm: str) -> str:
    async with app.test_request_context("/x"):
        return str(await form.render(model_cls=form.Empty, action="/x", confirm=confirm, empty=True))


@pytest.mark.parametrize(
    ("confirm", "fragment"),
    [
        ("Archive 0.1+sbp? It will be archived.", "0.1+sbp"),
        ("Remove the catalogue row for maven/maven-3/3.9.9/apache-maven-3.9.9-bin.tar.gz?", "maven/maven-3"),
        ("Delete the file (copy)?", "(copy)"),
    ],
)
async def test_render_confirm_passes_version_and_path_characters_through(app, confirm: str, fragment: str) -> None:
    # The message goes into a data attribute and is read back by form-confirm.js, so there is
    # no string literal to break out of and nothing to reject
    rendered = await _render(app, confirm)

    assert f'data-confirm="{confirm}"' in rendered
    assert fragment in rendered


async def test_render_confirm_escapes_markup_in_attribute(app) -> None:
    rendered = await _render(app, 'Go <b>"now"</b>?')

    assert "<b>" not in rendered
    assert 'data-confirm="Go &lt;b&gt;&#34;now&#34;&lt;/b&gt;?"' in rendered
