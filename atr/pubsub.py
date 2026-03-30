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
import os
import pathlib
import urllib.parse
from typing import Any

import asfpy.pubsub

import atr.ldap as ldap
import atr.log as log
import atr.svn.commits as commits


def is_ldap_payload(payload: dict[str, Any]) -> bool:
    return "ldap" in payload.get("pubsub_topics", [])


def is_commit_payload(payload: dict[str, Any]) -> bool:
    return "commit" in payload.get("pubsub_topics", [])


class PubSubListener:
    def __init__(
        self,
        svn_working_copy_root: os.PathLike | str,
        url: str,
        username: str,
        password: str,
        topics: str = "commit/svn,private/ldap",
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
            async for payload in asfpy.pubsub.listen(
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
