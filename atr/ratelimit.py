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

"""Rate limits applied inside a handler, after authentication.

Decorator limits run before a request is authenticated, so they can only be keyed by session,
Bearer JWT or client address. These are keyed by whatever the handler has verified. The key
shouldn't include anything the caller chooses, or they could pick a fresh bucket each time.
"""

import datetime
import math
from typing import Final

import quart
import quart_rate_limiter as rate_limiter

import atr.config as config
import atr.log as log

_APP_EXTENSION: Final[str] = "atr_rate_limiter"


async def check(scope: str, key: str, count: int, period: datetime.timedelta) -> None:
    """Count one request against the limit for this scope and key, or refuse it with a 429."""
    limiter = _limiter(scope)
    if limiter is None:
        return
    # The same GCRA sum as quart_rate_limiter, which keeps its own private
    # Allows a burst of count requests, then one per interval
    seconds = period.total_seconds()
    interval = seconds / count
    store_key = f"{quart.current_app.import_name}-inline-{scope}-{count}-{seconds}-{key}"
    now = _now()
    stored = await limiter.store.get(store_key, now)
    if stored.tzinfo is None:
        stored = stored.astimezone(datetime.UTC)
    arrival = max(stored, now)
    ahead = (arrival - now).total_seconds()
    allowed_ahead = seconds - interval
    if ahead > allowed_ahead:
        log.warning(f"Rate limit {scope} reached for {key}")
        raise rate_limiter.RateLimitExceeded(math.ceil(ahead - allowed_ahead))
    await limiter.store.set(store_key, arrival + datetime.timedelta(seconds=interval))


def setup(app: quart.Quart, limiter: rate_limiter.RateLimiter) -> None:
    """Register the app's limiter, so these limits share its store."""
    app.extensions[_APP_EXTENSION] = limiter


def _limiter(scope: str) -> rate_limiter.RateLimiter | None:
    limiter = quart.current_app.extensions.get(_APP_EXTENSION)
    if not isinstance(limiter, rate_limiter.RateLimiter):
        # Only test mode runs without a limiter
        if config.is_test_mode():
            return None
        raise RuntimeError(f"No rate limiter is set up, so the {scope} limit can't be applied")
    if not quart.current_app.config.get("QUART_RATE_LIMITER_ENABLED", True):
        return None
    return limiter


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)
