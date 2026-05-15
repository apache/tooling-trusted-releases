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

import pathlib
import sys
import textwrap
from typing import Final

_ALLOWED: Final = frozenset(
    {
        ("_virtualenv.pth", b"import _virtualenv"),
    }
)

_VENV: Final = pathlib.Path(".venv")


def main() -> None:
    sys.exit(_run())


def _run() -> int:
    if not _VENV.is_dir():
        print(f"{_VENV}/ not present; skipping .pth check", file=sys.stderr)
        return 0
    seen: set[pathlib.Path] = set()
    bad: list[pathlib.Path] = []
    for path in sorted(_VENV.rglob("*.pth")):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        content = path.read_bytes()
        if (path.name, content) in _ALLOWED:
            continue
        bad.append(path)
    if not bad:
        return 0
    print("Unexpected .pth file(s) detected in .venv.", file=sys.stderr)
    print("Python executes every '.pth' line beginning with 'import'", file=sys.stderr)
    print("on interpreter startup, which makes .pth a known", file=sys.stderr)
    print("supply-chain attack vector. Audit each file by hand before", file=sys.stderr)
    print("allowlisting it in scripts/check_pth_files.py.", file=sys.stderr)
    print(file=sys.stderr)
    for path in bad:
        rel = path.relative_to(_VENV)
        body = textwrap.indent(path.read_bytes().decode("utf-8", errors="replace"), "    ")
        print(f"  .venv/{rel}", file=sys.stderr)
        print(body, file=sys.stderr)
    return 1


if __name__ == "__main__":
    main()
