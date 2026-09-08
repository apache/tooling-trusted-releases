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

import unittest.mock as mock

import pytest

import atr.models.validation as validation
import atr.rat_excludes as rat_excludes
import atr.util as util


def test_empty_url_is_allowed() -> None:
    # An unset URL means we don't fetch anything, so it must pass validation
    validation.validate_rat_excludes_url("")
    validation.validate_rat_excludes_url("   ")


@pytest.mark.parametrize(
    "url",
    [
        "https://gitbox.apache.org/repos/asf/example.git/.rat-excludes",
        "https://apache.org/.rat-excludes",
        "https://raw.githubusercontent.com/apache/example/main/.rat-excludes",
    ],
)
def test_allowed_hosts_pass(url: str) -> None:
    validation.validate_rat_excludes_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://gitbox.apache.org/.rat-excludes",
        "https://example.com/.rat-excludes",
        "https://apache.org.evil.com/.rat-excludes",
        "ftp://apache.org/.rat-excludes",
        "https:///.rat-excludes",
    ],
)
def test_disallowed_urls_raise(url: str) -> None:
    with pytest.raises(ValueError):
        validation.validate_rat_excludes_url(url)


def _mock_session(response: mock.Mock) -> mock.MagicMock:
    session = mock.Mock()
    session.get = mock.AsyncMock(return_value=response)
    session_cm = mock.MagicMock()
    session_cm.__aenter__ = mock.AsyncMock(return_value=session)
    session_cm.__aexit__ = mock.AsyncMock(return_value=False)
    return session_cm


@pytest.mark.asyncio
async def test_redirect_is_treated_as_unavailable() -> None:
    # An allowlisted host that redirects elsewhere must not be followed
    response = mock.Mock(status=302)
    response.raise_for_status = mock.Mock()
    response.content = mock.Mock(read=mock.AsyncMock(return_value=b""))
    with mock.patch.object(util, "create_secure_session", return_value=_mock_session(response)):
        with pytest.raises(rat_excludes.UnavailableError):
            await rat_excludes.fetch("https://apache.org/.rat-excludes")


@pytest.mark.asyncio
async def test_oversized_body_is_unavailable() -> None:
    response = mock.Mock(status=200)
    response.raise_for_status = mock.Mock()
    response.content = mock.Mock(read=mock.AsyncMock(return_value=b"x" * (64 * 1024 + 1)))
    with mock.patch.object(util, "create_secure_session", return_value=_mock_session(response)):
        with pytest.raises(rat_excludes.UnavailableError):
            await rat_excludes.fetch("https://apache.org/.rat-excludes")
