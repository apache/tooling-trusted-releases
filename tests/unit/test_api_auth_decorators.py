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

    Not meant as a rigid drift check (that's PR 3's registry file), but a
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
