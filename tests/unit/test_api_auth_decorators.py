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

"""Tests that every API route declares an explicit authentication level.

See issue #1169. These tests are the enforcement mechanism for the
"fail-closed" property: a new route that forgets an auth decorator
either fails to import (the ``typed()`` enforcer) or fails this test
(the coverage assertion).
"""

from typing import Literal

import pytest

import atr.blueprints.api as api_blueprint
import atr.blueprints.api_auth as api_auth


def test_every_api_route_has_an_auth_level() -> None:
    """Every function registered via @api.typed must carry an auth level.

    This is the coverage test. If it fails, some route somewhere in
    ``atr/api/__init__.py`` is missing an ``@api.auth.<level>`` decorator.
    (The ``typed()`` decorator also catches this at import time, so a
    failure here usually means a bug in the enforcer itself or in the
    ordering of decorators.)
    """
    # Importing atr.api triggers registration of every route.
    import atr.api  # noqa: F401

    levels = api_blueprint.route_auth_levels()
    assert levels, "no API routes were registered; did atr.api fail to import?"

    invalid = {name: lvl for name, lvl in levels.items() if lvl not in api_auth.VALID_LEVELS}
    assert not invalid, f"routes with invalid auth level: {invalid}"


def test_typed_rejects_route_without_auth_decorator() -> None:
    """A route missing any ``@api.auth.*`` decorator must fail at import time.

    This is the fail-closed guarantee. A developer adding a new endpoint
    and forgetting the auth decorator gets a TypeError from ``typed()``
    before the server can even start.
    """

    # Minimal stand-in for a real route. No auth decorator applied.
    async def fake_route(_fake: Literal["fake"]) -> tuple[dict, int]:
        return {}, 200

    with pytest.raises(TypeError, match="missing an auth decorator"):
        api_blueprint.typed(fake_route)


def test_typed_accepts_route_with_each_auth_level() -> None:
    """Each of the four auth levels must satisfy ``typed()``'s check."""
    for level in ("public", "bearer", "body_oidc", "pat"):

        async def fake_route(_fake: Literal["fake"]) -> tuple[dict, int]:
            return {}, 200

        decorator = getattr(api_auth, level)
        marked = decorator(fake_route)
        # typed() does more than just the auth check (URL rule registration,
        # etc.), so we can't call it here without a full Quart app context.
        # But we can confirm the marker attribute made it through, which is
        # what typed() keys off.
        assert getattr(marked, api_auth.AUTH_LEVEL_ATTR, None) == level, (
            f"@api.auth.{level} did not set {api_auth.AUTH_LEVEL_ATTR!r}"
        )


def test_auth_levels_cannot_be_stacked() -> None:
    """Applying two different auth decorators to the same route must fail."""

    async def fake_route(_fake: Literal["fake"]) -> tuple[dict, int]:
        return {}, 200

    marked = api_auth.public(fake_route)
    with pytest.raises(TypeError, match=r"cannot apply @api\.auth"):
        api_auth.bearer(marked)


def test_applying_same_auth_level_twice_is_idempotent() -> None:
    """Re-applying the same level must not raise; it's a harmless no-op."""

    async def fake_route(_fake: Literal["fake"]) -> tuple[dict, int]:
        return {}, 200

    first = api_auth.public(fake_route)
    second = api_auth.public(first)
    assert getattr(second, api_auth.AUTH_LEVEL_ATTR) == "public"


def test_expected_level_distribution() -> None:
    """Sanity check: the headline counts from the #1169 audit hold.

    18 bearer, 7 body_oidc, 1 pat, 20 public = 46 total.

    Not meant as a rigid drift check (that's the registry test below), but a
    heads-up that catches obvious regressions like "the PAT endpoint
    accidentally got marked public".
    """
    import atr.api  # noqa: F401

    levels = api_blueprint.route_auth_levels()
    counts = {lvl: 0 for lvl in api_auth.VALID_LEVELS}
    for lvl in levels.values():
        counts[lvl] += 1

    assert counts["pat"] == 1, f"expected exactly one @api.auth.pat route, got {counts['pat']}"
    assert counts["bearer"] >= 15, f"bearer count dropped unexpectedly: {counts['bearer']}"
    assert counts["body_oidc"] >= 5, f"body_oidc count dropped unexpectedly: {counts['body_oidc']}"
    assert sum(counts.values()) >= 40, f"total route count dropped unexpectedly: {counts}"


