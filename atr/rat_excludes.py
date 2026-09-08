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

from typing import Final

import aiohttp

import atr.hashes as hashes
import atr.log as log
import atr.models.validation as validation
import atr.util as util

# A .rat-excludes file is a short list of glob patterns, so anything larger than this is either
# not the file we expected or an attempt to make us buffer something big
_MAX_BYTES: Final[int] = 64 * 1024

_TIMEOUT: Final[aiohttp.ClientTimeout] = aiohttp.ClientTimeout(total=15, connect=10)


class RatExcludesError(Exception):
    pass


class TransientError(RatExcludesError):
    # A network-level problem that might clear on its own, so the check retries rather than fails
    pass


class UnavailableError(RatExcludesError):
    # A problem the release manager has to fix (bad host, missing file, oversized), so the check
    # records it rather than retrying
    pass


def content_hash(data: bytes) -> str:
    return hashes.compute_bytes_hash(data)


async def fetch(url: str) -> bytes:
    """Fetch a project's .rat-excludes file, enforcing the host allowlist and size cap."""
    try:
        validation.validate_rat_excludes_url(url)
    except ValueError as exc:
        raise UnavailableError(str(exc)) from exc

    try:
        # public=True routes DNS through the resolver that refuses private and loopback
        # addresses, so a project URL can't be pointed back at our own network
        async with util.create_secure_session(timeout=_TIMEOUT, public=True) as session:
            # Don't follow redirects - the allowlist only checks the URL we were given
            response = await session.get(url, allow_redirects=False)
            response.raise_for_status()
            if response.status >= 300:
                raise UnavailableError(f"RAT excludes URL redirected (HTTP {response.status}); use a direct link")
            body = await response.content.read(_MAX_BYTES + 1)
    except aiohttp.ClientResponseError as exc:
        raise UnavailableError(f"RAT excludes URL returned HTTP {exc.status}") from exc
    except (aiohttp.ClientSSLError, aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError) as exc:
        log.warning(f"Transient failure fetching RAT excludes URL {url}: {exc}")
        raise TransientError(str(exc)) from exc
    except aiohttp.ClientError as exc:
        raise UnavailableError(str(exc)) from exc

    if len(body) > _MAX_BYTES:
        raise UnavailableError(f"RAT excludes file exceeds {_MAX_BYTES} bytes")
    return body
