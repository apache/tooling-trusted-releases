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
import sys
from typing import Final

# Looser than the 30-day Python window: the frontend deps are Bootstrap's CSS/JS, which move more
# slowly, and bump.sh's 14-day cooldown already narrows the window from publish to allowed
_MAX_AGE_DAYS: Final[int] = 60

# bump.sh writes its --before cutoff here, the npm counterpart of uv.lock's exclude-newer. It's a
# committed file rather than the lockfile's mtime, which a fresh checkout would reset to now
_EXCLUDE_NEWER_PATH: Final[pathlib.Path] = pathlib.Path("bootstrap/source/.npm-exclude-newer")


def main() -> None:
    if not _EXCLUDE_NEWER_PATH.exists():
        print(f"ERROR: {_EXCLUDE_NEWER_PATH} not found", file=sys.stderr)
        print("Run: bootstrap/context/bump.sh VERSION", file=sys.stderr)
        sys.exit(1)

    timestamp = _parse_timestamp(_EXCLUDE_NEWER_PATH.read_text(encoding="utf-8").strip())
    if timestamp is None:
        print(f"ERROR: Could not parse a timestamp from {_EXCLUDE_NEWER_PATH}", file=sys.stderr)
        print("Run: bootstrap/context/bump.sh VERSION", file=sys.stderr)
        sys.exit(1)

    now = datetime.datetime.now(datetime.UTC)
    age = now - timestamp

    if age > datetime.timedelta(days=_MAX_AGE_DAYS):
        print(f"ERROR: npm dependencies are {age.days} days old (the limit is {_MAX_AGE_DAYS} days)", file=sys.stderr)
        print(f"Last updated: {timestamp.isoformat()}", file=sys.stderr)
        print("Run: bootstrap/context/bump.sh VERSION", file=sys.stderr)
        sys.exit(1)

    print(f"OK: npm dependencies are {age.days} days old (the limit is {_MAX_AGE_DAYS} days)")


def _parse_timestamp(timestamp_str: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except ValueError:
        return None


if __name__ == "__main__":
    main()
