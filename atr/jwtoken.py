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

from __future__ import annotations

import datetime as datetime
import functools
import secrets as secrets
from typing import TYPE_CHECKING, Any, Final

import aiohttp
import asfquart.base as base
import jwt
import quart

import atr.config as config
import atr.ldap as ldap
import atr.log as log
import atr.models.github as github
import atr.util as util

_ALGORITHM: Final[str] = "HS256"
_ATR_JWT_AUDIENCE: Final[str] = "atr-api-pat-test-v1"
_ATR_JWT_ISSUER: Final[str] = f"https://{config.get().APP_HOST}/"
_ATR_JWT_TTL: Final[int] = 30 * 60
_GITHUB_OIDC_AUDIENCE: Final[str] = "atr-test-v1"
_GITHUB_OIDC_EXPECTED: Final[dict[str, str]] = {
    "enterprise": "the-asf",
    "enterprise_id": "212555",
    "repository_owner": "apache",
    "runner_environment": "github-hosted",
}
_GITHUB_OIDC_ISSUER: Final[str] = "https://token.actions.githubusercontent.com"
_JWT_SECRET_KEY: Final[str] = config.get().JWT_SECRET_KEY

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine


def issue(uid: str, *, ttl: int = _ATR_JWT_TTL) -> str:
    now = datetime.datetime.now(tz=datetime.UTC)
    payload = {
        "sub": uid,
        "iss": _ATR_JWT_ISSUER,
        "aud": _ATR_JWT_AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + datetime.timedelta(seconds=ttl),
        "jti": secrets.token_hex(128 // 8),
    }
    return jwt.encode(payload, _JWT_SECRET_KEY, algorithm=_ALGORITHM)


def require[**P, R](func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Awaitable[R]]:
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        token = _extract_bearer_token(quart.request)
        try:
            claims = await verify(token)
        except jwt.ExpiredSignatureError as exc:
            log.failed_authentication("jwt_token_expired")
            raise base.ASFQuartException("Token has expired", errorcode=401) from exc
        except jwt.InvalidSignatureError as exc:
            log.failed_authentication("jwt_signature_invalid")
            raise base.ASFQuartException("Token signature verification failed", errorcode=401) from exc
        except jwt.InvalidTokenError as exc:
            log.failed_authentication("jwt_token_invalid")
            raise base.ASFQuartException("Invalid Bearer JWT format", errorcode=401) from exc
        except jwt.PyJWTError as exc:
            log.failed_authentication("jwt_token_invalid_2")
            raise base.ASFQuartException(f"Invalid Bearer JWT: {exc}", errorcode=401) from exc

        quart.g.jwt_claims = claims
        return await func(*args, **kwargs)

    return wrapper


async def verify(token: str) -> dict[str, Any]:
    # Grab the "supposed" asf UID from the token presented, to make sure we know who failed to authenticate on failure.
    claims_unsafe = jwt.decode(token, options={"verify_signature": False}, algorithms=[_ALGORITHM])
    asf_uid = claims_unsafe.get("sub")
    log.set_asf_uid(asf_uid)
    claims = jwt.decode(
        token,
        _JWT_SECRET_KEY,
        algorithms=[_ALGORITHM],
        issuer=_ATR_JWT_ISSUER,
        audience=_ATR_JWT_AUDIENCE,
        options={"require": ["sub", "iss", "aud", "iat", "exp", "jti"]},
    )
    log.debug(f"JWT claims: {claims}")
    if not isinstance(asf_uid, str):
        log.failed_authentication("jwt_subject_invalid")
        raise jwt.InvalidTokenError("Invalid Bearer JWT subject")
    if not await ldap.is_active(asf_uid):
        log.failed_authentication("account_deleted_or_banned")
        raise base.ASFQuartException("Account is disabled", errorcode=401)
    return claims


async def verify_github_oidc(token: str) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    dangerous_headers = {"jku", "x5u", "jwk"}
    if dangerous_headers.intersection(header.keys()):
        raise base.ASFQuartException("JWT contains disallowed headers", errorcode=401)
    try:
        async with util.create_secure_session() as session:
            r = await session.get(
                f"{_GITHUB_OIDC_ISSUER}/.well-known/openid-configuration",
                timeout=aiohttp.ClientTimeout(total=10),
            )
            r.raise_for_status()
            jwks_uri = (await r.json())["jwks_uri"]
    except aiohttp.ClientSSLError as exc:
        log.error(f"TLS failure fetching OIDC config: {exc}")
        raise base.ASFQuartException(
            f"TLS verification failed for GitHub OIDC endpoint: {exc}",
            errorcode=502,
        ) from exc
    except aiohttp.ClientConnectionError as exc:
        log.error(f"Failed to connect to GitHub OIDC endpoint: {exc}")
        raise base.ASFQuartException(
            f"Failed to connect to GitHub OIDC endpoint: {exc}",
            errorcode=502,
        ) from exc
    except aiohttp.ClientResponseError as exc:
        log.error(f"GitHub OIDC endpoint returned HTTP {exc.status}: {exc.message}")
        raise base.ASFQuartException(
            f"GitHub OIDC endpoint returned HTTP {exc.status}: {exc.message}",
            errorcode=502,
        ) from exc
    except (aiohttp.ServerTimeoutError, aiohttp.ClientError) as exc:
        log.warning(f"Failed to fetch OIDC config: {exc}")
        jwks_uri = f"{_GITHUB_OIDC_ISSUER}/.well-known/jwks"

    jwks_client = jwt.PyJWKClient(jwks_uri)
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        key=signing_key.key,
        algorithms=["RS256"],
        audience=_GITHUB_OIDC_AUDIENCE,
        issuer=_GITHUB_OIDC_ISSUER,
        options={"require": ["exp", "iat"]},
    )
    for key, value in _GITHUB_OIDC_EXPECTED.items():
        if payload[key] != value:
            raise base.ASFQuartException(
                f"GitHub OIDC payload mismatch: {key} = {payload[key]} != {value}",
                errorcode=401,
            )
    return github.TrustedPublisherPayload.model_validate(payload).model_dump()


def _extract_bearer_token(request: quart.Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if (scheme.lower() != "bearer") or (not token):
        raise base.ASFQuartException(
            "Authentication required. Please provide a valid Bearer token in the Authorization header", errorcode=401
        )
    return token
