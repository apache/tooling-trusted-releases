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

import contextlib
import unittest.mock as mock

import asfquart.session
import pytest
import quart

import atr.blueprints.common as common
import atr.config as config
import atr.get.tokens
import atr.models.sql as sql
import atr.post.tokens
import atr.sessions as sessions
import atr.storage as storage
import atr.web as web


@pytest.fixture
def token_routes(monkeypatch):
    session = sql.UserSession(uid="test")
    monkeypatch.setattr(asfquart.session, "read", mock.AsyncMock(return_value=session))
    monkeypatch.setattr(common, "authenticate", mock.AsyncMock(return_value=web.Committer(session)))
    monkeypatch.setattr(sessions, "form_error_pop", mock.AsyncMock(return_value={}))
    save_error = mock.AsyncMock()
    monkeypatch.setattr(sessions, "form_error_put", save_error)
    reader = mock.MagicMock()
    reader.tokens.own_personal_access_tokens = mock.AsyncMock(return_value=[])
    reader.tokens.most_recent_jwt_pat = mock.AsyncMock(return_value=None)
    monkeypatch.setattr(storage, "read_as_foundation_committer", lambda: contextlib.nullcontext(reader))
    writer = mock.MagicMock()
    issue_jwt = mock.AsyncMock(return_value="issued-jwt")
    writer.as_foundation_committer.return_value.tokens.issue_jwt = issue_jwt
    monkeypatch.setattr(storage, "write", lambda _: contextlib.nullcontext(writer))
    render = mock.AsyncMock(return_value="tokens page")
    monkeypatch.setattr(atr.get.tokens.template, "blank", render)
    app = quart.Quart(__name__)
    app.secret_key = "test-key"
    app.add_url_rule("/tokens", endpoint=atr.get.tokens.tokens.endpoint, view_func=atr.get.tokens.tokens)
    app.add_url_rule(
        "/tokens/jwt",
        endpoint=atr.post.tokens.jwt_post.endpoint,
        view_func=atr.post.tokens.jwt_post,
        methods=["POST"],
    )
    return app, issue_jwt, render, save_error


@pytest.mark.parametrize("mode", list(config.Mode))
@pytest.mark.parametrize("malformed", [False, True])
async def test_jwt_post_mode_gate(token_routes, monkeypatch, mode, malformed):
    app, issue_jwt, _, save_error = token_routes
    monkeypatch.setattr(config, "get_mode", lambda: mode)
    form_data = {"pat": "submitted-pat", "csrf_token": "x"}
    if malformed:
        form_data["extra"] = "unexpected"
    response = await app.test_client().post("/tokens/jwt", form=form_data, headers={"Accept": "text/html"})
    if mode is config.Mode.Production:
        assert response.status_code == 404
        issue_jwt.assert_not_awaited()
    elif malformed:
        assert response.status_code == 400
        issue_jwt.assert_not_awaited()
    else:
        assert response.status_code == 200
        assert await response.get_data(as_text=True) == "issued-jwt"
        assert response.headers["Cache-Control"] == "no-store"
        issue_jwt.assert_awaited_once_with("submitted-pat", "<local>")
    save_error.assert_not_awaited()


@pytest.mark.parametrize("mode", list(config.Mode))
async def test_tokens_page_mode_gate(token_routes, monkeypatch, mode):
    app, _, render, _ = token_routes
    monkeypatch.setattr(config, "get_mode", lambda: mode)
    response = await app.test_client().get("/tokens")
    assert response.status_code == 200
    context = render.call_args.kwargs
    content = str(context["content"])
    assert "Generate token" in content
    assert ('name="pat"' in content) == (mode is not config.Mode.Production)
    assert ("create-a-jwt" in context.get("typescripts", [])) == (mode is not config.Mode.Production)
