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

import asyncio
import json
import os
import pathlib
import urllib.parse
from collections.abc import AsyncGenerator
from typing import Any

import aiohttp

import atr.ldap as ldap
import atr.log as log
import atr.svn.commits as commits

# The server sends keepalives every 5 seconds, so we should see
# activity well within this timeout period.
_DEFAULT_INACTIVITY_TIMEOUT = 11
### for debug:
# _DEFAULT_INACTIVITY_TIMEOUT = 4.5

# Default read buffer size. Max payload size in pypubsub is 256kb (plus metadata and JSON overhead)
_DEFAULT_READ_BUFFER_SIZE = 300 * 1024


def is_ldap_payload(payload: dict[str, Any]) -> bool:
    return "ldap" in payload.get("pubsub_topics", [])


def is_commit_payload(payload: dict[str, Any]) -> bool:
    return "commit" in payload.get("pubsub_topics", [])


#
# TYPICAL USAGE:
#
#   async for payload in listen(PUBSUB_URL):
#
# This will produce a series of payloads, forever.
#
# NOTE: this listener is intended for pypubsub, which terminates
#   payloads with a newline. The old svnpubsub used NUL characters,
#   so this client will not work with that server.
#


async def listen(
    pubsub_url: str,
    username: str | None = None,
    password: str | None = None,
    sock_read: float | None = None,
    buffersize: int | None = None,
) -> AsyncGenerator[Any | None]:
    if username:
        if password is None:
            raise ValueError("PubSub password is required")
        auth = aiohttp.BasicAuth(username, password)
    else:
        auth = None

    if sock_read is None:
        sock_read = _DEFAULT_INACTIVITY_TIMEOUT
    ct = aiohttp.ClientTimeout(sock_read=sock_read)

    if buffersize is None:
        buffersize = _DEFAULT_READ_BUFFER_SIZE

    async with aiohttp.ClientSession(auth=auth, timeout=ct, read_bufsize=buffersize) as session:
        # Retry immediately, and then back it off.
        delay = 0.0

        ### tbd: look at event loop, to see if it has been halted
        while True:
            log.debug("Opening new connection...")
            try:
                async for payload in _process_connection(session, pubsub_url):
                    if not payload:
                        ### tbd?: event loop killed or hit EOF
                        pass

                    # We got a payload, so reset the DELAY.
                    delay = 0.0

                    yield payload

            except (
                ConnectionRefusedError,
                aiohttp.ClientConnectorError,
                aiohttp.ServerTimeoutError,
                aiohttp.ClientPayloadError,
            ) as e:
                log.error(f"Connection failed ({type(e).__name__}: {e}), reconnecting in {delay} seconds")
                await asyncio.sleep(delay)

                # Back off on the delay. Step it up from 0s, doubling each
                # time, and top out at 30s retry. Steps: 0, 2, 6, 14, 30.
                delay = min(30.0, (delay + 1.0) * 2)


async def _process_connection(session, pubsub_url):
    # Connect to pubsub and listen for payloads.
    async with session.get(pubsub_url) as conn:
        # print('LIMITS:', conn.content.get_read_buffer_limits())

        while True:
            # The pubsub server defines stream payloads as:
            #    ENCODED_JSON(payload)+"\n"
            #
            # Due to the encoding, bare newlines will not occur
            # within the encoded part. Thus, we can read content
            # until we find a newline.
            #
            # Note: this newline is in RAW, but the json loader
            # ignores it.
            try:
                raw = await conn.content.readuntil(b"\n")
            except ValueError as e:
                log.error(f'Saw "{e}"; re-raising as ClientPayloadError to close/reconnect')
                raise aiohttp.ClientPayloadError("re-raised from ValueError in readuntil()")

            if not raw:
                # We just hit EOF.
                yield None

            yield json.loads(raw)


class PubSubListener:
    def __init__(
        self,
        svn_working_copy_root: os.PathLike | str,
        url: str,
        username: str,
        password: str,
        topics: str = "commit/svn,ldap",
    ) -> None:
        self.svn_working_copy_root = pathlib.Path(svn_working_copy_root)
        self.url = url
        self.username = username
        self.password = password
        self.topics = topics

    async def start(self) -> None:
        """Run forever, processing PubSub payloads as they arrive."""
        # TODO: Add reconnection logic here?
        # Or does asfpy.pubsub.listen() already do this?
        if not self.url:
            log.error("PubSub URL is not configured")
            log.warning("PubSubListener disabled: no URL provided")
            return

        if (not self.username) or (not self.password):
            log.error("PubSub credentials not configured")
            log.warning("PubSubListener disabled: missing credentials")
            return

        if not self.url.startswith("https://"):
            log.error(
                f"PubSub URL must use HTTPS protocol: {self.url!r}. Example: 'https://pubsub.apache.org:2069'",
            )
            log.warning("PubSubListener disabled due to invalid URL")
            return

        full_url = urllib.parse.urljoin(self.url, self.topics)
        log.info(f"PubSubListener starting with URL: {full_url}")

        try:
            async for payload in listen(
                full_url,
                username=self.username,
                password=self.password,
            ):
                if (payload is None) or ("stillalive" in payload):
                    continue

                if is_commit_payload(payload):
                    await commits.handle(payload, self.svn_working_copy_root)
                elif is_ldap_payload(payload):
                    await ldap.handle_update(payload)
                else:
                    continue
        except asyncio.CancelledError:
            log.info("PubSubListener cancelled, shutting down gracefully")
            raise
        except Exception as exc:
            log.exception(f"PubSubListener error: {exc}")
        finally:
            log.info("PubSubListener.start() finished")
