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

from typing import Final

import jwt
import pytest
import quart

import atr.jwtoken as jwtoken

_SIGNING_KEY: Final[str] = "a" * 64


@pytest.fixture(autouse=True)
def _fixed_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jwtoken, "_signing_key", lambda: _SIGNING_KEY)


async def test_bearer_subject_returns_sub_of_valid_token() -> None:
    token = jwtoken.issue("alice")
    assert await _bearer_subject(f"Bearer {token}") == "alice"


async def test_bearer_subject_rejects_token_with_wrong_signature() -> None:
    claims = jwt.decode(jwtoken.issue("alice"), options={"verify_signature": False})
    forged = jwt.encode(claims, "b" * 64, algorithm="HS256")
    assert await _bearer_subject(f"Bearer {forged}") is None


async def test_bearer_subject_rejects_expired_token() -> None:
    token = jwtoken.issue("alice", ttl=-3600)
    assert await _bearer_subject(f"Bearer {token}") is None


async def test_bearer_subject_rejects_malformed_token() -> None:
    assert await _bearer_subject("Bearer not-a-jwt") is None


async def test_bearer_subject_returns_none_without_bearer_header() -> None:
    assert await _bearer_subject(None) is None
    assert await _bearer_subject("Basic dXNlcjpwYXNz") is None


async def _bearer_subject(authorization: str | None) -> str | None:
    app = quart.Quart(__name__)
    headers = {"Authorization": authorization} if (authorization is not None) else {}
    async with app.test_request_context("/", headers=headers):
        return jwtoken.bearer_subject(quart.request)
