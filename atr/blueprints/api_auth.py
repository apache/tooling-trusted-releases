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

"""Authentication levels and enforcers for API routes. See #1169.

The Auth enum is the auth_scheme passed to @api.typed; authenticate_header and
authenticate_body are the enforcers the route factory calls.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import TYPE_CHECKING, Final

import asfquart.base as base
import jwt as pyjwt
import pydantic
import quart
import quart_schema

import atr.config as config
import atr.jwtoken as jwtoken
import atr.user as user

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    import atr.models.github as github


class Auth(enum.StrEnum):
    PUBLIC = "public"
    BEARER = "bearer"
    SYSTEM_BEARER = "system_bearer"
    BODY_OIDC = "body_oidc"
    PAT = "pat"


# Header-credential schemes authenticate from the Authorization header, so they
# run before the body is parsed. Body-credential schemes carry the credential in
# the request body, so they run after parsing. PUBLIC and PAT enforce nothing
# here (PAT routes validate the PAT from the body themselves).
HEADER_SCHEMES: Final[frozenset[Auth]] = frozenset({Auth.BEARER, Auth.SYSTEM_BEARER})
BODY_SCHEMES: Final[frozenset[Auth]] = frozenset({Auth.BODY_OIDC})

_TP_CONTEXT_ATTR: Final[str] = "tp_context"


@dataclasses.dataclass(frozen=True)
class TrustedPublisherContext:
    """Verified Trusted Publisher state exposed to body_oidc handlers."""

    payload: github.TrustedPublisherPayload
    asf_uid: str | None
    publisher: str


async def authenticate_body(scheme: Auth, data: Any) -> None:
    # The validated request body carries the publisher and OIDC JWT.
    if scheme is not Auth.BODY_OIDC:
        return
    if data is None:
        raise base.ASFQuartException("Trusted Publisher auth requires a validated request body", errorcode=401)
    publisher = getattr(data, "publisher", None)
    jwt = getattr(data, "jwt", None)
    if (not isinstance(publisher, str)) or (not isinstance(jwt, str)):
        raise base.ASFQuartException(
            "Trusted Publisher auth requires 'publisher' and 'jwt' string fields", errorcode=401
        )
    # Lazy import: atr.db.interaction pulls in much of the project, so blueprint
    # modules don't import it at the top level.
    import atr.db.interaction as interaction

    try:
        payload, asf_uid = await interaction.validate_trusted_jwt(publisher, jwt)
    except base.ASFQuartException:
        raise
    except (interaction.InteractionError, pyjwt.InvalidTokenError, pydantic.ValidationError) as exc:
        raise base.ASFQuartException(f"Trusted Publisher auth failed: {exc}", errorcode=401) from exc

    if config.get().ADMIN_ONLY and (not user.is_admin(asf_uid)):
        raise base.ASFQuartException("ATR is currently available to administrators only", errorcode=403)

    quart.g.tp_context = TrustedPublisherContext(payload=payload, asf_uid=asf_uid, publisher=publisher)


async def authenticate_header(scheme: Auth) -> None:
    # Header-credential auth: verify the Bearer JWT.
    claims = await jwtoken.authenticate()
    if (scheme is Auth.SYSTEM_BEARER) and (not claims.get("atr_sys")):
        raise base.ASFQuartException("System privileges required", errorcode=403)


def security_scheme_for(scheme: Auth) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    # Bearer security scheme for header-credential schemes; no-op otherwise.
    if scheme in HEADER_SCHEMES:
        return quart_schema.security_scheme([{"BearerAuth": []}])
    return lambda func: func


def trusted_publisher_context() -> TrustedPublisherContext:
    """Return the TrustedPublisherContext that authenticate_body placed on quart.g."""
    ctx = getattr(quart.g, _TP_CONTEXT_ATTR, None)
    if ctx is None:
        raise RuntimeError("trusted_publisher_context() called outside a body_oidc route")
    return ctx
