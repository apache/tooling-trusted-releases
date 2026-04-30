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

"""Explicit authentication level decorators for API endpoints.

Every route decorated with :func:`atr.blueprints.api.typed` must also be
decorated with exactly one of the auth level decorators in this module.
The :func:`atr.blueprints.api.typed` decorator enforces this at import
time; forgetting the decorator raises :class:`TypeError` and the server
will not start.

Usage::

    @api.typed
    @api.auth.bearer
    @quart_schema.validate_response(models.api.FooResults, 200)
    async def foo(...):
        ...

Decorator order: ``@api.typed`` on the outside, then ``@api.auth.<level>``,
then any ``@quart_schema`` or ``@rate_limiter`` decorators closer to the
function.

The levels are:

- ``public``     No authentication. Route may be called by anyone.
- ``bearer``     ATR-issued JWT in an ``Authorization: Bearer ...`` header.
- ``body_oidc``  Trusted Publisher OIDC token carried in the request body.
- ``pat``        Personal Access Token in the request body, exchanged for a JWT.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

import quart_schema

import atr.jwtoken as jwtoken

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from typing import Any


AuthLevel = Literal["public", "bearer", "body_oidc", "pat"]

# Public name so external tests and the typed() enforcer agree.
AUTH_LEVEL_ATTR: Final[str] = "_api_auth_level"

VALID_LEVELS: Final[frozenset[AuthLevel]] = frozenset({"public", "bearer", "body_oidc", "pat"})


def bearer[**P, R](
    func: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Awaitable[R]]:
    """Require an ATR-issued Bearer JWT in the ``Authorization`` header.

    Folds in two concerns that previously had to be declared separately
    on every bearer endpoint:

    1. ``@jwtoken.require`` — validates the JWT and populates
       ``quart.g.jwt_claims`` so handlers can read the caller's identity
       via ``_jwt_asf_uid()``.
    2. ``@quart_schema.security_scheme([{"BearerAuth": []}])`` — advertises
       the scheme in the generated OpenAPI document.

    The auth-level marker is applied to the outermost wrapper so
    :func:`atr.blueprints.api.typed` can read it back.
    """
    wrapped = jwtoken.require(func)
    wrapped = quart_schema.security_scheme([{"BearerAuth": []}])(wrapped)
    _mark("bearer", wrapped)
    return wrapped


def body_oidc[F: Callable[..., Awaitable[Any]]](func: F) -> F:
    """Marker for routes authenticated by a Trusted Publisher OIDC token in the body.

    PR 1: marker only. The handler still calls ``interaction.trusted_jwt(...)``
    or ``interaction.validate_trusted_jwt(...)`` itself. PR 3 moves the
    validation into the decorator.
    """
    return _mark("body_oidc", func)


def pat[F: Callable[..., Awaitable[Any]]](func: F) -> F:
    """Marker for routes that accept a Personal Access Token in the body.

    PR 1: marker only. Currently applies to ``jwt_create`` only, which
    exchanges a PAT for an ATR-issued JWT. PR 3 may move the PAT validation
    into the decorator once we have a second caller to design against.
    """
    return _mark("pat", func)


def public[F: Callable[..., Awaitable[Any]]](func: F) -> F:
    """Mark an API route as public (no authentication required).

    This is a marker only: it has no runtime effect. Its purpose is to
    make "this route is intentionally public" a visible, grep-able,
    diffable statement in the source, and to let the import-time enforcer
    in ``typed()`` tell the difference between "intentionally public" and
    "someone forgot to add an auth decorator".
    """
    return _mark("public", func)


def _mark[F: Callable[..., Any]](level: AuthLevel, func: F) -> F:
    """Attach the auth-level sentinel to ``func``.

    Raises TypeError if ``func`` has already been marked with a different
    level (i.e. someone stacked two auth decorators).
    """
    existing = getattr(func, AUTH_LEVEL_ATTR, None)
    if (existing is not None) and (existing != level):
        raise TypeError(
            f"{getattr(func, '__name__', func)!r}: cannot apply "
            f"@api.auth.{level} on top of @api.auth.{existing}. "
            "A route may declare exactly one auth level."
        )
    setattr(func, AUTH_LEVEL_ATTR, level)
    return func
