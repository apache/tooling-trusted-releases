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

"""
Reflect external KEYS file changes seen in dist SVN back into ATR.

For a committee set to REFLECT mode, SVN is the source of truth for its KEYS file. When the dist
watcher sees a commit touch that file, we read the new content and hand it to the reflection service,
which brings ATR's keys into line. ATR never pushes in this mode, so there's no loop to guard against.
"""

import asyncio
import pathlib
import shutil
import tempfile
from typing import Final

import aiofiles

import atr.db as db
import atr.log as log
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.svn as svn
import atr.svn.dist as dist
import atr.util as util

_INCUBATOR_SEGMENT: Final[str] = "incubator"
_KEYS_FILENAME: Final[str] = "KEYS"


async def reflect_commit(commit: dict) -> None:
    if str(commit.get("committer", "")) == svn.ASF_TOOL:
        # Our own KEYS publishes are already in step with the database
        return
    changed = commit.get("changed", {})
    if not isinstance(changed, dict):
        return
    candidates = _committees_with_keys_change(changed)
    if not candidates:
        return

    revision = _revision_number(commit.get("id"))
    # Resolve to committees actually in reflect mode, and build each read URL while the committee is
    # attached, so the export below doesn't touch a detached row.
    targets: list[tuple[str, str]] = []
    async with db.session() as data:
        for committee_key in sorted(candidates):
            committee = await data.committee(key=committee_key).get()
            if (committee is None) or (committee.keys_mode is not sql.KeysMode.REFLECT):
                continue
            targets.append((committee_key, util.svn_publish_internal_url(committee, None) + "/KEYS"))

    for committee_key, keys_url in targets:
        await _reflect_one(committee_key, keys_url, revision)


async def reflect_committee(committee_key: str) -> None:
    async with db.session() as data:
        committee = await data.committee(key=committee_key).get()
        if (committee is None) or (committee.keys_mode is not sql.KeysMode.REFLECT):
            return
        keys_url = util.svn_publish_internal_url(committee, None) + "/KEYS"
    await _reflect_one(committee_key, keys_url, revision=None)


def _committees_with_keys_change(changed: dict) -> set[str]:
    # Committees whose canonical KEYS file this commit added or changed. The file lives at
    # release/<committee>/KEYS for a TLP and release/incubator/<podling>/KEYS for a podling. A pure
    # deletion is left alone, so a KEYS file removed in SVN can't silently wipe a committee's keys.
    committees: set[str] = set()
    for raw_path, info in changed.items():
        path = str(raw_path)
        if not path.startswith(dist.RELEASE_PREFIX):
            continue
        flags = str(info.get("flags", "")) if isinstance(info, dict) else ""
        if flags.startswith("D"):
            continue
        parts = path.removeprefix(dist.RELEASE_PREFIX).split("/")
        if (len(parts) == 2) and (parts[1] == _KEYS_FILENAME):
            committees.add(parts[0])
        elif (len(parts) == 3) and (parts[0] == _INCUBATOR_SEGMENT) and (parts[2] == _KEYS_FILENAME):
            committees.add(parts[1])
    return committees


def _revision_number(value: object) -> int | None:
    return int(value) if isinstance(value, int) or (isinstance(value, str) and value.isdigit()) else None


async def _reflect_one(committee_key: str, keys_url: str, revision: int | None) -> None:
    try:
        keys_text = await _export_keys(keys_url, revision)
    except Exception:
        log.exception(f"reflect: could not read KEYS for {committee_key} from SVN")
        return
    try:
        async with storage.write_as_system(storage.WriteAsKeysReflectionService) as service:
            result = await service.reflect_committee_keys(committee_key, keys_text)
    except Exception:
        log.exception(f"reflect: could not apply KEYS changes for {committee_key}")
        return
    log.info(
        f"reflect: {committee_key} KEYS synced from SVN - "
        f"added {result.added}, removed {result.removed}, flagged {result.flagged}, cleared {result.cleared}"
        + (f", {result.errors} failed to parse" if result.errors else "")
        + ("" if result.reliable else " (removals skipped: KEYS file did not parse)")
    )


async def _export_keys(keys_url: str, revision: int | None) -> str:
    temp_dir = await asyncio.to_thread(tempfile.mkdtemp, dir=paths.get_tmp_dir())
    try:
        destination = pathlib.Path(temp_dir) / _KEYS_FILENAME
        await svn.export(keys_url, revision, destination)
        async with aiofiles.open(destination, encoding="utf-8") as f:
            return await f.read()
    finally:
        await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
