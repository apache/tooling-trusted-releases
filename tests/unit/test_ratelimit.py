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

import datetime
from typing import Final

import pytest
import quart
import quart_rate_limiter as rate_limiter

import atr.ratelimit as ratelimit

_HOUR: Final = datetime.timedelta(hours=1)


@pytest.mark.asyncio
async def test_check_counts_each_key_on_its_own() -> None:
    async with _app().app_context():
        for _ in range(3):
            await ratelimit.check("scope", "example:alice", 3, _HOUR)

        await ratelimit.check("scope", "example:bob", 3, _HOUR)
        await ratelimit.check("scope", "other:alice", 3, _HOUR)
        with pytest.raises(rate_limiter.RateLimitExceeded):
            await ratelimit.check("scope", "example:alice", 3, _HOUR)


@pytest.mark.asyncio
async def test_check_counts_each_scope_on_its_own() -> None:
    async with _app().app_context():
        await ratelimit.check("one", "example:alice", 1, _HOUR)

        await ratelimit.check("two", "example:alice", 1, _HOUR)
        with pytest.raises(rate_limiter.RateLimitExceeded):
            await ratelimit.check("one", "example:alice", 1, _HOUR)


@pytest.mark.asyncio
async def test_check_does_nothing_in_test_mode_which_has_no_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit.config, "is_test_mode", lambda: True)
    async with quart.Quart(__name__).app_context():
        for _ in range(5):
            await ratelimit.check("scope", "example:alice", 1, _HOUR)


@pytest.mark.asyncio
async def test_check_does_nothing_when_limits_are_switched_off() -> None:
    app = _app()
    app.config["QUART_RATE_LIMITER_ENABLED"] = False
    async with app.app_context():
        for _ in range(5):
            await ratelimit.check("scope", "example:alice", 1, _HOUR)


@pytest.mark.asyncio
async def test_check_lets_one_more_through_after_each_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    clock = [start]
    monkeypatch.setattr(ratelimit, "_now", lambda: clock[0])
    async with _app().app_context():
        for _ in range(6):
            await ratelimit.check("scope", "example:alice", 6, _HOUR)
        with pytest.raises(rate_limiter.RateLimitExceeded):
            await ratelimit.check("scope", "example:alice", 6, _HOUR)

        # 6 an hour is one every 10 minutes after the burst
        clock[0] = start + datetime.timedelta(minutes=10, seconds=1)
        await ratelimit.check("scope", "example:alice", 6, _HOUR)
        with pytest.raises(rate_limiter.RateLimitExceeded):
            await ratelimit.check("scope", "example:alice", 6, _HOUR)


@pytest.mark.asyncio
async def test_check_refuses_loudly_when_no_limiter_was_set_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit.config, "is_test_mode", lambda: False)
    async with quart.Quart(__name__).app_context():
        with pytest.raises(RuntimeError, match="No rate limiter is set up"):
            await ratelimit.check("scope", "example:alice", 1, _HOUR)


@pytest.mark.asyncio
async def test_check_refuses_past_the_limit_and_says_when_to_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[str] = []
    monkeypatch.setattr(ratelimit.log, "warning", logged.append)
    async with _app().app_context():
        for _ in range(6):
            await ratelimit.check("scope", "example:alice", 6, _HOUR)

        with pytest.raises(rate_limiter.RateLimitExceeded) as exc_info:
            await ratelimit.check("scope", "example:alice", 6, _HOUR)

    assert exc_info.value.code == 429
    assert 0 < exc_info.value.retry_after <= 600
    assert logged == ["Rate limit scope reached for example:alice"]


def _app() -> quart.Quart:
    app = quart.Quart(__name__)
    # High default, so only the limits under test can refuse
    limiter = rate_limiter.RateLimiter(
        app, default_limits=[rate_limiter.RateLimit(1000, datetime.timedelta(minutes=1))]
    )
    ratelimit.setup(app, limiter)
    return app
