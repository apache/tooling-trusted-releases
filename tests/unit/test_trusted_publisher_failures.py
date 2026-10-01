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

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import asfquart
import asfquart.base as base
import jwt as pyjwt
import pydantic
import pytest

import atr.blueprints.api_auth as api_auth
import atr.db.interaction as interaction

_SECRET_DETAIL = "internal-detail-that-must-not-leak"


class _Body(pydantic.BaseModel):
    publisher: str
    jwt: str


class _Recorder:
    def __init__(self) -> None:
        self.auth_failures: list[tuple[str, str]] = []
        self.warnings: list[dict[str, Any]] = []


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr("asfquart.APP", None)
    monkeypatch.setattr(
        api_auth.log, "auth_failure", lambda type, reason, asfuid=None: rec.auth_failures.append((type, reason))
    )

    class _FakeService:
        async def tokens_notify_trusted_publisher_failure(
            self, reason: str, detail: str, failed: Any, source_ip: str | None, suppressed: int
        ) -> None:
            rec.warnings.append({"reason": reason, "detail": detail, "suppressed": suppressed})

    @contextlib.asynccontextmanager
    async def fake_write_as_system(service_cls: type) -> AsyncGenerator[_FakeService]:
        yield _FakeService()

    monkeypatch.setattr(api_auth.storage, "write_as_system", fake_write_as_system)
    return rec


def _throttle_returns(monkeypatch: pytest.MonkeyPatch, held: int | None) -> None:
    monkeypatch.setattr(api_auth._TrustedPublisherAlertThrottle, "due", lambda self, reason, now: held)


def _validate_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    async def fake_validate(publisher: str, jwt: str) -> tuple[object, str | None]:
        raise exc

    monkeypatch.setattr(interaction, "validate_trusted_jwt", fake_validate)


async def _authenticate() -> base.ASFQuartException:
    app = asfquart.construct("test")
    async with app.test_request_context("/"):
        with pytest.raises(base.ASFQuartException) as excinfo:
            await api_auth.authenticate_body(api_auth.Auth.BODY_OIDC, _Body(publisher="github", jwt="a.b.c"))
    return excinfo.value


@pytest.mark.asyncio
async def test_alert_queue_failure_still_rejects(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    _validate_raising(monkeypatch, pyjwt.InvalidSignatureError(_SECRET_DETAIL))
    _throttle_returns(monkeypatch, 0)

    @contextlib.asynccontextmanager
    async def broken_write_as_system(service_cls: type) -> AsyncGenerator[None]:
        raise RuntimeError("database unavailable")
        yield

    monkeypatch.setattr(api_auth.storage, "write_as_system", broken_write_as_system)

    exc = await _authenticate()

    assert exc.errorcode == 401


@pytest.mark.asyncio
async def test_alerting_failure_queues_warning_with_detail(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder
) -> None:
    _validate_raising(monkeypatch, pyjwt.InvalidSignatureError(_SECRET_DETAIL))
    _throttle_returns(monkeypatch, 3)

    await _authenticate()

    assert recorder.warnings == [{"reason": "signature_invalid", "detail": _SECRET_DETAIL, "suppressed": 3}]


@pytest.mark.asyncio
async def test_held_back_failure_is_logged_but_not_emailed(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder
) -> None:
    _validate_raising(monkeypatch, pyjwt.InvalidIssuerError(_SECRET_DETAIL))
    _throttle_returns(monkeypatch, None)

    await _authenticate()

    assert recorder.auth_failures == [("trusted_publisher", "issuer_invalid")]
    assert recorder.warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (pyjwt.ExpiredSignatureError(_SECRET_DETAIL), "Trusted Publisher token has expired"),
        (pyjwt.InvalidSignatureError(_SECRET_DETAIL), "Trusted Publisher token signature is invalid"),
        (pyjwt.InvalidAudienceError(_SECRET_DETAIL), "Trusted Publisher token audience is invalid"),
        (pyjwt.DecodeError(_SECRET_DETAIL), "Trusted Publisher token is invalid"),
        (interaction.InteractionError(_SECRET_DETAIL), "Trusted Publisher is not supported"),
    ],
)
async def test_rejection_message_is_fixed_per_reason(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder, exc: Exception, message: str
) -> None:
    _validate_raising(monkeypatch, exc)
    _throttle_returns(monkeypatch, None)

    rejected = await _authenticate()

    assert rejected.errorcode == 401
    assert str(rejected) == message
    assert _SECRET_DETAIL not in str(rejected)


