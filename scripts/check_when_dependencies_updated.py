#!/usr/bin/env python3

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
import pathlib
import subprocess
import sys
from typing import Final

_MAX_AGE_DAYS: Final[int] = 30

# With a relative exclude-newer-span, uv records only this placeholder
_SENTINEL_PREFIX: Final[str] = "0001-01-01"


def main() -> None:
    lock_path = pathlib.Path("uv.lock")
    if not lock_path.exists():
        print("ERROR: uv.lock not found", file=sys.stderr)
        sys.exit(1)

    options = _parse_options(lock_path)
    timestamp = _last_updated(lock_path, options)
    if timestamp is None:
        print("ERROR: Could not determine when uv.lock was last updated", file=sys.stderr)
        print("Run: make update-deps", file=sys.stderr)
        sys.exit(1)

    now = datetime.datetime.now(datetime.UTC)
    age = now - timestamp

    if age > datetime.timedelta(days=_MAX_AGE_DAYS):
        print(f"ERROR: Dependencies are {age.days} days old (the limit is {_MAX_AGE_DAYS} days)", file=sys.stderr)
        print(f"Last updated: {timestamp.isoformat()}", file=sys.stderr)
        print("Run: make update-deps", file=sys.stderr)
        sys.exit(1)

    print(f"OK: Dependencies are {age.days} days old (the limit is {_MAX_AGE_DAYS} days)")


def _last_updated(lock_path: pathlib.Path, options: dict[str, str]) -> datetime.datetime | None:
    exclude_newer = options.get("exclude-newer")
    if exclude_newer is not None and not exclude_newer.startswith(_SENTINEL_PREFIX):
        return _parse_timestamp(exclude_newer)
    if "exclude-newer-span" not in options:
        return None
    # The lockfile carries no resolution timestamp in span mode, so we use
    # git instead, counting uncommitted changes to uv.lock as an update now
    if _is_modified(lock_path):
        return datetime.datetime.now(datetime.UTC)
    return _last_commit_time(lock_path)


def _parse_options(lock_path: pathlib.Path) -> dict[str, str]:
    options: dict[str, str] = {}
    in_options = False
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("["):
            in_options = line.strip() == "[options]"
            continue
        if not in_options:
            continue
        key, eq, value = line.partition("=")
        if not eq:
            continue
        value = value.strip()
        if value.startswith('"'):
            value = value[1:].partition('"')[0]
        options[key.strip()] = value
    return options


def _is_modified(lock_path: pathlib.Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", str(lock_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() != ""


def _last_commit_time(lock_path: pathlib.Path) -> datetime.datetime | None:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cI", "--", str(lock_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    return _parse_timestamp(text)


def _parse_timestamp(timestamp_str: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        return None


if __name__ == "__main__":
    main()