@pytest.mark.asyncio
async def test_public_endpoint_accepts_unauthenticated_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A @api.auth.public route must respond without any credentials.

    Uses ``/api/committees/list`` as the representative public endpoint
    because it takes no path parameters and no body.
    """
    import asfquart

    import atr.blueprints as blueprints

    monkeypatch.setattr("atr.blueprints._export_routes", lambda _: None)
    monkeypatch.setattr("asfquart.APP", None)

    app = asfquart.construct("test")
    blueprints.register(app)

    client = app.test_client()
    response = await client.get("/api/committees/list")
    # 200 on success, 500 if DB isn't reachable in the test env - either way
    # we know we passed authentication, which is what we're testing.
    assert response.status_code != 401, (
        f"public endpoint returned 401, which means @api.auth.public is "
        f"enforcing auth it shouldn't: body={await response.get_data()!r}"
    )


@pytest.mark.asyncio
async def test_bearer_endpoint_rejects_unauthenticated_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A @api.auth.bearer route must return 401 without a Bearer token.

    Uses ``/api/user/info`` as the representative bearer endpoint: simple
    GET, no path or body params to fabricate.
    """
    import asfquart

    import atr.blueprints as blueprints

    monkeypatch.setattr("atr.blueprints._export_routes", lambda _: None)
    monkeypatch.setattr("asfquart.APP", None)

    app = asfquart.construct("test")
    blueprints.register(app)

    client = app.test_client()
    response = await client.get("/api/user/info")
    assert response.status_code == 401, (
        f"bearer endpoint did not return 401 without credentials: status={response.status_code}"
    )


# Note: there is no HTTP-level negative test for @api.auth.body_oidc here.
# Driving a body_oidc endpoint through the test client requires the full
# QuartSchema(app, security_schemes=...) initialization that server.py does
# (it populates QUART_SCHEMA_CONVERT_CASING etc.), which the asfquart test
# fixture doesn't replicate. The decorator's auth behavior is already
# covered at the unit level by:
#   - test_body_oidc_populates_quart_g_with_context (happy path)
#   - test_body_oidc_missing_body_raises_bad_request
#   - test_body_oidc_rejects_body_without_jwt_fields
# A proper HTTP-level test belongs in the e2e suite where the real app
# initialization runs.


# ---------------------------------------------------------------------------
# TrustedPublisherContext handoff from decorator to handler.
#
# The body_oidc decorator's contract has two halves: reject invalid tokens
# (covered above), and make the verified payload available to the handler.
# The tests below cover the second half.
# ---------------------------------------------------------------------------


def _fake_trusted_payload(**overrides: object) -> object:
    """Build a minimally-valid TrustedPublisherPayload for tests.

    The model has many required fields but none that the context-handoff
    tests care about beyond the few we reference directly.
    """
    import atr.models.github as github

    defaults = {
        "actor": "test-user",
        "actor_id": 12345,
        "aud": "https://atr.example/",
        "base_ref": "",
        "check_run_id": "1",
        "enterprise": "the-asf",
        "enterprise_id": "212555",
        "event_name": "workflow_dispatch",
        "head_ref": "",
        "iat": 0,
        "iss": "https://token.actions.githubusercontent.com",
        "job_workflow_ref": "apache/test/.github/workflows/x.yml@refs/heads/main",
        "job_workflow_sha": "0" * 40,
        "jti": "test-jti",
        "ref": "refs/heads/main",
        "ref_protected": "false",
        "ref_type": "branch",
        "repository": "apache/test",
        "repository_owner": "apache",
        "repository_visibility": "public",
        "run_attempt": "1",
        "run_number": "1",
        "runner_environment": "github-hosted",
        "sha": "0" * 40,
        "sub": "repo:apache/test:ref:refs/heads/main",
        "workflow": "test",
        "workflow_ref": "apache/test/.github/workflows/x.yml@refs/heads/main",
        "workflow_sha": "0" * 40,
    }
    defaults.update(overrides)
    return github.TrustedPublisherPayload.model_validate(defaults)


