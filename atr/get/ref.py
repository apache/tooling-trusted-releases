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
from typing import Literal

import aiofiles
import aiofiles.os
import quart

import atr.blueprints.get as get
import atr.config as config
import atr.models.safe as safe
import atr.web as web

# Perhaps GitHub will get around to implementing symbol permalinks:
# https://github.com/orgs/community/discussions/13292
# Then this code will be easier, but we should still keep our own links


@get.typed
async def resolve(_session: web.Public, _ref: Literal["ref"], ref_path: safe.RelPath) -> web.WerkzeugResponse:
    """
    URL: /ref/<ref_path>
    Resolve a code reference to a GitHub permalink.
    """
    project_root = pathlib.Path(config.get().PROJECT_ROOT)
    path = str(ref_path)

    if ":" in path:
        file_path_str, symbol = path.rsplit(":", 1)
        resolved_file, validated_path_str = _validate_and_resolve_path(safe.RelPath(file_path_str), project_root)

        if (not await aiofiles.os.path.exists(resolved_file)) or (not await aiofiles.os.path.isfile(resolved_file)):
            quart.abort(404)

        line_number = await _resolve_symbol_to_line(resolved_file, symbol)

        if line_number is None:
            quart.abort(404)

        github_url = f"https://github.com/apache/tooling-trusted-releases/blob/main/{validated_path_str}#L{line_number}"
        return quart.redirect(github_url, code=303)

    # TODO: Should these functions exist in RelPath?
    is_directory = path.endswith("/")
    path_str = path.rstrip("/")
    resolved_path, validated_path_str = _validate_and_resolve_path(safe.RelPath(path_str), project_root)

    if not await aiofiles.os.path.exists(resolved_path):
        quart.abort(404)

    if is_directory:
        if not await aiofiles.os.path.isdir(resolved_path):
            quart.abort(404)
        github_url = f"https://github.com/apache/tooling-trusted-releases/tree/main/{validated_path_str}"
    else:
        if not await aiofiles.os.path.isfile(resolved_path):
            quart.abort(404)
        github_url = f"https://github.com/apache/tooling-trusted-releases/blob/main/{validated_path_str}"

    return quart.redirect(github_url, code=303)


async def _resolve_symbol_to_line(file_path: pathlib.Path, symbol: str) -> int | None:
    try:
        async with aiofiles.open(file_path, encoding="utf-8") as f:
            source = await f.read()
        tree = ast.parse(source, filename=str(file_path))
    except Exception:
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                return node.lineno
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and (target.id == symbol):
                    return node.lineno
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and (node.target.id == symbol):
                return node.lineno

    return None


def _validate_and_resolve_path(rel_path: safe.RelPath, project_root: pathlib.Path) -> tuple[pathlib.Path, str]:
    validated_path = rel_path.as_path()

    file_path = project_root / validated_path

    try:
        resolved_path = file_path.resolve()
        resolved_path.relative_to(project_root)
    except (FileNotFoundError, ValueError):
        quart.abort(404)

    return resolved_path, str(validated_path)
