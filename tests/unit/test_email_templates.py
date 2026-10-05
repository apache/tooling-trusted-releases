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
import types
import unittest.mock as mock

import pytest

import atr.construct as construct
import atr.email_templates as email_templates
import atr.util as util

_URL = "https://raw.githubusercontent.com/apache/example/main/vote.txt"


@pytest.mark.asyncio
async def test_fetch_decodes_body() -> None:
    response = _response(200, b"Hello {{PROJECT_NAME}}\n")
    with mock.patch.object(util, "create_secure_session", return_value=_mock_session(response)):
        assert await email_templates.fetch(_URL) == "Hello {{PROJECT_NAME}}\n"


@pytest.mark.asyncio
async def test_fetch_rejects_empty_body() -> None:
    response = _response(200, b"  \n")
    with mock.patch.object(util, "create_secure_session", return_value=_mock_session(response)):
        with pytest.raises(email_templates.EmailTemplateError):
            await email_templates.fetch(_URL)


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_body() -> None:
    response = _response(200, b"x" * (64 * 1024 + 1))
    with mock.patch.object(util, "create_secure_session", return_value=_mock_session(response)):
        with pytest.raises(email_templates.EmailTemplateError):
            await email_templates.fetch(_URL)


@pytest.mark.asyncio
async def test_fetch_rejects_redirect() -> None:
    response = _response(302, b"")
    with mock.patch.object(util, "create_secure_session", return_value=_mock_session(response)):
        with pytest.raises(email_templates.EmailTemplateError):
            await email_templates.fetch(_URL)


@pytest.mark.asyncio
async def test_resolve_falls_back_to_default_with_warning() -> None:
    project = _project(start_vote_template_url=_URL)
    with (
        mock.patch.object(email_templates, "fetch", side_effect=email_templates.EmailTemplateError("boom")),
    ):
        resolved = await construct.resolve_template(project, "start_vote")
    assert resolved.body == "default body"
    assert resolved.warning is not None
    assert "boom" in resolved.warning
    assert _URL in resolved.warning


@pytest.mark.asyncio
async def test_resolve_uses_fetched_template() -> None:
    project = _project(start_vote_template_url=_URL)
    with (
        mock.patch.object(email_templates, "fetch", mock.AsyncMock(return_value="fetched body")),
    ):
        resolved = await construct.resolve_template(project, "start_vote")
    assert resolved.body == "fetched body"
    assert resolved.warning is None


@pytest.mark.asyncio
async def test_resolve_without_url_uses_policy_template() -> None:
    project = _project()
    fetch = mock.AsyncMock()
    with (
        mock.patch.object(email_templates, "fetch", fetch),
    ):
        resolved = await construct.resolve_template(project, "start_vote")
    assert resolved.body == "inline body"
    assert resolved.warning is None
    fetch.assert_not_called()


def _mock_session(response: mock.Mock) -> mock.MagicMock:
    session = mock.Mock()
    session.get = mock.AsyncMock(return_value=response)
    session_cm = mock.MagicMock()
    session_cm.__aenter__ = mock.AsyncMock(return_value=session)
    session_cm.__aexit__ = mock.AsyncMock(return_value=False)
    return session_cm


def _project(**overrides: str) -> types.SimpleNamespace:
    values = {
        "policy_start_vote_template_url": "",
        "policy_start_vote_template": "inline body",
        "policy_start_vote_default": "default body",
        "key": "example",
    }
    values.update({f"policy_{name}": value for name, value in overrides.items()})
    return types.SimpleNamespace(**values)


def _response(status: int, body: bytes) -> mock.Mock:
    response = mock.Mock(status=status)
    response.raise_for_status = mock.Mock()
    response.content = mock.Mock(read=mock.AsyncMock(return_value=body))
    return response
