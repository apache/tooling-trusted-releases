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

"""Tests for the API route auth enforcers and scheme distribution.

See issue #1169. The "fail-closed" property is now structural: @api.typed takes
auth_scheme as a required keyword-only argument typed as the Auth enum, so a
route can't be registered without a valid scheme - the type checker enforces it.
The distribution test below is a heads-up against an endpoint's scheme silently
changing.
"""

import pytest

import atr.blueprints.api as api_blueprint
import atr.blueprints.api_auth as api_auth


def test_expected_scheme_distribution() -> None:
    """Sanity check: the headline counts from the #1169 audit still hold.

    A heads-up, not a rigid drift check, that catches obvious regressions like
    "the PAT endpoint accidentally became public".
    """
    # Importing atr.api triggers registration of every route.
    import atr.api  # noqa: F401

    schemes = api_blueprint.route_auth_schemes()
    assert schemes, "no API routes were registered; did atr.api fail to import?"

    counts = {member.value: 0 for member in api_auth.Auth}
    for scheme in schemes.values():
        counts[scheme] += 1

    assert counts["pat"] == 1, f"expected exactly one pat route, got {counts['pat']}"
    assert counts["bearer"] >= 15, f"bearer count dropped unexpectedly: {counts['bearer']}"
    assert counts["body_oidc"] >= 5, f"body_oidc count dropped unexpectedly: {counts['body_oidc']}"
    assert sum(counts.values()) >= 40, f"total route count dropped unexpectedly: {counts}"


@pytest.mark.asyncio
async def test_public_endpoint_accepts_unauthenticated_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public route must respond without any credentials.

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
        f"public endpoint returned 401, which means the public scheme is "
        f"enforcing auth it shouldn't: body={await response.get_data()!r}"
    )


@pytest.mark.asyncio
async def test_bearer_endpoint_rejects_unauthenticated_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bearer route must return 401 without a Bearer token.

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


# Note: there is no HTTP-level negative test for the body_oidc scheme here.
# Driving a body_oidc endpoint through the test client requires the full
# QuartSchema(app, security_schemes=...) initialization that server.py does
# (it populates QUART_SCHEMA_CONVERT_CASING etc.), which the asfquart test
# fixture doesn't replicate. The authenticate_body behaviour is already
# covered at the unit level by:
#   - test_body_oidc_populates_quart_g_with_context (happy path)
#   - test_body_oidc_missing_body_raises_unauthorized
#   - test_body_oidc_rejects_body_without_jwt_fields
# A proper HTTP-level test belongs in the e2e suite where the real app
# initialization runs.


# ---------------------------------------------------------------------------
# TrustedPublisherContext handoff to the handler.
#
# authenticate_body's contract has two halves: reject invalid tokens
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
    """trusted_publisher_context() must raise if called outside a body_oidc route.

    Silently returning ``None`` would let a handler misattribute actions
    to "no one" when authenticate_body hadn't run. Making misuse loud is the
    safer default.
    """
    import asfquart

    monkeypatch.setattr("asfquart.APP", None)

    app = asfquart.construct("test")

    async def _assert_raises() -> None:
        async with app.test_request_context("/"):
            with pytest.raises(RuntimeError, match=r"outside a body_oidc route"):
                api_auth.trusted_publisher_context()

    import asyncio

    asyncio.run(_assert_raises())


@pytest.mark.asyncio
async def test_body_oidc_populates_quart_g_with_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On successful validation, authenticate_body stashes the verified context
    on ``quart.g.tp_context`` and the handler reads it via
    ``trusted_publisher_context()``.

    We stub ``interaction.validate_trusted_jwt`` so the test doesn't hit the
    network or need a real OIDC token. That's the right seam: authenticate_body's
    job is the handoff, not the crypto. Crypto is covered by
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

    app = asfquart.construct("test")
    async with app.test_request_context("/"):
        await api_auth.authenticate_body(api_auth.Auth.BODY_OIDC, FakeBody(publisher="github", jwt="fake.jwt.token"))
        ctx = api_auth.trusted_publisher_context()
        assert ctx.payload is fake_payload
        assert ctx.asf_uid == "alice"
        assert ctx.publisher == "github"


@pytest.mark.asyncio
async def test_body_oidc_missing_body_raises_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body_oidc route with no request body must raise 401."""
    import asfquart
    import asfquart.base as base

    monkeypatch.setattr("asfquart.APP", None)

    app = asfquart.construct("test")
    async with app.test_request_context("/"):
        with pytest.raises(base.ASFQuartException) as excinfo:
            await api_auth.authenticate_body(api_auth.Auth.BODY_OIDC, None)
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

    app = asfquart.construct("test")
    async with app.test_request_context("/"):
        with pytest.raises(base.ASFQuartException) as excinfo:
            await api_auth.authenticate_body(api_auth.Auth.BODY_OIDC, BodyWithoutJwt(name="hi"))
        assert excinfo.value.errorcode == 401


# ---------------------------------------------------------------------------
# interaction.trusted_project_for_payload / trusted_release_for_payload
#
# These helpers sit on the security-relevant path for every body_oidc
# endpoint: they take an already-verified OIDC payload and do the
# project/release/phase lookup. The authenticate_body tests above cover
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
