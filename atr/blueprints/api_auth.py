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
import datetime
import enum
import time
from typing import TYPE_CHECKING, Final

import asfquart.base as base
import jwt as pyjwt
import pydantic
import quart
import quart_schema

import atr.config as config
import atr.errors as errors
import atr.jwtoken as jwtoken
import atr.log as log
import atr.storage as storage
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

_TP_ALERT_APP_EXTENSION: Final[str] = "tp_alert_throttle"
_TP_ALERT_DETAIL_LIMIT: Final[int] = 1000
_TP_ALERT_INTERVAL: Final[float] = 3600.0
_TP_AUTH_TYPE: Final[str] = "trusted_publisher"
_TP_CONTEXT_ATTR: Final[str] = "tp_context"


@dataclasses.dataclass(frozen=True)
class TrustedPublisherContext:
    """Verified Trusted Publisher state exposed to body_oidc handlers."""

    payload: github.TrustedPublisherPayload
    asf_uid: str | None
    publisher: str


@dataclasses.dataclass
class _TrustedPublisherAlertThrottle:
    # Monotonic time of the last warning email per reason, and failures held back since then
    sent: dict[str, float] = dataclasses.field(default_factory=dict)
    held: dict[str, int] = dataclasses.field(default_factory=dict)

    def due(self, reason: str, now: float) -> int | None:
        # Returns the number of failures held back since the last warning when another
        # warning is due for this reason, or None when this failure should be held back
        if (last := self.sent.get(reason)) is not None:
            if now - last < _TP_ALERT_INTERVAL:
                self.held[reason] = self.held.get(reason, 0) + 1
                return None
        held_count = self.held.pop(reason, 0)
        self.sent[reason] = now
        return held_count


@dataclasses.dataclass(frozen=True)
class _TrustedPublisherFailure:
    reason: str
    message: str
    alert: bool


# Checked in order, so subclasses have to come before the classes they extend
# Expired and not-yet-valid tokens are mostly CI retries and clock skew, so they don't alert
_TP_FAILURES: Final[tuple[tuple[type[Exception], _TrustedPublisherFailure], ...]] = (
    (
        pyjwt.ExpiredSignatureError,
        _TrustedPublisherFailure("token_expired", "Trusted Publisher token has expired", False),
    ),
    (
        pyjwt.ImmatureSignatureError,
        _TrustedPublisherFailure("token_not_yet_valid", "Trusted Publisher token is not yet valid", False),
    ),
    (
        pyjwt.InvalidSignatureError,
        _TrustedPublisherFailure("signature_invalid", "Trusted Publisher token signature is invalid", True),
    ),
    (
        pyjwt.InvalidAudienceError,
        _TrustedPublisherFailure("audience_invalid", "Trusted Publisher token audience is invalid", True),
    ),
    (
        pyjwt.InvalidIssuerError,
        _TrustedPublisherFailure("issuer_invalid", "Trusted Publisher token issuer is invalid", True),
    ),
    (
        pyjwt.InvalidTokenError,
        _TrustedPublisherFailure("token_invalid", "Trusted Publisher token is invalid", True),
    ),
    (
        pydantic.ValidationError,
        _TrustedPublisherFailure("claims_invalid", "Trusted Publisher token claims are invalid", True),
    ),
)
_TP_UNSUPPORTED: Final[_TrustedPublisherFailure] = _TrustedPublisherFailure(
    "publisher_unsupported", "Trusted Publisher is not supported", False
)

# The ATR-authored rejections from inside token validation, by status code
# A 502 isn't here because it means the GitHub OIDC endpoint was unavailable, not that auth failed
_TP_REJECTION_REASONS: Final[dict[int, str]] = {401: "token_rejected", 403: "account_unlinked"}


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
    except base.ASFQuartException as exc:
        reason = _TP_REJECTION_REASONS.get(errors.response_status_code(exc))
        if reason is not None:
            await _trusted_publisher_failure_record(_TrustedPublisherFailure(reason, str(exc), True), exc)
        raise
    except interaction.InteractionError as exc:
        await _trusted_publisher_failure_record(_TP_UNSUPPORTED, exc)
        raise base.ASFQuartException(_TP_UNSUPPORTED.message, errorcode=401) from exc
    except (pyjwt.InvalidTokenError, pydantic.ValidationError) as exc:
        failure = _trusted_publisher_failure(exc)
        await _trusted_publisher_failure_record(failure, exc)
        raise base.ASFQuartException(failure.message, errorcode=401) from exc

    if config.get().ADMIN_ONLY and (not user.is_admin(asf_uid)):
        log.auth_failure(_TP_AUTH_TYPE, "admin_only", asf_uid)
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


def _tp_alert_throttle() -> _TrustedPublisherAlertThrottle:
    # Created on first use, so each app (and each test app) starts with an empty throttle
    extensions = quart.current_app.extensions
    throttle = extensions.get(_TP_ALERT_APP_EXTENSION)
    if not isinstance(throttle, _TrustedPublisherAlertThrottle):
        throttle = _TrustedPublisherAlertThrottle()
        extensions[_TP_ALERT_APP_EXTENSION] = throttle
    return throttle


def _trusted_publisher_failure(exc: Exception) -> _TrustedPublisherFailure:
    for exc_type, failure in _TP_FAILURES:
        if isinstance(exc, exc_type):
            return failure
    raise TypeError(f"No Trusted Publisher failure for {type(exc).__name__}")


async def _trusted_publisher_failure_record(failure: _TrustedPublisherFailure, exc: Exception) -> None:
    # The full exception goes to the log and the warning email, never to the caller
    log.auth_failure(_TP_AUTH_TYPE, failure.reason)
    log.info(f"Trusted Publisher auth failed ({failure.reason}): {errors.message(exc)}")
    if not failure.alert:
        return
    held = _tp_alert_throttle().due(failure.reason, time.monotonic())
    if held is None:
        return
    detail = errors.message(exc)[:_TP_ALERT_DETAIL_LIMIT]
    failed = datetime.datetime.now(datetime.UTC)
    try:
        async with storage.write_as_system(storage.WriteAsTrustedPublisherAlertService) as wats:
            await wats.tokens_notify_trusted_publisher_failure(
                failure.reason, detail, failed, quart.request.remote_addr, held
            )
    except Exception:
        # The caller still has to get its 401, so a warning that can't be queued is only logged
        log.exception("Could not queue the Trusted Publisher failure warning")
