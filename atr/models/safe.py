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

from __future__ import annotations

import pathlib
import string
import unicodedata
from typing import Annotated, Any, Final

import pydantic

_ALPHANUM: Final = frozenset(string.ascii_letters + string.digits + "-")
_NUMERIC: Final = frozenset(string.digits)
_PATH_CHARS: Final = frozenset(string.ascii_letters + string.digits + "-._+~/()")
_VERSION_CHARS: Final = _ALPHANUM | frozenset(".+")


class SafeType:
    __slots__ = ("_value",)

    @classmethod
    def _valid_chars(cls) -> frozenset[str]:
        # default is the base set; subclasses can override this method
        return frozenset()

    def _additional_validations(self, value: str):
        pass

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("Value cannot be empty")

        _assert_standard_safe_syntax(value)

        if not all(c in self._valid_chars() for c in value):
            raise ValueError("Value contains invalid characters")

        self._additional_validations(value)

        self._value = value

    def __bool__(self) -> bool:
        return True

    def __fspath__(self) -> str:
        return self._value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, self.__class__):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._value!r})"

    def __str__(self) -> str:
        return self._value

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type: Any, _handler: Any) -> Any:
        import pydantic_core.core_schema as core_schema

        return core_schema.no_info_plain_validator_function(
            lambda v: cls(v) if isinstance(v, str) else v,
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, _core_schema: Any, _handler: Any) -> dict[str, Any]:
        return {"type": "string"}


class StatePath:
    """An absolute path within the managed storage system.

    Tracks the managed root directory and ensures all derived paths remain within it.
    The initial path (from get_*_dir) is the root; paths created via / carry it forward.
    """

    __slots__ = ("_path", "_root")

    def __init__(self, path: pathlib.Path, root: pathlib.Path | None = None) -> None:
        if not path.is_absolute():
            raise ValueError("Path must be absolute")
        resolved = path.resolve()
        managed_root = (root or path).resolve()
        if not resolved.is_relative_to(managed_root):
            raise ValueError(f"Path {resolved} is not within managed root {managed_root}")
        self._path = path
        self._root = root or path

    def __fspath__(self) -> str:
        return str(self._path)

    def __str__(self) -> str:
        return str(self._path)

    def __truediv__(self, other: str | pathlib.Path | SafeType) -> StatePath:
        validated = other if isinstance(other, SafeType) else RelPath(str(other))
        return StatePath(self._path / validated, self._root)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, self.__class__):
            return self._path == other._path
        return NotImplemented

    @property
    def parent(self) -> StatePath:
        """Root-safe parent - cannot traverse outside the original root path"""
        return StatePath(self._path.parent, self._root)

    @property
    def path(self) -> pathlib.Path:
        return self._path

    @property
    def root(self) -> pathlib.Path:
        return self._root

    @property
    def name(self) -> str:
        return self._path.name

    @classmethod
    def __get_pydantic_core_schema__(cls, _source_type: Any, _handler: Any) -> Any:
        import pydantic_core.core_schema as core_schema

        def _validate(v: Any) -> Any:
            if isinstance(v, str):
                return cls(pathlib.Path(v))
            if isinstance(v, dict) and v.get("__type__") == "StatePath":
                return cls(pathlib.Path(v["path"]), pathlib.Path(v["root"]))
            return v

        def _serialize(v: Any) -> Any:
            return {"__type__": "StatePath", "path": str(v.path), "root": str(v.root)}

        return core_schema.no_info_plain_validator_function(
            _validate,
            serialization=core_schema.plain_serializer_function_ser_schema(_serialize),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, _core_schema: Any, _handler: Any) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "__type__": {"type": "string", "const": "StatePath"},
                "path": {"type": "string", "format": "path"},
                "root": {"type": "string", "format": "path"},
            },
            "required": ["__type__", "path", "root"],
        }


class Alphanumeric(SafeType):
    @classmethod
    def _valid_chars(cls) -> frozenset[str]:
        # default is the base set; subclasses can override this method
        return _ALPHANUM


class CommitteeKey(Alphanumeric):
    pass


