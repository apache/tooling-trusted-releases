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

import asyncio
import enum
import os
import pathlib
import re
import stat
from collections.abc import Callable
from typing import Final

import aiofiles
import aiofiles.os

import atr.analysis as analysis
import atr.detection as detection
import atr.models.safe as safe
import atr.util as util

# Binary markers (of 2553 classified as binary):
#   _bin_binary_token           1378   54.0%
#   _bin_distribution_token      202    7.9%
#   _bin_path_binary            1062   41.6%
#   _bin_platform_token          719   28.2%
#   _bin_path_platform            24    0.9%
#
# Docs markers (of 2553 classified as binary):
#   _doc_extra_token              37    1.4%
#   _doc_filename_token           78    3.1%
#   _doc_path_token               38    1.5%
#
# Source markers (of 3797 classified as source):
#   _src_source_release         1028   27.1%
#   _src_source_token           1917   50.5%
#   _src_path_source             502   13.2%

# Binary markers

# 54.0%
_BIN_BINARY_RE: Final[re.Pattern[str]] = re.compile(r"(^|[-_.])(binary-assembly|binary|bin)(?=[-_.]|$)", re.IGNORECASE)

# 7.9%
_BIN_DISTRIBUTION_TOKENS: Final[frozenset[str]] = frozenset(
    {"classic", "cli", "debug", "lib", "plugin", "plugins", "portable", "sdk", "vagrant", "war"}
)

# 41.6%
_BIN_PATH_PARTS: Final[frozenset[str]] = frozenset({"bin", "binaries", "binary"})

# 0.9% (path), 28.2% (filename)
_BIN_PLATFORM_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "64bit",
        "aarch64",
        "amd64",
        "apk",
        "arm64",
        "arm64bit",
        "darwin",
        "linux",
        "mac",
        "macos",
        "osx",
        "win",
        "windows",
        "win32",
        "win64",
        "x64",
        "x86",
        "x86_64",
    }
)

# Docs markers

# 1.4%
_DOC_EXTRA_TOKENS: Final[frozenset[str]] = frozenset({"apidocs", "markdown", "wikipages"})

# 1.5%
_DOC_PATH_PARTS: Final[frozenset[str]] = frozenset({"doc", "docs", "javadoc", "manual", "site", "wikipages"})

# 3.1%
_DOC_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"(^|[-_.])(doc|docs|javadoc|manual|site)(?=[-_.]|$)", re.IGNORECASE)

# Source markers

# 13.2%
_SRC_PATH_PARTS: Final[frozenset[str]] = frozenset({"source", "src"})

# 27.1%
_SRC_RELEASE_RE: Final[re.Pattern[str]] = re.compile(r"(^|[-_.])source-release(?=[-_.]|$)", re.IGNORECASE)

# 50.5%
_SRC_SOURCE_RE: Final[re.Pattern[str]] = re.compile(r"(^|[-_.])(project|source|sources|src)(?=[-_.]|$)", re.IGNORECASE)

_TOKEN_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[-_.]+")


class FileType(enum.Enum):
    BINARY = "binary"
    DISALLOWED = "disallowed"
    DOCS = "docs"
    METADATA = "metadata"
    SOURCE = "source"


def archive_marker_counts(stem: str, path: pathlib.PurePath) -> tuple[int, int, int]:
    name = pathlib.PurePosixPath(stem).name
    tokens = _get_stem_tokens(name)
    ptokens = _get_path_tokens(path)
    release = _src_source_release(name)
    source = _src_source_token(name)
    explicit = release or source
    source_count = release + source + _src_path_source(ptokens)
    binary_count = (
        _bin_binary_token(name)
        + _bin_distribution_token(tokens, explicit)
        + _bin_path_binary(ptokens)
        + _bin_platform_token(tokens)
        + _bin_path_platform(ptokens)
    )
    docs_count = _doc_extra_token(tokens) + _doc_filename_token(name) + _doc_path_token(ptokens)
    return source_count, binary_count, docs_count


async def classify(
    path: safe.RelPath,
    base_path: safe.StatePath | None = None,
    source_matcher: Callable[[str], bool] | None = None,
    binary_matcher: Callable[[str], bool] | None = None,
    archive_cache_dir: safe.StatePath | None = None,
    _project_key: safe.ProjectKey | None = None,
) -> FileType:
    if (path.as_path().name in analysis.DISALLOWED_FILENAMES) or (
        path.as_path().suffix in analysis.DISALLOWED_SUFFIXES
    ):
        return FileType.DISALLOWED

    path_str = str(path)

    search = re.search(analysis.extension_pattern(), path_str)
    if search and search.group("metadata"):
        return FileType.METADATA

    if any(path_str.endswith(s) for s in analysis.STANDALONE_METADATA_SUFFIXES):
        return FileType.METADATA

    if (not search) or (not search.group("artifact")):
        return FileType.BINARY

    abs_str = str(base_path / path) if (base_path is not None) else None
    if (source_matcher is not None) and (abs_str is not None) and source_matcher(abs_str):
        return FileType.SOURCE
    if (binary_matcher is not None) and (abs_str is not None) and binary_matcher(abs_str):
        return FileType.BINARY
    stem = path_str[: search.start()]
    if not any(path_str.endswith(suffix) for suffix in detection.CLASSIFY_ARCHIVE_SUFFIXES):
        return FileType.BINARY
    if archive_cache_dir is not None:
        marker = await _content_markers(archive_cache_dir)
        if marker is not None:
            return marker
    return classify_from_counts(*archive_marker_counts(stem, path.as_path()))