def test_trusted_publisher_context_raises_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trusted_publisher_context() must raise if called outside a body_oidc handler.

    Silently returning ``None`` would let a handler misattribute actions
    to "no one" when the decorator hadn't run. Making misuse loud is the
    safer default.
    """
    import asfquart

    monkeypatch.setattr("asfquart.APP", None)

    app = asfquart.construct("test")

    async def _assert_raises() -> None:
        async with app.test_request_context("/"):
            with pytest.raises(RuntimeError, match=r"outside a @api\.auth\.body_oidc"):
                api_auth.trusted_publisher_context()

    import asyncio

    asyncio.run(_assert_raises())


@pytest.mark.asyncio
async def test_body_oidc_populates_quart_g_with_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On successful validation, @api.auth.body_oidc stashes the verified
    context on ``quart.g.tp_context`` and the handler reads it via
    ``trusted_publisher_context()``.

    We stub ``interaction.validate_trusted_jwt`` so the test doesn't hit
    the network or need a real OIDC token. That's the right seam: the
    decorator's job is the handoff, not the crypto. Crypto is covered by
    ``jwtoken.verify_github_oidc``'s own tests.
    """
    import asfquart
    import pydantic

    import atr.db.interaction as interaction

    monkeypatch.setattr("asfquart.APP", None)

    fake_payload = _fake_trusted_payload()

    async def fake_validate(publisher: str, jwt: str) -> tuple[object, str | None]:
        assert publisher == "github"
        assert jwt == "fake.jwt.token"
        return fake_payload, "alice"

    monkeypatch.setattr(interaction, "validate_trusted_jwt", fake_validate)

    class FakeBody(pydantic.BaseModel):
        publisher: str
        jwt: str

    captured: dict[str, object] = {}

    @api_auth.body_oidc
    async def handler(data: FakeBody) -> tuple[dict, int]:
        ctx = api_auth.trusted_publisher_context()
        captured["payload"] = ctx.payload
        captured["asf_uid"] = ctx.asf_uid
        captured["publisher"] = ctx.publisher
        return {}, 200

    app = asfquart.construct("test")
    async with app.test_request_context("/"):
        await handler(data=FakeBody(publisher="github", jwt="fake.jwt.token"))

    assert captured["payload"] is fake_payload
    assert captured["asf_uid"] == "alice"
    assert captured["publisher"] == "github"


@pytest.mark.asyncio
async def test_body_oidc_missing_body_raises_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body_oidc handler without a ``data`` kwarg must raise 401."""
    import asfquart
    import asfquart.base as base

    monkeypatch.setattr("asfquart.APP", None)

    @api_auth.body_oidc
    async def handler() -> tuple[dict, int]:
        return {}, 200

    app = asfquart.construct("test")
    async with app.test_request_context("/"):
        with pytest.raises(base.ASFQuartException) as excinfo:
            await handler()
        assert excinfo.value.errorcode == 401


@pytest.mark.asyncio
async def test_body_oidc_rejects_body_without_jwt_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body without publisher+jwt string fields must be rejected with 401."""
    import asfquart
    import asfquart.base as base
    import pydantic

    monkeypatch.setattr("asfquart.APP", None)

    class BodyWithoutJwt(pydantic.BaseModel):
        name: str

    @api_auth.body_oidc
    async def handler(data: BodyWithoutJwt) -> tuple[dict, int]:
        return {}, 200

    app = asfquart.construct("test")
    async with app.test_request_context("/"):
        with pytest.raises(base.ASFQuartException) as excinfo:
            await handler(data=BodyWithoutJwt(name="hi"))
        assert excinfo.value.errorcode == 401


