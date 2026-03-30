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

import pathlib
from typing import TYPE_CHECKING, Final

import atr.log as log
import atr.svn as svn

if TYPE_CHECKING:
    from collections.abc import Sequence

# TODO: Check that these prefixes are correct
_WATCHED_PREFIXES: Final[tuple[str, ...]] = (
    "/svn/dist/dev",
    "/svn/dist/release",
)


async def handle(payload: dict, working_copy_root: pathlib.Path) -> None:
    pubsub_path = str(payload.get("pubsub_path", ""))
    # Ignore commits outside dist/dev or dist/release
    if pubsub_path.startswith(_WATCHED_PREFIXES):
        log.debug(f"PubSub payload: {payload}")
        await _process_payload(payload, working_copy_root)


async def _process_payload(payload: dict, working_copy_root: pathlib.Path) -> None:
    """
    Update each changed file in the local working copy.

    Payload format that we listen to:
        {
          "commit": {
             "changed": ["/path/inside/repo/foo.txt", ...]
          },
          ...
        }
    """
    changed: Sequence[str] = payload.get("commit", {}).get("changed", [])
    for repo_path in changed:
        prefix = next((p for p in _WATCHED_PREFIXES if repo_path.startswith(p)), "")
        if not prefix:
            continue
        local_path = working_copy_root / repo_path[len(prefix) :].lstrip("/")
        try:
            await svn.update(local_path)
            log.info(f"svn updated {local_path}")
        except Exception as exc:
            log.warning(f"failed svn update {local_path}: {exc}")
