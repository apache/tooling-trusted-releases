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
import pathlib
import re
import urllib.parse
from collections.abc import AsyncGenerator, Callable
from typing import Any

import aiohttp

import atr.cache as cache
import atr.db as db
import atr.ldap as ldap
import atr.log as log
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.svn.commits as commits
import atr.util as util

_CURSOR_PATTERN = re.compile(r"[0-9A-Za-z-]{36}")

# The server sends keepalives every 5 seconds, so we should see
# activity well within this timeout period.
_DEFAULT_INACTIVITY_TIMEOUT = 11
### for debug:
# _DEFAULT_INACTIVITY_TIMEOUT = 4.5

# Default read buffer size. Max payload size in pypubsub is 256kb (plus metadata and JSON overhead)
_DEFAULT_READ_BUFFER_SIZE = 300 * 1024

_STREAM_CONTENT_TYPE = "application/vnd.pypubsub-stream"


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
    cursor: Callable[[], str | None] | None = None,
) -> AsyncGenerator[dict[str, Any]]:
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
                async for payload in _process_connection(session, pubsub_url, cursor() if cursor else None):
                    # We got a payload, so reset the DELAY.
                    delay = 0.0

                    yield payload

            except Exception as e:
                log.error(f"Connection failed ({type(e).__name__}: {e}), reconnecting in {delay} seconds")

            await asyncio.sleep(delay)

            # Back off on the delay. Step it up from 0s, doubling each
            # time, and top out at 30s retry. Steps: 0, 2, 6, 14, 30.
            delay = min(30.0, (delay + 1.0) * 2)


async def _cursor_load(stream: str) -> str | None:
    try:
        text = await asyncio.to_thread(_cursor_path().read_text)
    except FileNotFoundError:
        return None
    data = json.loads(text)
    if data.get("stream") != stream:
        return None
    cursor = data.get("cursor")
    if isinstance(cursor, str) and _CURSOR_PATTERN.fullmatch(cursor):
        return cursor
    raise ValueError(f"Invalid cursor in {_cursor_path()}")


def _cursor_path() -> pathlib.Path:
    return pathlib.Path(paths.get_runtime_dir()) / "pubsub-cursor.json"


async def _cursor_save(stream: str, cursor: str) -> None:
    await util.atomic_write_file(_cursor_path(), json.dumps({"stream": stream, "cursor": cursor}))


async def _failure_record(payload: dict[str, Any], detail: str) -> None:
    cursor = payload.get("pubsub_cursor")
    failure = sql.PubSubFailure(
        cursor=cursor if isinstance(cursor, str) else None,
        detail=detail,
        payload=payload,
    )
    try:
        async with db.session() as data:
            data.add(failure)
            await data.commit()
    except Exception as exc:
        log.exception(f"PubSub failure record failed: {exc}")


async def _halt_notify(detail: str) -> None:
    message = (
        f"PubSub is not running: the resume file {_cursor_path()} is damaged ({detail}). "
        "Inspect it, then remove it and restart ATR to resume from the live stream."
    )
    try:
        for asf_uid in sorted(cache.admins_get()):
            async with storage.write_as_user_service(asf_uid) as waus:
                await waus.notifications_create(message)
    except Exception:
        log.exception("Failed to record PubSub halt notifications")


async def _handle_payload(payload: dict[str, Any]) -> str | None:
    if is_commit_payload(payload):
        return await commits.handle(payload)
    if is_ldap_payload(payload):
        return await ldap.handle_update(payload)
    return "No handler for payload topics"


async def _process_connection(session, pubsub_url, since):
    # Connect to pubsub and listen for payloads.
    headers = {"X-Fetch-Since-Cursor": since} if since else {}
    async with session.get(pubsub_url, headers=headers) as conn:
        # print('LIMITS:', conn.content.get_read_buffer_limits())
        conn.raise_for_status()
        if conn.content_type != _STREAM_CONTENT_TYPE:
            log.warning(f"Unexpected pubsub content type: {conn.content_type}")
        if since:
            # TODO: Modify the PubSub server to confirm replay
            log.info(f"Requested replay since cursor {since}; the server does not confirm replay")

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
            raw = await conn.content.readuntil(b"\n")

            if not raw:
                # EOF - end this connection so listen() reopens it, rather than
                # falling through to json.loads(b"") and raising.
                return

            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError(f"Expected a JSON object, got {type(payload).__name__}")
            yield payload


class PubSubListener:
    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        topics: str = "commit/svn,ldap",
    ) -> None:
        self.url = url
        self.username = username
        self.password = password
        self.topics = topics
        self.cursor: str | None = None
        self.staged: str | None = None

    async def start(self) -> None:
        """Run forever, processing PubSub payloads as they arrive."""
        # TODO: Add reconnection logic here?
        # Or does asfpy.pubsub.listen() already do this?
        if not self._configured():
            return

        full_url = urllib.parse.urljoin(self.url, self.topics)
        log.info(f"PubSubListener starting with URL: {full_url}")

        try:
            self.cursor = await _cursor_load(full_url)
        except Exception as exc:
            log.exception(f"PubSub resume file is damaged, not starting: {exc}")
            await _halt_notify(f"{type(exc).__name__}: {exc}")
            return

        try:
            async for payload in listen(
                full_url, username=self.username, password=self.password, cursor=lambda: self.cursor
            ):
                if "stillalive" in payload:
                    await self._checkpoint(full_url)
                    continue
                # Isolate per-payload failures: one bad commit or LDAP event must not tear the
                # whole listener down (and with it the other topic)
                try:
                    detail = await _handle_payload(payload)
                except Exception as exc:
                    log.exception(
                        f"PubSub handler failed for one payload, skipping: {exc}",
                        cursor=payload.get("pubsub_cursor"),
                    )
                    await _failure_record(payload, f"{type(exc).__name__}: {exc}")
                    continue
                if detail is not None:
                    log.warning(f"PubSub handler reported a problem: {detail}", cursor=payload.get("pubsub_cursor"))
                    await _failure_record(payload, detail)
                staged = payload.get("pubsub_cursor")
                if isinstance(staged, str) and _CURSOR_PATTERN.fullmatch(staged):
                    self.staged = staged
        except asyncio.CancelledError:
            log.info("PubSubListener cancelled, shutting down gracefully")
            raise
        except Exception as exc:
            log.exception(f"PubSubListener error: {exc}")
        finally:
            log.info("PubSubListener.start() finished")

    async def _checkpoint(self, stream: str) -> None:
        cursor = self.staged
        if (cursor is None) or (cursor == self.cursor):
            return
        try:
            await _cursor_save(stream, cursor)
        except Exception as exc:
            log.exception(f"PubSub cursor save failed, will retry: {exc}")
            return
        self.cursor = cursor

    def _configured(self) -> bool:
        if not self.url:
            log.error("PubSub URL is not configured")
            log.warning("PubSubListener disabled: no URL provided")
            return False
        if (not self.username) or (not self.password):
            log.error("PubSub credentials not configured")
            log.warning("PubSubListener disabled: missing credentials")
            return False
        if not self.url.startswith("https://"):
            log.error(f"PubSub URL must use HTTPS protocol: {self.url!r}. Example: 'https://pubsub.apache.org:2069'")
            log.warning("PubSubListener disabled due to invalid URL")
            return False
        return True
