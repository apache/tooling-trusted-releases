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

_DEFAULT_PATH: Final = pathlib.Path(__file__).parent.parent / "atr" / "models" / "sql.py"
_ENUM_BASE_NAMES: Final = frozenset({"Enum", "StrEnum", "enum.Enum", "enum.StrEnum"})
_FIELD_NAMES: Final = frozenset({"Field", "sqlmodel.Field"})
_RELATIONSHIP_NAMES: Final = frozenset({"Relationship", "sqlmodel.Relationship"})
_COLUMN_NAMES: Final = frozenset({"Column", "sqlalchemy.Column", "sqlmodel.Column"})
_SKIPPED_NONNULL_TYPES: Final = frozenset({"bool", "bytes", "int", "str"})


def main() -> None:
    sys.exit(_run(sys.argv[1:]))


def _annotation_allows_null(annotation: ast.expr) -> bool:
    if _is_any(annotation):
        return True
    if _is_none(annotation):
        return True
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return _annotation_allows_null(annotation.left) or _annotation_allows_null(annotation.right)
    if isinstance(annotation, ast.Subscript) and (_name(annotation.value) == "Optional"):
        return True
    return False


def _annotation_is_skipped(annotation: ast.expr, enum_names: frozenset[str]) -> bool:
    root_name = _annotation_root_name(annotation)
    return (root_name in _SKIPPED_NONNULL_TYPES) or (root_name in enum_names)


def _annotation_root_name(annotation: ast.expr) -> str | None:
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        left_name = _annotation_root_name(annotation.left)
        if left_name is not None:
            return left_name
        return _annotation_root_name(annotation.right)
    if isinstance(annotation, ast.Subscript):
        return _name(annotation.value)
    return _name(annotation)


def _check_class(path: pathlib.Path, node: ast.ClassDef, enum_names: frozenset[str]) -> list[str]:
    errors = []

    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign):
            continue
        if not isinstance(statement.target, ast.Name):
            continue
        if _annotation_allows_null(statement.annotation):
            continue
        if _annotation_is_skipped(statement.annotation, enum_names):
            continue
        if _is_relationship(statement.value):
            continue
        if _has_explicit_not_nullable(statement.value):
            continue

        field_name = statement.target.id
        errors.append(
            f"{path}:{statement.lineno}: {node.name}.{field_name} is non-optional and must declare nullable=False"
        )

    return errors


def _check_file(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    errors = []
    enum_names = _enum_names(tree)

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not _is_table_model(node):
            continue
        errors.extend(_check_class(path, node, enum_names))

    return errors


def _column_has_not_nullable(value: ast.expr) -> bool:
    if not isinstance(value, ast.Call):
        return False
    if _name(value.func) not in _COLUMN_NAMES:
        return False

    for keyword in value.keywords:
        if (keyword.arg == "nullable") and isinstance(keyword.value, ast.Constant) and (keyword.value.value is False):
            return True

    return False


def _enum_names(tree: ast.Module) -> frozenset[str]:
    result = set()

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any(_name(base) in _ENUM_BASE_NAMES for base in node.bases):
            result.add(node.name)

    return frozenset(result)


def _field_keyword_is_false(value: ast.Call, name: str) -> bool:
    for keyword in value.keywords:
        if (keyword.arg == name) and isinstance(keyword.value, ast.Constant) and (keyword.value.value is False):
            return True
    return False


def _field_keyword_is_true(value: ast.Call, name: str) -> bool:
    for keyword in value.keywords:
        if (keyword.arg == name) and isinstance(keyword.value, ast.Constant) and (keyword.value.value is True):
            return True
    return False


def _has_explicit_not_nullable(value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    if _name(value.func) not in _FIELD_NAMES:
        return False
    if _field_keyword_is_true(value, "primary_key"):
        return True
    if _field_keyword_is_false(value, "nullable"):
        return True

    for keyword in value.keywords:
        if keyword.arg != "sa_column":
            continue
        if _column_has_not_nullable(keyword.value):
            return True

    return False


def _is_any(value: ast.expr) -> bool:
    return _name(value) in {"Any", "typing.Any"}


def _is_none(value: ast.expr) -> bool:
    return isinstance(value, ast.Constant) and (value.value is None)


def _is_relationship(value: ast.expr | None) -> bool:
    return isinstance(value, ast.Call) and (_name(value.func) in _RELATIONSHIP_NAMES)


def _is_table_model(node: ast.ClassDef) -> bool:
    if "sqlmodel.SQLModel" not in {_name(base) for base in node.bases}:
        return False

    for keyword in node.keywords:
        if (keyword.arg == "table") and isinstance(keyword.value, ast.Constant) and (keyword.value.value is True):
            return True

    return False


def _name(value: ast.expr) -> str | None:
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        parent = _name(value.value)
        if parent is None:
            return value.attr
        return f"{parent}.{value.attr}"
    return None


def _paths(argv: list[str]) -> list[pathlib.Path]:
    if not argv:
        return [_DEFAULT_PATH]
    return [pathlib.Path(arg) for arg in argv]


def _run(argv: list[str]) -> int:
    errors = []

    for path in _paths(argv):
        errors.extend(_check_file(path))

    for error in errors:
        print(error, file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    main()