class Numeric(SafeType):
    @classmethod
    def _valid_chars(cls) -> frozenset[str]:
        # default is the base set; subclasses can override this method
        return _NUMERIC


class ProjectKey(Alphanumeric):
    """A project name that has been validated for safety."""


class ReleaseKey(Alphanumeric):
    """A release name composed from a validated ProjectKey and VersionKey."""

    @classmethod
    def _valid_chars(cls) -> frozenset[str]:
        return _VERSION_CHARS


class RelPath(SafeType):
    """A relative file path that has been validated for safety."""

    @classmethod
    def _valid_chars(cls) -> frozenset[str]:
        return _PATH_CHARS

    def _additional_validations(self, value: str) -> None:
        posix_path = pathlib.PurePosixPath(value)
        windows_path = pathlib.PureWindowsPath(value)
        if posix_path.is_absolute() or windows_path.is_absolute():
            raise ValueError("Absolute paths are not allowed")
        if "//" in value:
            raise ValueError("Path cannot contain empty segments")
        for segment in pathlib.Path(value).parts:
            if segment in (".", ".."):
                raise ValueError("Path cannot contain directory traversal")
            if segment in (".git", ".svn"):
                raise ValueError("Path cannot contain SCM directories")
            if segment.startswith(".") and not segment.startswith(".atr") and segment != ".gitkeep":
                raise ValueError("Path cannot contain dotfiles")

    def as_path(self) -> pathlib.Path:
        """Return the validated path as a pathlib.Path."""
        return pathlib.Path(self._value)

    @classmethod
    def from_path(cls, value: pathlib.Path) -> RelPath:
        return cls(str(value))

    def append(self, path: str | pathlib.Path) -> RelPath:
        return RelPath(f"{self!s}/{path!s}")

    def prepend(self, path: str | pathlib.Path) -> RelPath:
        return RelPath(f"{path!s}/{self!s}")

    def removeprefix(self, prefix: str):
        return RelPath(self._value.removeprefix(prefix))

    def __lt__(self, other):
        if not isinstance(other, RelPath):
            return NotImplemented
        return self.as_path() < other.as_path()

    def __le__(self, other):
        if not isinstance(other, RelPath):
            return NotImplemented
        return self.as_path() <= other.as_path()

    def __gt__(self, other):
        if not isinstance(other, RelPath):
            return NotImplemented
        return self.as_path() > other.as_path()

    def __ge__(self, other):
        if not isinstance(other, RelPath):
            return NotImplemented
        return self.as_path() >= other.as_path()


class RevisionNumber(Numeric):
    """A revision number that has been validated for safety."""


class VersionKey(Alphanumeric):
    """A version name that has been validated for safety"""

    @classmethod
    def _valid_chars(cls) -> frozenset[str]:
        return _VERSION_CHARS

    def _additional_validations(self, value: str):
        if value[0] not in _ALPHANUM:
            raise ValueError("A version should start with an alphanumeric character")
        if value[-1] not in _ALPHANUM:
            raise ValueError("A version should end with an alphanumeric character")


def _empty_to_none(v: object) -> object:
    if isinstance(v, str) and (not v):
        return None
    return v


def _strip_slashes_or_none(v: object) -> object:
    """Strip leading/trailing slashes from a path string; return None if only slashes."""
    if isinstance(v, str):
        stripped = v.strip("/")
        if not stripped:
            return None
        return stripped
    return v


type OptionalAlphanumeric = Annotated[
    Alphanumeric | None,
    pydantic.BeforeValidator(_strip_slashes_or_none),
]

type OptionalRelPath = Annotated[
    RelPath | None,
    pydantic.BeforeValidator(_strip_slashes_or_none),
]

type OptionalRevisionNumber = Annotated[
    RevisionNumber | None,
    pydantic.BeforeValidator(_empty_to_none),
]


def _assert_standard_safe_syntax(value: str) -> None:
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("Value must be NFC-normalized")

    for c in value:
        cat = unicodedata.category(c)
        if cat[0] == "C":
            raise ValueError("Value contains disallowed control/format character")

        if cat[0] == "M":
            raise ValueError("Value contains disallowed combining mark")