# ---------------------------------------------------------------------------
# interaction.trusted_project_for_payload / trusted_release_for_payload
#
# These helpers sit on the security-relevant path for every body_oidc
# endpoint: they take an already-verified OIDC payload and do the
# project/release/phase lookup. The decorator-level tests above cover
# the auth edge; these cover the post-auth lookup edge.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trusted_project_for_payload_rejects_missing_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the OIDC token carries no ASF UID (i.e. it's a trusted-role
    token, not a per-user token), ``trusted_project_for_payload`` must
    refuse. That refusal is what keeps the publisher/* endpoints from
    being callable by ATR's own workflow tokens."""
    import atr.db.interaction as interaction

    payload = _fake_trusted_payload()
    with pytest.raises(interaction.InteractionError, match="ASF user not found"):
        await interaction.trusted_project_for_payload(
            payload,
            None,
            interaction.TrustedProjectPhase.COMPOSE,
        )


@pytest.mark.asyncio
async def test_trusted_release_for_payload_rejects_user_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The distribute/* endpoints require a trusted-role token (asf_uid
    in the payload is None); a per-user token must be refused. Mirrors
    the check that ``trusted_jwt_for_dist`` performs on live tokens."""
    import atr.db.interaction as interaction
    import atr.models.safe as safe

    with pytest.raises(interaction.InteractionError, match="Must use Trusted Publishing"):
        await interaction.trusted_release_for_payload(
            payload_asf_uid="bob",  # non-None => user token => reject
            asserted_asf_uid="alice",
            phase=interaction.TrustedProjectPhase.COMPOSE,
            project_key=safe.ProjectKey("example"),
            version_key=safe.VersionKey("1.0.0"),
        )


@pytest.mark.asyncio
async def test_trusted_project_for_payload_delegates_to_project_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: with an ASF UID and a verified payload, the helper
    calls through to ``_trusted_project`` and returns its result.

    Stubs ``_trusted_project`` so the test doesn't need a database. What
    we're asserting is that the helper does the null check and then
    delegates — not the project resolution itself, which has its own
    tests in interaction.py's existing suite.
    """
    import atr.db.interaction as interaction

    payload = _fake_trusted_payload(repository="apache/myproj")
    sentinel = object()

    async def fake_trusted_project(repository, workflow_ref, phase):
        assert repository == "apache/myproj"
        assert phase is interaction.TrustedProjectPhase.VOTE
        return sentinel

    monkeypatch.setattr(interaction, "_trusted_project", fake_trusted_project)

    asf_uid, project = await interaction.trusted_project_for_payload(
        payload,
        "alice",
        interaction.TrustedProjectPhase.VOTE,
    )
    assert asf_uid == "alice"
    assert project is sentinel


@pytest.mark.asyncio
async def test_trusted_release_for_payload_delegates_to_dist_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path for the dist variant: with no ASF UID in the payload
    (i.e. a trusted-role token), the helper delegates the phase/project/
    release lookup to ``_trusted_dist_lookup`` using the caller-asserted
    ASF UID from the body.
    """
    import atr.db.interaction as interaction
    import atr.models.safe as safe

    project_sentinel = object()
    release_sentinel = object()

    async def fake_lookup(asf_uid, phase, project_key, version_key):
        assert asf_uid == "alice"
        assert phase is interaction.TrustedProjectPhase.COMPOSE
        return project_sentinel, release_sentinel

    monkeypatch.setattr(interaction, "_trusted_dist_lookup", fake_lookup)

    project, release = await interaction.trusted_release_for_payload(
        payload_asf_uid=None,  # trusted-role token
        asserted_asf_uid="alice",
        phase=interaction.TrustedProjectPhase.COMPOSE,
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("1.0.0"),
    )
    assert project is project_sentinel
    assert release is release_sentinel
