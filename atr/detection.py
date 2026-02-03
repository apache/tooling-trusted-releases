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
from typing import Final

import puremagic

_BZIP2_TYPES: Final[set[str]] = {"application/x-bzip2"}
_DEB_TYPES: Final[set[str]] = {"application/vnd.debian.binary-package", "application/x-archive"}
_EXE_TYPES: Final[set[str]] = {"application/vnd.microsoft.portable-executable", "application/octet-stream"}
_GZIP_TYPES: Final[set[str]] = {"application/x-gzip", "application/x-tgz"}
_PDF_TYPES: Final[set[str]] = {"application/pdf"}
_RPM_TYPES: Final[set[str]] = {"application/x-rpm"}
_TAR_TYPES: Final[set[str]] = {"application/x-tar"}
_XZ_TYPES: Final[set[str]] = {"application/x-xz"}
_ZIP_TYPES: Final[set[str]] = {"application/zip", "application/java-archive"}

_EXPECTED: Final[dict[str, set[str]]] = {
    ".apk": _ZIP_TYPES,
    ".bin.zip": _ZIP_TYPES,
    ".deb": _DEB_TYPES,
    ".exe": _EXE_TYPES,
    ".jar": _ZIP_TYPES,
    ".nar": _ZIP_TYPES,
    ".nbm": _ZIP_TYPES,
    ".pack.gz": _GZIP_TYPES,
    ".pdf": _PDF_TYPES,
    ".rpm": _RPM_TYPES,
    ".src.tgz": _GZIP_TYPES,
    ".src.zip": _ZIP_TYPES,
    ".tar": _TAR_TYPES,
    ".tar.bz2": _BZIP2_TYPES,
    ".tar.gz": _GZIP_TYPES,
    ".tar.xz": _XZ_TYPES,
    ".tgz": _GZIP_TYPES,
    ".vsix": _ZIP_TYPES,
    ".war": _ZIP_TYPES,
    ".whl": _ZIP_TYPES,
    ".zip": _ZIP_TYPES,
}

_COMPOUND_SUFFIXES: Final = tuple(s for s in _EXPECTED if s.count(".") > 1)


def validate_directory(directory: pathlib.Path) -> list[str]:
    # TODO: Report errors using the whole relative path, not just the filename
    errors: list[str] = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            errors.append(f"{path.name}: Symbolic links are not allowed")
            continue
        if path.is_file():
            if error := validate_file(path):
                errors.append(error)
    return errors


def validate_file(path: pathlib.Path) -> str | None:
    # TODO: Report errors using the whole relative path, not just the filename
    suffix = _suffix(path.name)
    if suffix not in _EXPECTED:
        return f"{path.name}: Unsupported file format ({suffix})"
    if path.stat().st_size == 0:
        return f"{path.name}: Empty file"
    try:
        results = puremagic.magic_file(path)
    except puremagic.PureError:
        return f"{path.name}: Unidentified file format (expected {suffix})"
    detected_types = {r.mime_type for r in results if r.mime_type != ""}
    if not (detected_types & _EXPECTED[suffix]):
        primary = next(iter(detected_types)) if len(detected_types) > 0 else "unknown"
        return f"{path.name}: Content mismatch (expected {suffix}, detected {primary})"
    return None


def _suffix(filename: str) -> str:
    name = filename.lower()
    for compound in _COMPOUND_SUFFIXES:
        if name.endswith(compound):
            return compound
    return pathlib.Path(name).suffix
