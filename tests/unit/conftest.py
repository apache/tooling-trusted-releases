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
from typing import NamedTuple

import pytest

import atr.cache as cache


class EmailUidCacheSnapshot(NamedTuple):
    exists: bool
    size: int | None
    mode: int | None
    mtime_ns: int | None


def _real_email_uid_cache_path() -> pathlib.Path:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "state" / "secrets" / "cached" / "email_uid.json"


_REAL_EMAIL_UID_CACHE_PATH = _real_email_uid_cache_path().resolve()
_ORIGINAL_EMAIL_UID_SAVE_TO_FILE = cache.email_uid_save_to_file


async def _guarded_email_uid_save_to_file(hashes: dict[str, str], reverse: dict[str, list[str]]) -> None:
    if cache._email_uid_path().resolve() == _REAL_EMAIL_UID_CACHE_PATH:
        raise AssertionError(
            "Unit test attempted to write the real email-to-UID cache. "
            "Patch atr.config.get to use a temporary STATE_DIR, or mock cache.email_uid_view_or_live()."
        )
    await _ORIGINAL_EMAIL_UID_SAVE_TO_FILE(hashes, reverse)


cache.email_uid_save_to_file = _guarded_email_uid_save_to_file


def _snapshot(path: pathlib.Path) -> EmailUidCacheSnapshot:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return EmailUidCacheSnapshot(False, None, None, None)
    return EmailUidCacheSnapshot(True, stat.st_size, stat.st_mode & 0o777, stat.st_mtime_ns)


@pytest.fixture(autouse=True)
def prevent_real_email_uid_cache_writes():
    before = _snapshot(_REAL_EMAIL_UID_CACHE_PATH)

    yield

    after = _snapshot(_REAL_EMAIL_UID_CACHE_PATH)
    assert after == before, (
        "Unit test changed the real email-to-UID cache file. "
        f"path={_REAL_EMAIL_UID_CACHE_PATH}, before={before}, after={after}"
    )
