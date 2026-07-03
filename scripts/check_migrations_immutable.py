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

import json
import os
import subprocess
import sys
from typing import Final

_DEPLOY_BRANCHES: Final = ("main", "tertia")
_MIGRATIONS_PREFIX: Final = "migrations/versions"
_REMOTE_BRANCHES: Final = ("altera", "arm", "main", "sbp", "tertia")


def main() -> None:
    sys.exit(_run())


def _git_lines(*arguments: str) -> list[str]:
    process = subprocess.run(["git", *arguments], capture_output=True, text=True)
    if process.returncode != 0:
        detail = process.stderr.strip() or f"exit status {process.returncode}"
        sys.exit(f"ERROR: git {' '.join(arguments)}: {detail}")
    return [line for line in process.stdout.splitlines() if line.strip()]


def _git_ok(*arguments: str) -> bool:
    return subprocess.run(["git", *arguments], capture_output=True).returncode == 0


def _github_base() -> str | None:
    # GITHUB_BASE_REF is set only for pull_request events, so push events get no range check
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if not base_ref:
        return None
    remote_ref = f"origin/{base_ref}"
    if not _git_ok("rev-parse", "--verify", "--quiet", remote_ref):
        sys.exit(f"ERROR: {remote_ref} is not available; the workflow checkout must use fetch-depth: 0")
    return _git_lines("merge-base", "HEAD", remote_ref)[0]


def _push_base() -> str | None:
    if os.environ.get("GITHUB_EVENT_NAME") != "push":
        return None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    with open(event_path) as file:
        before = json.load(file).get("before", "")
    if before.strip("0") and _git_ok("rev-parse", "--verify", "--quiet", f"{before}^{{commit}}"):
        return before
    if os.environ.get("GITHUB_REF_NAME") in _DEPLOY_BRANCHES:
        sys.exit(f"ERROR: the previous tip {before} of this deploy branch is not available to diff against")
    if not _git_ok("rev-parse", "--verify", "--quiet", "origin/main"):
        sys.exit("ERROR: origin/main is not available; the workflow checkout must use fetch-depth: 0")
    return _git_lines("merge-base", "HEAD", "origin/main")[0]


def _range_violations(base: str) -> list[str]:
    filters = ["--name-status", "--diff-filter=DMRT", "--", _MIGRATIONS_PREFIX]
    violations = _git_lines("log", "--format=%h", base + "..HEAD", *filters)
    for entry in _git_lines("diff", base, "HEAD", *filters):
        if entry not in violations:
            violations.append(entry)
    return violations


def _run() -> int:
    violations = _staged_violations()
    base = _github_base()
    if base is None:
        base = _push_base()
    if base is not None:
        violations.extend(_range_violations(base))
    if not violations:
        return 0
    print("Migrations are append only once pushed, and can never be modified, renamed, or deleted")
    print("To correct a migration which has already shipped, add a new migration on top instead")
    for violation in violations:
        print(f"  {violation}")
    return 1


def _shipped_paths() -> set[str]:
    paths: set[str] = set()
    for branch in _REMOTE_BRANCHES:
        ref = f"origin/{branch}"
        if not _git_ok("rev-parse", "--verify", "--quiet", ref):
            continue
        paths.update(_git_lines("ls-tree", "-r", "--name-only", ref, "--", _MIGRATIONS_PREFIX))
    return paths


def _staged_violations() -> list[str]:
    entries = _git_lines("diff", "--cached", "--name-status", "--diff-filter=DMRT", "--", _MIGRATIONS_PREFIX)
    if not entries:
        return []
    shipped = _shipped_paths()
    return [entry for entry in entries if entry.split("\t")[1] in shipped]


if __name__ == "__main__":
    main()
