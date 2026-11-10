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

import asyncio
import importlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, Final

import asfquart.base as base
import jwt
import pytest
import quart
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.blueprints.api as api_blueprint
import atr.blueprints.api_auth as api_auth
import atr.db as db
import atr.jwtoken as jwtoken
import atr.models.safe as safe
import atr.models.sql as sql

type _Stored = Callable[..., Awaitable[None]]

_PROJECT: Final = safe.ProjectKey("example")
_SIGNING_KEY: Final[str] = "a" * 64


@pytest.fixture(autouse=True)
def _fixed_signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jwtoken, "_signing_key", lambda: _SIGNING_KEY)


def test_authorize_project_accepts_the_token_project() -> None:
    _authorize({"sub": "alice", "atr_prj": "example"}, [SimpleNamespace(project=_PROJECT)])


def test_authorize_project_leaves_an_unlimited_token_alone() -> None:
    _authorize({"sub": "alice"}, [SimpleNamespace(project="other")])
    _authorize({"sub": "alice"}, [])


def test_authorize_project_refuses_a_request_naming_no_project() -> None:
    with pytest.raises(base.ASFQuartException) as exc_info:
        _authorize({"sub": "alice", "atr_prj": "example"}, [SimpleNamespace(version="1.0"), None])
    assert exc_info.value.errorcode == 403


@pytest.mark.parametrize("requested", ["other", "Example", "example-sub"])
def test_authorize_project_refuses_another_project(requested: str) -> None:
    with pytest.raises(base.ASFQuartException) as exc_info:
        _authorize({"sub": "alice", "atr_prj": "example"}, [SimpleNamespace(project=requested)])
    assert exc_info.value.errorcode == 403


def test_authorize_project_refuses_two_projects_in_one_request() -> None:
    arguments = [SimpleNamespace(project="example"), SimpleNamespace(project="other")]
    with pytest.raises(base.ASFQuartException) as exc_info:
        _authorize({"sub": "alice", "atr_prj": "example"}, arguments)
    assert exc_info.value.errorcode == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", [api_auth.Auth.BEARER, api_auth.Auth.SYSTEM_BEARER])
async def test_authenticate_header_refuses_a_project_token_on_other_schemes(
    monkeypatch: pytest.MonkeyPatch, scheme: api_auth.Auth
) -> None:
    async def fake_authenticate() -> dict[str, object]:
        # The system claim can't appear on a real project token, and is here so that only the project check can refuse
        return {"sub": "alice", "atr_prj": "example", "atr_sys": True}

    monkeypatch.setattr(jwtoken, "authenticate", fake_authenticate)

    with pytest.raises(base.ASFQuartException) as exc_info:
        await api_auth.authenticate_header(scheme)
    assert exc_info.value.errorcode == 403


@pytest.mark.asyncio
async def test_authenticate_header_takes_a_project_token_on_the_project_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_authenticate() -> dict[str, object]:
        return {"sub": "alice", "atr_prj": "example"}

    monkeypatch.setattr(jwtoken, "authenticate", fake_authenticate)

    await api_auth.authenticate_header(api_auth.Auth.BEARER_PROJECT)


def test_issue_for_project_limits_the_token_to_five_minutes_and_one_project() -> None:
    token = jwtoken.issue_for_project("alice", _PROJECT)
    claims = jwt.decode(token.jwt, options={"verify_signature": False})

    assert claims["sub"] == "alice"
    assert claims["atr_prj"] == "example"
    assert claims["exp"] - claims["iat"] == 5 * 60
    assert (token.jti, token.expires) == (claims["jti"], claims["exp"])
    assert "atr_th" not in claims
    assert "atr_sys" not in claims


def test_only_release_create_and_store_take_a_project_token() -> None:
    # Importing atr.api is what registers the routes
    importlib.import_module("atr.api")

    schemes = api_blueprint.route_auth_schemes()
    assert {name for name, scheme in schemes.items() if scheme == "bearer_project"} == {
        "release_create",
        "release_store",
    }


def test_project_claims_check_refuses_an_expired_token_inside_the_leeway() -> None:
    claims = {"sub": "alice", "atr_prj": "example", "exp": int(time.time()) - 30}
    with pytest.raises(jwt.ExpiredSignatureError):
        jwtoken._project_claims_check(claims)


@pytest.mark.parametrize("extra", [{"atr_sys": True}, {"atr_th": "abc"}])
def test_project_claims_check_refuses_system_and_pat_claims(extra: dict[str, Any]) -> None:
    claims = {"sub": "alice", "atr_prj": "example", "exp": int(time.time()) + 60, **extra}
    with pytest.raises(jwt.InvalidTokenError):
        jwtoken._project_claims_check(claims)


def test_project_scope_is_none_without_the_claim() -> None:
    assert jwtoken.project_scope({"sub": "alice"}) is None


@pytest.mark.parametrize("value", ["", None, 7, ["example"]])
def test_project_scope_refuses_an_unusable_claim(value: object) -> None:
    with pytest.raises(jwt.InvalidTokenError):
        jwtoken.project_scope({"sub": "alice", "atr_prj": value})


@pytest.mark.asyncio
async def test_verify_refuses_a_project_token_with_no_row(stored: _Stored) -> None:
    token = jwtoken.issue_for_project("alice", _PROJECT)

    with pytest.raises(base.ASFQuartException) as exc_info:
        await jwtoken.verify(token.jwt)
    assert exc_info.value.errorcode == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"revoked": True}, {"project_key": "other"}, {"asf_uid": "bob"}])
async def test_verify_refuses_a_project_token_whose_row_disagrees(stored: _Stored, change: dict[str, Any]) -> None:
    token = jwtoken.issue_for_project("alice", _PROJECT)
    await stored(token, **change)

    with pytest.raises(base.ASFQuartException) as exc_info:
        await jwtoken.verify(token.jwt)
    assert exc_info.value.errorcode == 401


@pytest.mark.asyncio
async def test_verify_refuses_a_project_token_whose_row_has_expired(stored: _Stored) -> None:
    token = jwtoken.issue_for_project("alice", _PROJECT)
    await stored(token, expires=int(time.time()) - 1)

    with pytest.raises(jwt.ExpiredSignatureError):
        await jwtoken.verify(token.jwt)


@pytest.mark.asyncio
async def test_verify_takes_a_project_token_with_its_row(stored: _Stored) -> None:
    token = jwtoken.issue_for_project("alice", _PROJECT)
    await stored(token)

    claims = await jwtoken.verify(token.jwt)

    assert jwtoken.project_scope(claims) == "example"


@pytest.fixture
async def stored(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Stored]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)

    async def is_active(_asf_uid: str) -> bool:
        return True

    async def store(token: jwtoken.ProjectToken, **changes: Any) -> None:
        fields = {"jti": token.jti, "project_key": "example", "asf_uid": "alice", "expires": token.expires}
        async with sessionmaker() as data:
            data.add(sql.WorkflowJwt(github_uid="alice-gh", github_nid=1, **(fields | changes)))
            await data.commit()

    monkeypatch.setattr(jwtoken.ldap, "is_active", is_active)
    monkeypatch.setattr(jwtoken.db, "session", sessionmaker)
    yield store
    await engine.dispose()


def _authorize(claims: dict[str, Any], arguments: list[Any]) -> None:
    app = quart.Quart(__name__)

    async def run() -> None:
        async with app.test_request_context("/"):
            quart.g.jwt_claims = claims
            api_auth.authorize_project(api_auth.Auth.BEARER_PROJECT, arguments)

    asyncio.run(run())
