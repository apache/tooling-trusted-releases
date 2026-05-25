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

"""Authentication level decorators for API endpoints. See #1169."""

from __future__ import annotations

import dataclasses
import functools
from typing import TYPE_CHECKING, Final, Literal

import asfquart.base as base
import jwt as pyjwt
import pydantic
import quart
import quart_schema

import atr.jwtoken as jwtoken

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from typing import Any

    import atr.models.github as github


AuthLevel = Literal["public", "bearer", "system_bearer", "body_oidc", "pat"]

AUTH_LEVEL_ATTR: Final[str] = "_api_auth_level"

VALID_LEVELS: Final[frozenset[AuthLevel]] = frozenset({"public", "bearer", "system_bearer", "body_oidc", "pat"})

_TP_CONTEXT_ATTR: Final[str] = "tp_context"


@dataclasses.dataclass(frozen=True)
class TrustedPublisherContext:
    """Verified Trusted Publisher state exposed to body_oidc handlers."""

    payload: github.TrustedPublisherPayload
    asf_uid: str | None
    publisher: str


def bearer[**P, R](
    func: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Awaitable[R]]:
    """Require an ATR-issued Bearer JWT in the Authorization header."""
    wrapped = jwtoken.require(func)
    wrapped = quart_schema.security_scheme([{"BearerAuth": []}])(wrapped)
    _mark("bearer", wrapped)
    return wrapped


def body_oidc[**P, R](
    func: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Awaitable[R]]:
    """Validate a Trusted Publisher OIDC token carried in the request body."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        data = kwargs.get("data")
        if data is None:
            raise base.ASFQuartException(
                "Trusted Publisher auth requires a validated request body",
                errorcode=401,
            )
        publisher = getattr(data, "publisher", None)
        jwt = getattr(data, "jwt", None)
        if (not isinstance(publisher, str)) or (not isinstance(jwt, str)):
            raise base.ASFQuartException(
                "Trusted Publisher auth requires 'publisher' and 'jwt' string fields",
                errorcode=401,
            )

        # Lazy import to match the project convention: atr.db.interaction
        # pulls in much of the project, and other blueprint modules don't
        # import it at top level either.
        import atr.db.interaction as interaction

        try:
            payload, asf_uid = await interaction.validate_trusted_jwt(publisher, jwt)
        except base.ASFQuartException:
            # Already carries an HTTP status code (401 for credential
            # failures, 502 for upstream failures); let it propagate.
            raise
        except (interaction.InteractionError, pyjwt.InvalidTokenError, pydantic.ValidationError) as exc:
            # Credential-shaped failures: unsupported publisher, malformed
            # or expired JWT, payload that doesn't match the expected
            # TrustedPublisherPayload schema. All map to 401.
            raise base.ASFQuartException(f"Trusted Publisher auth failed: {exc}", errorcode=401) from exc

        quart.g.tp_context = TrustedPublisherContext(
            payload=payload,
            asf_uid=asf_uid,
            publisher=publisher,
        )
        return await func(*args, **kwargs)

    _mark("body_oidc", wrapper)
    return wrapper


def pat[F: Callable[..., Awaitable[Any]]](func: F) -> F:
    """Marker for routes that accept a PAT in the body (jwt_create only)."""
    return _mark("pat", func)


def public[F: Callable[..., Awaitable[Any]]](func: F) -> F:
    """Marker for routes that require no authentication."""
    return _mark("public", func)


def system_bearer[**P, R](
    func: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Awaitable[R]]:
    """Require an ATR-issued Bearer JWT with system privileges (from a system PAT).

    Ordinary user JWTs are rejected.
    """

    @functools.wraps(func)
    async def checked(*args: P.args, **kwargs: P.kwargs) -> R:
        claims = getattr(quart.g, "jwt_claims", None)
        if not (isinstance(claims, dict) and claims.get("atr_sys")):
            raise base.ASFQuartException("System privileges required", errorcode=403)
        return await func(*args, **kwargs)

    # require() verifies the JWT and sets quart.g.jwt_claims before checked runs.
    wrapped = jwtoken.require(checked)
    wrapped = quart_schema.security_scheme([{"BearerAuth": []}])(wrapped)
    _mark("system_bearer", wrapped)
    return wrapped


def trusted_publisher_context() -> TrustedPublisherContext:
    """Return the TrustedPublisherContext placed by @body_oidc on quart.g."""
    ctx = getattr(quart.g, _TP_CONTEXT_ATTR, None)
    if ctx is None:
        raise RuntimeError("trusted_publisher_context() called outside a @api.auth.body_oidc handler")
    return ctx


def _mark[F: Callable[..., Any]](level: AuthLevel, func: F) -> F:
    """Attach the auth-level sentinel to func; reject re-marking with a different level."""
    existing = getattr(func, AUTH_LEVEL_ATTR, None)
    if (existing is not None) and (existing != level):
        raise TypeError(
            f"{getattr(func, '__name__', func)!r}: cannot apply @api.auth.{level} on top of @api.auth.{existing}"
        )
    setattr(func, AUTH_LEVEL_ATTR, level)
    return func
