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

import atr.log as log
import atr.models.validation as validation
import atr.util as util

# An email template is a page of text at most, so anything larger is not the file we expected
_MAX_BYTES: Final[int] = 64 * 1024

_TIMEOUT: Final[aiohttp.ClientTimeout] = aiohttp.ClientTimeout(total=10, connect=5)


class EmailTemplateError(Exception):
    pass


async def fetch(url: str) -> str:
    """Fetch a project's email template, enforcing the host allowlist and size cap."""
    try:
        validation.validate_fetch_url(url)
    except ValueError as exc:
        raise EmailTemplateError(str(exc)) from exc

    try:
        # public=True routes DNS through the resolver that refuses private and loopback
        # addresses, so a project URL can't be pointed back at our own network
        async with util.create_secure_session(timeout=_TIMEOUT, public=True) as session:
            # Don't follow redirects - the allowlist only checks the URL we were given
            response = await session.get(url, allow_redirects=False)
            response.raise_for_status()
            if response.status >= 300:
                raise EmailTemplateError(f"the URL redirected (HTTP {response.status}); use a direct link")
            body = await response.content.read(_MAX_BYTES + 1)
    except aiohttp.ClientResponseError as exc:
        raise EmailTemplateError(f"the URL returned HTTP {exc.status}") from exc
    except aiohttp.ClientError as exc:
        log.warning(f"Failed to fetch email template URL {url}: {exc}")
        raise EmailTemplateError(str(exc) or type(exc).__name__) from exc
    except TimeoutError as exc:
        raise EmailTemplateError("the request timed out") from exc

    if len(body) > _MAX_BYTES:
        raise EmailTemplateError(f"the template exceeds {_MAX_BYTES} bytes")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EmailTemplateError("the template is not valid UTF-8") from exc
    if not text.strip():
        raise EmailTemplateError("the template is empty")
    return text
