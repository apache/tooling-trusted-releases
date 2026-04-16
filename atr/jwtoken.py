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
import os
import pathlib
import secrets as secrets
import urllib.parse as parse
from typing import TYPE_CHECKING, Any, Final

import aiohttp
import asfquart
import asfquart.base as base
import jwt
import quart

import atr.config as config
import atr.db as db
import atr.ldap as ldap
import atr.log as log
import atr.models.github as github
import atr.util as util

_ALGORITHM: Final[str] = "HS256"
_ATR_JWT_AUDIENCE: Final[str] = f"https://{config.get().APP_HOST}/"
_ATR_JWT_ISSUER: Final[str] = f"https://{config.get().APP_HOST}/"
_ATR_JWT_TTL: Final[int] = 30 * 60
_GITHUB_OIDC_AUDIENCE: Final[str] = f"https://{config.get().APP_HOST}/"
_GITHUB_OIDC_EXPECTED: Final[dict[str, str]] = {
    "enterprise": "the-asf",
    "enterprise_id": "212555",
    "repository_owner": "apache",
    "runner_environment": "github-hosted",
}
_GITHUB_OIDC_ISSUER: Final[str] = "https://token.actions.githubusercontent.com"
_GITHUB_TRUSTED_DOMAINS: Final[list[str]] = ["token.actions.githubusercontent.com"]
_JWT_KEY_APP_EXTENSION: Final[str] = "jwt_secret_key"
_JWT_KEY_PATH: Final[pathlib.Path] = pathlib.Path("secrets/generated/jwt_secret_key.txt")
_JWT_KEY_TMP_PATH: Final[pathlib.Path] = pathlib.Path("secrets/generated/jwt_secret_key.txt.tmp")
_JWT_KEY_HEX_LENGTH: Final[int] = (256 // 8) * 2

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine


def activate_signing_key(key: str) -> None:
    app = asfquart.APP
    if app is None:
        raise RuntimeError("Application is not initialised")
    app.extensions[_JWT_KEY_APP_EXTENSION] = key


def issue(uid: str, *, ttl: int = _ATR_JWT_TTL, pat_hash: str | None = None) -> str:
    # audit_guidance no explicit typ header or token_type claim is added: the aud claim (_ATR_JWT_AUDIENCE)
    # already acts as an explicit token type discriminator, and ATR issues only one JWT type verified
    # by a single internal verifier — the RFC 9068 typ header is relevant to multi-issuer OAuth2 RS
    # deployments, which this is not
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
    if pat_hash:
        payload["atr_th"] = pat_hash
    log.auth_event("jwt_issuance", uid, pat_hash=pat_hash if pat_hash else None)
    return jwt.encode(payload, _signing_key(), algorithm=_ALGORITHM)


def require[**P, R](func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Awaitable[R]]:
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        token = _extract_bearer_token(quart.request)
        try:
            claims = await verify(token)
        except jwt.ExpiredSignatureError as exc:
            log.auth_failure("jwt_token", "jwt_token_expired")
            raise base.ASFQuartException("Token has expired", errorcode=401) from exc
        except jwt.InvalidSignatureError as exc:
            log.auth_failure("jwt_token", "jwt_signature_invalid")
            raise base.ASFQuartException("Token signature verification failed", errorcode=401) from exc
        except jwt.InvalidTokenError as exc:
            log.auth_failure("jwt_token", "jwt_token_invalid")
            raise base.ASFQuartException("Invalid Bearer JWT format", errorcode=401) from exc
        except jwt.PyJWTError as exc:
            log.auth_failure("jwt_token", "jwt_token_invalid_2")
            raise base.ASFQuartException(f"Invalid Bearer JWT: {exc}", errorcode=401) from exc

        quart.g.jwt_claims = claims
        log.auth_success("jwt_token")
        return await func(*args, **kwargs)

    return wrapper


def setup_signing_key(app: base.QuartApp) -> None:
    key = _read_signing_key()
    if key is None:
        key = write_new_signing_key()
    app.extensions[_JWT_KEY_APP_EXTENSION] = key


async def verify(token: str) -> dict[str, Any]:
    jwt_secret_key = _signing_key()
    # We get the uid for logging here, which does allow faked UIDs to be used, but they'll only be used
    # to be set in an auth_failure log which would show the claimed UID, which we explicitly want for audit context
    # (even if it's not real, we want to know someone tried to auth as that user)
    claims_unsafe = jwt.decode(token, options={"verify_signature": False}, algorithms=[_ALGORITHM])
    asf_uid = claims_unsafe.get("sub")
    log.set_asf_uid(asf_uid)
    claims = jwt.decode(
        token,
        jwt_secret_key,
        algorithms=[_ALGORITHM],
        issuer=_ATR_JWT_ISSUER,
        audience=_ATR_JWT_AUDIENCE,
        options={"require": ["sub", "iss", "aud", "iat", "exp", "jti"]},
    )
    log.debug(f"JWT claims: {claims}")
    if not isinstance(asf_uid, str):
        log.auth_failure("jwt_token", "jwt_subject_invalid")
        raise jwt.InvalidTokenError("Invalid Bearer JWT subject")
    if not await ldap.is_active(asf_uid):
        log.auth_failure("jwt_token", "account_deleted_or_banned")
        raise base.ASFQuartException("Account is disabled", errorcode=401)

    # audit_guidance Revalidating PAT on each request ensures PAT deletion immediately revokes all JWTs issued therefrom
    pat_hash = claims.get("atr_th")
    # Not all JWTs come from PATs, so don't fail on missing atr_th
    if pat_hash:
        async with db.session() as data:
            pat = await data.personal_access_token(pat_hash).get()
            if not pat:
                log.auth_failure("jwt_token", "pat_hash_invalid")
                raise base.ASFQuartException("Personal Access Token invalid", errorcode=401)
            if pat.asfuid != claims.get("sub"):
                log.auth_failure("jwt_token", "pat_user_mismatch")
                raise base.ASFQuartException("Personal Access Token invalid", errorcode=401)
            if pat.expires < datetime.datetime.now(datetime.UTC):
                log.auth_failure("jwt_token", "pat_expired")
                raise base.ASFQuartException("Personal Access Token expired", errorcode=401)
    return claims


async def verify_github_oidc(token: str) -> github.TrustedPublisherPayload:
    header = jwt.get_unverified_header(token)
    dangerous_headers = {"jku", "x5u", "jwk"}
    if dangerous_headers.intersection(header.keys()):
        raise base.ASFQuartException("JWT contains disallowed headers", errorcode=401)
    try:
        async with util.create_secure_session(timeout=aiohttp.ClientTimeout(total=30, connect=10)) as session:
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

    url = parse.urlparse(jwks_uri)

    if url.hostname not in _GITHUB_TRUSTED_DOMAINS:
        log.error(f"Untrusted domain in GitHub OIDC endpoint: {jwks_uri}")
        raise base.ASFQuartException("Untrusted domain in GitHub OIDC endpoint", 502)

    if url.scheme != "https":
        log.error(f"Github OIDC returned insecure URI: {jwks_uri}")
        raise base.ASFQuartException("Github OIDC returned insecure URI", 502)

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
    return github.TrustedPublisherPayload.model_validate(payload)


def write_new_signing_key() -> str:
    key = _new_signing_key()
    _write_signing_key(key)
    return key


def _extract_bearer_token(request: quart.Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if (scheme.lower() != "bearer") or (not token):
        raise base.ASFQuartException(
            "Authentication required. Please provide a valid Bearer token in the Authorization header", errorcode=401
        )
    return token


def _new_signing_key() -> str:
    return secrets.token_hex(256 // 8)


def _read_signing_key() -> str | None:
    if not _JWT_KEY_PATH.exists():
        return None
    key = _JWT_KEY_PATH.read_text(encoding="utf-8").strip()
    if key == "":
        raise RuntimeError(f"JWT signing key file is empty: {_JWT_KEY_PATH}")
    if len(key) != _JWT_KEY_HEX_LENGTH:
        raise RuntimeError("JWT signing key is not 256 bits")
    return key


def _signing_key() -> str:
    app = asfquart.APP
    if app is not None:
        key = app.extensions.get(_JWT_KEY_APP_EXTENSION)
        if isinstance(key, str) and key:
            return key
    key = _read_signing_key()
    if key is not None:
        return key
    raise RuntimeError("JWT signing key is not initialised")


def _write_signing_key(key: str) -> None:
    if key == "":
        raise ValueError("JWT signing key must not be empty")
    if len(key) != _JWT_KEY_HEX_LENGTH:
        raise ValueError("JWT signing key must be 256 bits")
    _JWT_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _JWT_KEY_TMP_PATH.exists():
        _JWT_KEY_TMP_PATH.unlink()
    temp_fd = os.open(_JWT_KEY_TMP_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(temp_fd, "w", encoding="utf-8") as file:
        file.write(key)
        file.flush()
        os.fsync(file.fileno())
    os.chmod(_JWT_KEY_TMP_PATH, 0o400)
    os.replace(_JWT_KEY_TMP_PATH, _JWT_KEY_PATH)
