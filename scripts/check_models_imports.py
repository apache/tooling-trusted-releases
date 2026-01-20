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

import ast
import pathlib
import sys
from typing import Final

_ALLOWED_PACKAGES: Final = frozenset(
    {
        "pydantic",
        "pydantic_core",
        "sqlalchemy",
        "sqlmodel",
    }
)


def _check_file(path: pathlib.Path) -> list[str]:
    errors = []
    tree = ast.parse(path.read_text(), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if not _is_stdlib(alias.name) and (root not in _ALLOWED_PACKAGES):
                    errors.append(f"{path}:{node.lineno}: disallowed import '{alias.name}'")

        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # This uses "from ." or "from .name" syntax
                continue
            if node.module is None:
                # This should be unreachable
                continue
            root = node.module.split(".")[0]
            if not _is_stdlib(node.module) and (root not in _ALLOWED_PACKAGES):
                errors.append(f"{path}:{node.lineno}: disallowed import from '{node.module}'")

    return errors


def _is_stdlib(module: str) -> bool:
    root = module.split(".")[0]
    return root in sys.stdlib_module_names


def _run() -> int:
    models_dir = pathlib.Path(__file__).parent.parent / "atr" / "models"
    errors = []

    for path in models_dir.glob("*.py"):
        errors.extend(_check_file(path))

    for error in errors:
        print(error, file=sys.stderr)

    return 1 if errors else 0


def main() -> None:
    sys.exit(_run())


if __name__ == "__main__":
    main()
