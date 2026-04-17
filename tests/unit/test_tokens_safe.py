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

import datetime
import unittest.mock as mock

import jwt
import pytest

import atr.jwtoken as jwtoken
import atr.models.sql as sql
import atr.storage.readers.tokens
import atr.storage.types as types
import atr.storage.writers.tokens


class FakeAuthorisation:
    def __init__(self, asf_uid: str | None):
        self.asf_uid = asf_uid


class FakeRead:
    def __init__(self, asf_uid: str | None):
        self.authorisation = FakeAuthorisation(asf_uid)


class FakeReadData:
    def __init__(self, tokens: list[sql.PersonalAccessToken], most_recent: sql.PersonalAccessToken | None):
        self._tokens = tokens
        self._most_recent = most_recent

    async def query_all(self, _stmt):
        return self._tokens

    async def query_one_or_none(self, _stmt):
        return self._most_recent


class FakeWrite:
    def __init__(self, asf_uid: str | None):
        self.authorisation = FakeAuthorisation(asf_uid)


class FakeWriteAs:
    def __init__(self):
        self.mail = mock.MagicMock()
        self.mail.send = mock.AsyncMock(return_value=("mid", []))


class FakeWriteData:
    def __init__(self):
        self._added: sql.PersonalAccessToken | None = None

    def add(self, pat: sql.PersonalAccessToken) -> None:
        self._added = pat

    async def commit(self) -> None:
        if self._added is None:
            return
        self._added.id = 101


def test_personal_access_token_safe_excludes_token_hash() -> None:
    token = sql.PersonalAccessToken(
        id=5,
        asfuid="test",
        token_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        expires=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        label="unit",
        last_used=None,
    )

    safe = types.PersonalAccessTokenSafe.from_sql(token)

    assert safe.id == 5
    assert safe.asfuid == "test"
    assert "token_hash" not in safe.model_dump()
    assert not hasattr(safe, "token_hash")


@pytest.mark.asyncio
async def test_reader_pat_methods_return_safe_tokens() -> None:
    created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    token = sql.PersonalAccessToken(
        id=7,
        asfuid="test",
        token_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        created=created,
        expires=created + datetime.timedelta(days=180),
        label="reader-test",
        last_used=created + datetime.timedelta(days=1),
    )
    read = FakeRead("test")
    data = FakeReadData([token], token)
    reader = atr.storage.readers.tokens.FoundationCommitter(read, mock.MagicMock(), data)

    tokens_list = await reader.own_personal_access_tokens()
    most_recent = await reader.most_recent_jwt_pat()

    assert len(tokens_list) == 1
    assert isinstance(tokens_list[0], types.PersonalAccessTokenSafe)
    assert "token_hash" not in tokens_list[0].model_dump()
    assert isinstance(most_recent, types.PersonalAccessTokenSafe)
    assert most_recent is not None
    assert "token_hash" not in most_recent.model_dump()


@pytest.mark.asyncio
async def test_verify_rejects_jwt_without_nbf(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.datetime.now(tz=datetime.UTC)
    monkeypatch.setattr("atr.jwtoken._signing_key", lambda: "a" * 64)
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(side_effect=AssertionError))

    token = jwt.encode(
        {
            "sub": "test",
            "iss": jwtoken._ATR_JWT_ISSUER,
            "aud": jwtoken._ATR_JWT_AUDIENCE,
            "iat": now,
            "exp": now + datetime.timedelta(minutes=30),
            "jti": "test-jti",
        },
        "a" * 64,
        algorithm=jwtoken._ALGORITHM,
    )

    with pytest.raises(jwt.MissingRequiredClaimError, match="nbf"):
        await jwtoken.verify(token)


@pytest.mark.asyncio
async def test_writer_add_token_returns_safe_token() -> None:
    created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    write = FakeWrite("test")
    write_as = FakeWriteAs()
    data = FakeWriteData()
    writer = atr.storage.writers.tokens.FoundationCommitter(write, write_as, data)

    safe = await writer.add_token(
        token_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        created=created,
        expires=created + datetime.timedelta(days=180),
        label="writer-test",
    )

    assert isinstance(safe, types.PersonalAccessTokenSafe)
    assert safe.id == 101
    assert "token_hash" not in safe.model_dump()
