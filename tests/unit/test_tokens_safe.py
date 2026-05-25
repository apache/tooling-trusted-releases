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

import asfquart.base as base
import jwt
import pytest

import atr.constants as constants
import atr.db as db
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


class FakeQuery:
    """Mimics db.Query: sync order_by chains, async all/get terminate."""

    def __init__(self, tokens: list[sql.PersonalAccessToken]):
        self._tokens = tokens

    def order_by(self, *_args: object) -> "FakeQuery":
        return self

    async def all(self) -> list[sql.PersonalAccessToken]:
        return self._tokens

    async def get(self) -> sql.PersonalAccessToken | None:
        return self._tokens[0] if self._tokens else None


def _apply_pat_filters(
    tokens: list[sql.PersonalAccessToken],
    token_hash: object = db.NOT_SET,
    id: object = db.NOT_SET,
    asfuid: object = db.NOT_SET,
    created_by: object = db.NOT_SET,
    is_system: object = False,
) -> list[sql.PersonalAccessToken]:
    # Default is_system=False mirrors db.Session.personal_access_token.
    result = list(tokens)
    if db.is_defined(token_hash):
        result = [t for t in result if t.token_hash == token_hash]
    if db.is_defined(id):
        result = [t for t in result if t.id == id]
    if db.is_defined(asfuid):
        result = [t for t in result if t.asfuid == asfuid]
    if db.is_defined(created_by):
        result = [t for t in result if t.created_by == created_by]
    if db.is_defined(is_system):
        result = [t for t in result if t.is_system == is_system]
    return result


class FakeReadData:
    def __init__(self, tokens: list[sql.PersonalAccessToken], most_recent: sql.PersonalAccessToken | None):
        self._tokens = tokens
        self._most_recent = most_recent

    def personal_access_token(self, *args: object, **kwargs: object) -> FakeQuery:
        if args:
            kwargs.setdefault("token_hash", args[0])
        return FakeQuery(_apply_pat_filters(self._tokens, **kwargs))

    async def query_one_or_none(self, _stmt):
        return self._most_recent


class FakeWrite:
    def __init__(self, asf_uid: str | None):
        self.authorisation = FakeAuthorisation(asf_uid)


class FakeWriteAs:
    def __init__(self):
        self.mail = mock.MagicMock()
        self.mail.send = mock.AsyncMock(return_value=("mid", []))
        self.append_to_audit_log = mock.MagicMock()


class FakeWriteData:
    def __init__(self, tokens: list[sql.PersonalAccessToken] | None = None):
        self._added: sql.PersonalAccessToken | None = None
        self._tokens = tokens if tokens is not None else []
        self.deleted: list[sql.PersonalAccessToken] = []

    def add(self, pat: sql.PersonalAccessToken) -> None:
        self._added = pat

    async def delete(self, pat: sql.PersonalAccessToken) -> None:
        self.deleted.append(pat)

    async def commit(self) -> None:
        if self._added is None:
            return
        self._added.id = 101

    def personal_access_token(self, *args: object, **kwargs: object) -> FakeQuery:
        if args:
            kwargs.setdefault("token_hash", args[0])
        return FakeQuery(_apply_pat_filters(self._tokens, **kwargs))


def _make_pat(
    *,
    expires: datetime.datetime | None = None,
    allowed_ip: str | None = None,
    is_system: bool = False,
) -> sql.PersonalAccessToken:
    now = datetime.datetime.now(tz=datetime.UTC)
    # System PATs have no owning user; user PATs have asfuid equal to created_by.
    asfuid = None if is_system else "test"
    return sql.PersonalAccessToken(
        asfuid=asfuid,
        created_by="test",
        token_hash="0" * 64,
        created=now,
        expires=expires if expires is not None else now + datetime.timedelta(days=1),
        allowed_ip=allowed_ip,
        is_system=is_system,
    )


def test_personal_access_token_safe_excludes_token_hash() -> None:
    token = sql.PersonalAccessToken(
        id=5,
        asfuid="test",
        created_by="test",
        token_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        expires=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        label="unit",
        last_used=None,
    )

    safe = types.PersonalAccessTokenSafe.from_sql(token)

    assert safe.id == 5
    assert safe.asfuid == "test"
    assert safe.created_by == "test"
    assert safe.is_system is False
    assert "token_hash" not in safe.model_dump()
    assert not hasattr(safe, "token_hash")