@pytest.mark.asyncio
async def test_rejection_message_omits_validation_input(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    class _Claims(pydantic.BaseModel):
        repository: int

    try:
        _Claims.model_validate({"repository": _SECRET_DETAIL})
    except pydantic.ValidationError as validation_error:
        _validate_raising(monkeypatch, validation_error)
    _throttle_returns(monkeypatch, None)

    rejected = await _authenticate()

    assert str(rejected) == "Trusted Publisher token claims are invalid"
    assert recorder.auth_failures == [("trusted_publisher", "claims_invalid")]


@pytest.mark.asyncio
async def test_routine_failure_is_logged_without_alerting(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    _validate_raising(monkeypatch, pyjwt.ExpiredSignatureError(_SECRET_DETAIL))

    def unexpected_alert(self: object, reason: str, now: float) -> int | None:
        raise AssertionError("routine failures must not reach the alert throttle")

    monkeypatch.setattr(api_auth._TrustedPublisherAlertThrottle, "due", unexpected_alert)

    await _authenticate()

    assert recorder.auth_failures == [("trusted_publisher", "token_expired")]


@pytest.mark.asyncio
async def test_unavailable_upstream_is_not_an_auth_failure(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder
) -> None:
    upstream = base.ASFQuartException("Failed to connect to GitHub OIDC endpoint", errorcode=502)
    _validate_raising(monkeypatch, upstream)

    rejected = await _authenticate()

    assert rejected is upstream
    assert recorder.auth_failures == []


@pytest.mark.asyncio
async def test_unlinked_account_keeps_message_and_alerts(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    unlinked = base.ASFQuartException("GitHub account abc (ID 123) is not yet linked", errorcode=403)
    _validate_raising(monkeypatch, unlinked)
    _throttle_returns(monkeypatch, 0)

    rejected = await _authenticate()

    assert rejected is unlinked
    assert recorder.auth_failures == [("trusted_publisher", "account_unlinked")]
    assert [w["reason"] for w in recorder.warnings] == ["account_unlinked"]


def test_warning_is_due_first_then_held_until_interval_passes() -> None:
    throttle = api_auth._TrustedPublisherAlertThrottle()
    start = 1000.0
    interval = api_auth._TP_ALERT_INTERVAL

    assert throttle.due("signature_invalid", start) == 0
    assert throttle.due("signature_invalid", start) is None
    assert throttle.due("signature_invalid", start + interval - 1) is None
    assert throttle.due("signature_invalid", start + interval) == 2
    assert throttle.due("signature_invalid", start + interval) is None


def test_warning_throttle_is_per_reason() -> None:
    throttle = api_auth._TrustedPublisherAlertThrottle()

    assert throttle.due("signature_invalid", 1000.0) == 0
    assert throttle.due("issuer_invalid", 1000.0) == 0
    assert throttle.due("signature_invalid", 1000.0) is None


@pytest.mark.asyncio
async def test_warning_throttle_is_held_per_app() -> None:
    first = asfquart.construct("first")
    second = asfquart.construct("second")
    async with first.app_context():
        throttle = api_auth._tp_alert_throttle()
        assert api_auth._tp_alert_throttle() is throttle
    async with second.app_context():
        assert api_auth._tp_alert_throttle() is not throttle
