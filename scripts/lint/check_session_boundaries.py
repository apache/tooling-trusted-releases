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

"""Check session-context boundaries (issue #299).

Two rules are checked:

1. `async with db.session()` does not appear inside `async with storage.write*(...)`
   or `async with storage.read*(...)`. The outer `storage.write/read` already
   provides a session, and a separate inner session risks producing ORM objects
   that outlive the inner block.

2. Public methods in `atr/storage/writers/*.py` do not take `sql.X` table-model
   types as parameters. Writers take `safe.X` keys and load the ORM object in their
   own session. This applies only to names defined as
   `class X(sqlmodel.SQLModel, table=True)` in `atr/models/sql.py`, so enums and
   other value types in `sql` are fine. Return annotations are not checked.

   `Callable[...]` subscripts are treated as opaque, as the callback pattern
   hands ORM objects to a callback whose lifetime is bounded by the framework's
   own session.

With no arguments the whole of `atr/` is scanned, otherwise only the given paths.
"""

import ast
import pathlib
import sys
from typing import Final

_DEFAULT_PATH: Final = pathlib.Path(__file__).parent.parent.parent / "atr"
_SQL_MODELS_PATH: Final = pathlib.Path(__file__).parent.parent.parent / "atr" / "models" / "sql.py"
_WRITERS_DIR_FRAGMENT: Final = "atr/storage/writers/"
_CALLABLE_NAMES: Final = frozenset({"Callable", "typing.Callable", "collections.abc.Callable"})


def main() -> None:
    sys.exit(_run(sys.argv[1:]))


def _annotation_references_table_model(annotation: ast.AST | None, table_models: frozenset[str]) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Subscript) and _is_callable(annotation.value):
        return False
    if isinstance(annotation, (ast.Name, ast.Attribute)):
        name = _name(annotation)
        if name and any((name == f"sql.{model}") or name.endswith(f".sql.{model}") for model in table_models):
            return True
    for child in ast.iter_child_nodes(annotation):
        if _annotation_references_table_model(child, table_models):
            return True
    return False


def _check_file(path: pathlib.Path, table_models: frozenset[str]) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    errors: list[str] = []
    _check_session_nesting(path, tree, False, errors)
    if _is_writer_module(path):
        errors.extend(_check_writer_signatures(path, tree, table_models))
    return errors


def _check_session_nesting(path: pathlib.Path, node: ast.AST, in_storage: bool, errors: list[str]) -> None:
    if isinstance(node, ast.AsyncWith):
        opens_storage = any(_is_storage_context_call(item.context_expr) for item in node.items)
        opens_db_session = any(_is_db_session_call(item.context_expr) for item in node.items)
        if in_storage and opens_db_session:
            errors.append(f"{path}:{node.lineno}: db.session() opened inside storage.write/read context")
        next_in_storage = in_storage or opens_storage
        for child in ast.iter_child_nodes(node):
            _check_session_nesting(path, child, next_in_storage, errors)
        return
    for child in ast.iter_child_nodes(node):
        _check_session_nesting(path, child, in_storage, errors)


def _check_signature(
    path: pathlib.Path,
    cls: ast.ClassDef,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    table_models: frozenset[str],
) -> list[str]:
    errors: list[str] = []
    for arg in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs):
        if _annotation_references_table_model(arg.annotation, table_models):
            errors.append(
                f"{path}:{arg.lineno}: {cls.name}.{func.name} parameter '{arg.arg}' has sql.* table-model type"
            )
    return errors


def _check_writer_signatures(path: pathlib.Path, tree: ast.AST, table_models: frozenset[str]) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if member.name.startswith("_"):
                continue
            errors.extend(_check_signature(path, node, member, table_models))
    return errors


def _files(argv: list[str]) -> list[pathlib.Path]:
    if not argv:
        return _python_files(_DEFAULT_PATH)
    paths: list[pathlib.Path] = []
    for arg in argv:
        path = pathlib.Path(arg)
        if path.is_dir():
            paths.extend(_python_files(path))
        else:
            paths.append(path)
    return paths


def _is_callable(value: ast.expr) -> bool:
    name = _name(value)
    return (name is not None) and (name in _CALLABLE_NAMES)


def _is_db_session_call(value: ast.expr) -> bool:
    return isinstance(value, ast.Call) and (_name(value.func) == "db.session")


def _is_storage_context_call(value: ast.expr) -> bool:
    if not isinstance(value, ast.Call):
        return False
    name = _name(value.func)
    if name is None:
        return False
    if not name.startswith("storage."):
        return False
    method = name.removeprefix("storage.")
    return method.startswith("write") or method.startswith("read")


def _is_table_model(node: ast.ClassDef) -> bool:
    if "sqlmodel.SQLModel" not in {_name(base) for base in node.bases}:
        return False
    for keyword in node.keywords:
        if (keyword.arg == "table") and isinstance(keyword.value, ast.Constant) and (keyword.value.value is True):
            return True
    return False


def _is_writer_module(path: pathlib.Path) -> bool:
    return _WRITERS_DIR_FRAGMENT in str(path).replace("\\", "/")


def _name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        parent = _name(value.value)
        if parent is None:
            return value.attr
        return f"{parent}.{value.attr}"
    return None


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(directory.rglob("*.py"))


def _run(argv: list[str]) -> int:
    table_models = _table_model_names()
    errors: list[str] = []
    for path in _files(argv):
        errors.extend(_check_file(path, table_models))
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def _table_model_names() -> frozenset[str]:
    tree = ast.parse(_SQL_MODELS_PATH.read_text(), filename=str(_SQL_MODELS_PATH))
    result: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_table_model(node):
            result.add(node.name)
    return frozenset(result)


if __name__ == "__main__":
    main()