@pytest.mark.asyncio
async def test_reader_pat_methods_return_safe_tokens() -> None:
    created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    token = sql.PersonalAccessToken(
        id=7,
        asfuid="test",
        created_by="test",
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
async def test_reader_hides_system_pats_from_user_listing() -> None:
    user_token = _make_pat()
    user_token.id = 11
    system_token = _make_pat(is_system=True)
    system_token.id = 12

    read = FakeRead("test")
    data = FakeReadData([user_token, system_token], None)
    reader = atr.storage.readers.tokens.FoundationCommitter(read, mock.MagicMock(), data)

    tokens_list = await reader.own_personal_access_tokens()

    assert [t.id for t in tokens_list] == [11]


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


def test_is_expired() -> None:
    now = datetime.datetime.now(tz=datetime.UTC)
    assert _make_pat(expires=now + datetime.timedelta(days=1)).is_expired is False
    assert _make_pat(expires=now - datetime.timedelta(days=1)).is_expired is True


def test_allows_ip() -> None:
    unrestricted = _make_pat(allowed_ip=None)
    assert unrestricted.allows_ip("1.2.3.4") is True
    assert unrestricted.allows_ip(None) is True

    single = _make_pat(allowed_ip="1.2.3.4")
    assert single.allows_ip("1.2.3.4") is True
    assert single.allows_ip("1.2.3.5") is False
    assert single.allows_ip(None) is False
    assert single.allows_ip("not-an-ip") is False

    cidr = _make_pat(allowed_ip="10.0.0.0/8")
    assert cidr.allows_ip("10.1.2.3") is True
    assert cidr.allows_ip("11.0.0.1") is False


@pytest.mark.asyncio
async def test_writer_add_system_token() -> None:
    created = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    write = FakeWrite("admin")
    write_as = FakeWriteAs()
    data = FakeWriteData()
    writer = atr.storage.writers.tokens.FoundationAdmin(write, write_as, data)

    safe = await writer.add_system_token(
        token_hash="0" * 64,
        created=created,
        expires=created + datetime.timedelta(days=180),
        label="asfyaml",
        allowed_ip="10.0.0.0/8",
    )

    assert isinstance(safe, types.PersonalAccessTokenSafe)
    assert safe.id == 101
    # System PATs have no owning user; created_by records the minting admin.
    assert safe.asfuid is None
    assert safe.created_by == "admin"
    assert safe.is_system is True
    assert safe.allowed_ip == "10.0.0.0/8"
    write_as.append_to_audit_log.assert_called_once()
    audit_kwargs = write_as.append_to_audit_log.call_args.kwargs
    assert audit_kwargs["is_system"] is True
    assert audit_kwargs["allowed_ip"] == "10.0.0.0/8"


@pytest.mark.asyncio
async def test_writer_revoke_system_token() -> None:
    token = _make_pat(is_system=True)
    token.id = 5
    write = FakeWrite("admin")
    data = FakeWriteData(tokens=[token])
    writer = atr.storage.writers.tokens.FoundationAdmin(write, FakeWriteAs(), data)

    assert await writer.revoke_system_token(5) is True
    assert data.deleted == [token]

    empty = FakeWriteData(tokens=[])
    writer_empty = atr.storage.writers.tokens.FoundationAdmin(write, FakeWriteAs(), empty)
    assert await writer_empty.revoke_system_token(999) is False


def _signed_jwt(
    secret: str,
    *,
    sub: str = constants.SYSTEM_SERVICE_UID,
    atr_sys: bool = True,
    atr_th: str | None = "0" * 64,
) -> str:
    now = datetime.datetime.now(tz=datetime.UTC)
    payload: dict[str, object] = {
        "sub": sub,
        "iss": jwtoken._ATR_JWT_ISSUER,
        "aud": jwtoken._ATR_JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + datetime.timedelta(minutes=30),
        "jti": "test-jti",
    }
    if atr_sys:
        payload["atr_sys"] = True
    if atr_th is not None:
        payload["atr_th"] = atr_th
    return jwt.encode(payload, secret, algorithm=jwtoken._ALGORITHM)


def _fake_db_session(pat: sql.PersonalAccessToken | None) -> mock.MagicMock:
    pat_query = mock.MagicMock()
    pat_query.get = mock.AsyncMock(return_value=pat)
    fake_data = mock.MagicMock()
    fake_data.personal_access_token = mock.MagicMock(return_value=pat_query)
    ctx = mock.AsyncMock()
    ctx.__aenter__ = mock.AsyncMock(return_value=fake_data)
    ctx.__aexit__ = mock.AsyncMock(return_value=False)
    return mock.MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_verify_system_token_skips_ldap(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a" * 64
    monkeypatch.setattr("atr.jwtoken._signing_key", lambda: secret)
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(side_effect=AssertionError("LDAP consulted")))
    monkeypatch.setattr("atr.db.session", _fake_db_session(_make_pat(is_system=True)))

    claims = await jwtoken.verify(_signed_jwt(secret))

    assert claims["sub"] == constants.SYSTEM_SERVICE_UID
    assert claims["atr_sys"] is True


@pytest.mark.asyncio
async def test_verify_system_token_rejects_wrong_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a" * 64
    monkeypatch.setattr("atr.jwtoken._signing_key", lambda: secret)
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(side_effect=AssertionError("LDAP consulted")))
    monkeypatch.setattr("atr.db.session", _fake_db_session(_make_pat(is_system=True)))

    with pytest.raises(base.ASFQuartException):
        await jwtoken.verify(_signed_jwt(secret, sub="someuser"))


@pytest.mark.asyncio
async def test_verify_system_token_rejects_non_system_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a" * 64
    monkeypatch.setattr("atr.jwtoken._signing_key", lambda: secret)
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(side_effect=AssertionError("LDAP consulted")))
    monkeypatch.setattr("atr.db.session", _fake_db_session(_make_pat(is_system=False)))

    with pytest.raises(base.ASFQuartException):
        await jwtoken.verify(_signed_jwt(secret))


@pytest.mark.asyncio
async def test_verify_system_claim_without_pat_hash_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a" * 64
    monkeypatch.setattr("atr.jwtoken._signing_key", lambda: secret)
    monkeypatch.setattr("atr.ldap.is_active", mock.AsyncMock(side_effect=AssertionError("LDAP consulted")))
    with pytest.raises(base.ASFQuartException):
        await jwtoken.verify(_signed_jwt(secret, atr_th=None))


@pytest.mark.asyncio
async def test_verify_user_token_requires_ldap_active(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "a" * 64
    monkeypatch.setattr("atr.jwtoken._signing_key", lambda: secret)
    is_active = mock.AsyncMock(return_value=False)
    monkeypatch.setattr("atr.ldap.is_active", is_active)

    with pytest.raises(base.ASFQuartException):
        await jwtoken.verify(_signed_jwt(secret, sub="someuser", atr_sys=False, atr_th=None))
    is_active.assert_awaited_once()