def classify_from_counts(source_count: int, binary_count: int, docs_count: int) -> FileType:
    if (source_count == 0) and (binary_count == 0):
        if docs_count > 0:
            return FileType.DOCS
        return FileType.SOURCE
    if source_count >= binary_count:
        return FileType.SOURCE
    return FileType.BINARY


def matchers_from_policy(
    source_artifact_paths: list[str],
    binary_artifact_paths: list[str],
    base_path: safe.StatePath,
) -> tuple[Callable[[str], bool] | None, Callable[[str], bool] | None]:
    # TODO: Arguably this should just go into classify(...)
    # Then it could take a release policy or None
    source_matcher = util.create_path_matcher(source_artifact_paths, None, base_path) if source_artifact_paths else None
    binary_matcher = util.create_path_matcher(binary_artifact_paths, None, base_path) if binary_artifact_paths else None
    return source_matcher, binary_matcher


def _bin_binary_token(name: str) -> bool:
    return bool(_BIN_BINARY_RE.search(name))


def _bin_distribution_token(tokens: frozenset[str], explicit_source: bool) -> bool:
    if explicit_source:
        return False
    return bool(tokens & _BIN_DISTRIBUTION_TOKENS)


def _bin_path_binary(ptokens: frozenset[str]) -> bool:
    return bool(ptokens & _BIN_PATH_PARTS)


def _bin_path_platform(ptokens: frozenset[str]) -> bool:
    return bool(ptokens & _BIN_PLATFORM_TOKENS)


def _bin_platform_token(tokens: frozenset[str]) -> bool:
    return bool(tokens & _BIN_PLATFORM_TOKENS)


async def _content_markers(archive_cache_dir: safe.StatePath) -> FileType | None:
    marker = await _npm_content_marker(archive_cache_dir)
    if marker is not None:
        return marker
    return None


def _doc_extra_token(tokens: frozenset[str]) -> bool:
    return bool(tokens & _DOC_EXTRA_TOKENS)


def _doc_filename_token(name: str) -> bool:
    return bool(_DOC_TOKEN_RE.search(name))


def _doc_path_token(ptokens: frozenset[str]) -> bool:
    return bool(ptokens & _DOC_PATH_PARTS)


def _get_path_tokens(path: pathlib.PurePath) -> frozenset[str]:
    tokens: set[str] = set()
    for part in path.parent.parts:
        lower = part.lower()
        tokens.add(lower)
        tokens.update(token for token in _TOKEN_SPLIT_RE.split(lower) if token)
    return frozenset(tokens)


def _get_stem_tokens(name: str) -> frozenset[str]:
    return frozenset(token.lower() for token in _TOKEN_SPLIT_RE.split(name) if token)


async def _npm_content_marker(archive_cache_dir: safe.StatePath) -> FileType | None:
    try:
        entries = [name for name in await aiofiles.os.listdir(archive_cache_dir) if not name.startswith("._")]
    except OSError:
        return None
    if entries != ["package"]:
        return None
    try:
        root_stat = await asyncio.to_thread(os.lstat, (archive_cache_dir / "package").path)
    except OSError:
        return None
    if not stat.S_ISDIR(root_stat.st_mode):
        return None
    package_json_path = (archive_cache_dir / "package" / "package.json").path
    try:
        pj_stat = await asyncio.to_thread(os.lstat, package_json_path)
    except OSError:
        return None
    if not stat.S_ISREG(pj_stat.st_mode):
        return None
    if (pj_stat.st_size <= 0) or (pj_stat.st_size > util.NPM_PACKAGE_JSON_MAX_SIZE):
        return None
    try:
        async with aiofiles.open(package_json_path, "rb") as f:
            raw = await f.read(util.NPM_PACKAGE_JSON_MAX_SIZE)
    except OSError:
        return None
    npm_info, _ = util.parse_npm_pack_info(raw)
    if npm_info is None:
        return None
    return FileType.BINARY


def _src_path_source(ptokens: frozenset[str]) -> bool:
    return bool(ptokens & _SRC_PATH_PARTS)


def _src_source_release(name: str) -> bool:
    return bool(_SRC_RELEASE_RE.search(name))


def _src_source_token(name: str) -> bool:
    return bool(_SRC_SOURCE_RE.search(name)) and (not bool(_SRC_RELEASE_RE.search(name)))
