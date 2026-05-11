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

import atr.models.attestable as models
import atr.models.safe as safe

# TODO: Widen the range of types checked here
QUARANTINE_ARCHIVE_SUFFIXES: Final[tuple[str, ...]] = (".tar.gz", ".tgz", ".zip", ".bz2", ".xz")

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
_QUARANTINE_NORMALISED_SUFFIXES: Final[dict[str, str]] = {".tgz": ".tar.gz"}


def deduplicate_quarantine_archives(
    archive_paths: list[safe.RelPath], path_to_hash: dict[safe.RelPath, str]
) -> list[tuple[safe.RelPath, str]]:
    seen: dict[tuple[str, str], safe.RelPath] = {}
    for rel_path in archive_paths:
        content_hash = path_to_hash[rel_path]
        basename = _path_basename(str(rel_path))
        suffix = _quarantine_archive_suffix(basename)
        if suffix is None:
            continue
        key = (content_hash, suffix)
        if (key not in seen) or (str(rel_path) < str(seen[key])):
            seen[key] = rel_path
    return [(rel_path, path_to_hash[rel_path]) for rel_path in sorted(seen.values(), key=str)]


def detect_archives_requiring_quarantine(
    path_to_hash: dict[safe.RelPath, str], previous_attestable: models.Attestable | None
) -> list[safe.RelPath]:
    quarantine_paths: list[safe.RelPath] = []
    for path_key, hash_ref in path_to_hash.items():
        basename = _path_basename(str(path_key))
        suffix = _quarantine_archive_suffix(basename)
        if suffix is None:
            continue

        if previous_attestable is None:
            quarantine_paths.append(path_key)
            continue

        historical_hash_entry = previous_attestable.hashes.get(hash_ref)
        if historical_hash_entry is None:
            quarantine_paths.append(path_key)
            continue

        if "basenames" not in historical_hash_entry.model_fields_set:
            quarantine_paths.append(path_key)
            continue

        if not any(_quarantine_archive_suffix(b) == suffix for b in historical_hash_entry.basenames):
            quarantine_paths.append(path_key)

    return quarantine_paths


def validate_directory(directory: pathlib.Path) -> list[str]:
    # TODO: Report errors using the whole relative path, not just the filename
    errors: list[str] = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            errors.append(f"{path.name}: Symbolic links are not allowed")
            continue
        if path.is_file():
            if error := _validate_file(path):
                errors.append(error)
    return errors


def _archive_link_escapes_root(member_name: str, link_target: str | None, *, is_hardlink: bool = False) -> bool:
    if link_target is None:
        return False
    if link_target.startswith("/"):
        return True

    link_parts = pathlib.PurePosixPath(link_target).parts
    base_parts = () if is_hardlink else pathlib.PurePosixPath(member_name).parent.parts
    depth = 0
    for part in (*base_parts, *link_parts):
        if part in ("", ".", "/"):
            continue
        if part == "..":
            if depth == 0:
                return True
            depth -= 1
        else:
            depth += 1
    return False


def _archive_member_has_path_traversal(path_key: str) -> bool:
    if path_key.startswith("/"):
        return True

    return ".." in pathlib.PurePosixPath(path_key).parts


def _path_basename(path_key: str) -> str:
    return path_key.rsplit("/", maxsplit=1)[-1]


def _quarantine_archive_suffix(filename: str) -> str | None:
    lower_name = filename.lower()
    for suffix in QUARANTINE_ARCHIVE_SUFFIXES:
        if lower_name.endswith(suffix):
            return _QUARANTINE_NORMALISED_SUFFIXES.get(suffix, suffix)
    return None


def _suffix(filename: str) -> str:
    name = filename.lower()
    for compound in _COMPOUND_SUFFIXES:
        if name.endswith(compound):
            return compound
    return pathlib.Path(name).suffix


def _validate_file(path: pathlib.Path) -> str | None:
    # TODO: Report errors using the whole relative path, not just the filename
    suffix = _suffix(path.name)
    if suffix not in _EXPECTED:
        return None
    if path.stat().st_size == 0:
        return f"{path.name}: Empty file"
    try:
        results = puremagic.magic_file(path)
    except puremagic.PureError:
        return f"{path.name}: Unidentified file format (expected {suffix})"
    detected_types = {r.mime_type for r in results}
    if not (detected_types & _EXPECTED[suffix]):
        primary = results[0].mime_type if results else "unknown"
        return f"{path.name}: Content mismatch (expected {suffix}, detected {primary})"
    return None
