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

"""Tests for the api.route factory's auth enforcers.

These cover the level-dispatched auth callables the factory composes:
authenticate_header (header-credential, pre-body), authenticate_body
(body-credential, post-body), and the OpenAPI security annotation. The
auth-before-body-parse ordering at the HTTP level is exercised against a
migrated route in the e2e/Phase 2 suite, where the full app initialises.
"""

import asfquart.base as base
import pytest
import quart_schema

import atr.blueprints.api_auth as api_auth
import atr.jwtoken as jwtoken


def test_auth_enum_matches_the_declared_schemes() -> None:
    assert {member.value for member in api_auth.Auth} == {"public", "bearer", "system_bearer", "body_oidc", "pat"}
    assert api_auth.HEADER_SCHEMES == {api_auth.Auth.BEARER, api_auth.Auth.SYSTEM_BEARER}
    assert api_auth.BODY_SCHEMES == {api_auth.Auth.BODY_OIDC}


@pytest.mark.asyncio
async def test_authenticate_body_is_a_noop_for_non_body_levels() -> None:
    # PAT routes validate the credential themselves; nothing to enforce here.
    await api_auth.authenticate_body(api_auth.Auth.PAT, None)
    await api_auth.authenticate_body(api_auth.Auth.PUBLIC, None)


@pytest.mark.asyncio
async def test_authenticate_header_accepts_a_valid_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_authenticate() -> dict[str, object]:
        return {"sub": "alice", "atr_sys": True}

    monkeypatch.setattr(jwtoken, "authenticate", fake_authenticate)

    # Neither call should raise.
    await api_auth.authenticate_header(api_auth.Auth.BEARER)
    await api_auth.authenticate_header(api_auth.Auth.SYSTEM_BEARER)


@pytest.mark.asyncio
async def test_authenticate_header_propagates_token_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_authenticate() -> dict[str, object]:
        raise base.ASFQuartException("Invalid Bearer JWT format", errorcode=401)

    monkeypatch.setattr(jwtoken, "authenticate", fake_authenticate)

    with pytest.raises(base.ASFQuartException) as excinfo:
        await api_auth.authenticate_header(api_auth.Auth.BEARER)
    assert excinfo.value.errorcode == 401


@pytest.mark.asyncio
async def test_authenticate_header_requires_system_claim_for_system_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_authenticate() -> dict[str, object]:
        return {"sub": "alice"}

    monkeypatch.setattr(jwtoken, "authenticate", fake_authenticate)

    with pytest.raises(base.ASFQuartException) as excinfo:
        await api_auth.authenticate_header(api_auth.Auth.SYSTEM_BEARER)
    assert excinfo.value.errorcode == 403


@pytest.mark.asyncio
async def test_header_auth_runs_before_body_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A header-credential route must reject an unauthenticated request before
    its body is validated.

    Uses POST /api/project/config (system_bearer, with a request body). A
    malformed body sent without a Bearer token must return 401 (auth), not a
    400/500 from body validation - proving header auth wraps outside
    validation.
    """
    import asfquart

    import atr.blueprints as blueprints

    monkeypatch.setattr("atr.blueprints._export_routes", lambda _: None)
    monkeypatch.setattr("asfquart.APP", None)

    app = asfquart.construct("test")
    blueprints.register(app)

    client = app.test_client()
    response = await client.post("/api/project/config", json={"not": "a valid body"})
    assert response.status_code == 401, (
        f"expected 401 (auth before body validation), got {response.status_code}: {await response.get_data()!r}"
    )


def test_security_scheme_is_applied_only_for_header_levels() -> None:
    attribute = quart_schema.openapi.QUART_SCHEMA_SECURITY_ATTRIBUTE

    for level in (api_auth.Auth.BEARER, api_auth.Auth.SYSTEM_BEARER):

        async def handler() -> tuple[dict, int]:
            return {}, 200

        decorated = api_auth.security_scheme_for(level)(handler)
        assert hasattr(decorated, attribute), f"{level} should carry the bearer security scheme"

    for level in (api_auth.Auth.PUBLIC, api_auth.Auth.BODY_OIDC, api_auth.Auth.PAT):

        async def handler() -> tuple[dict, int]:
            return {}, 200

        decorated = api_auth.security_scheme_for(level)(handler)
        assert decorated is handler, f"{level} should not annotate a security scheme"
