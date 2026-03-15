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

import enum
import pathlib
import re
from collections.abc import Callable
from typing import Final

import atr.analysis as analysis
import atr.detection as detection
import atr.util as util

_BINARY_STEM: Final[re.Pattern[str]] = re.compile(r"[-_](binary-assembly|binary|bin)(?=[-_]|$)")
_SOURCE_STEM: Final[re.Pattern[str]] = re.compile(r"[-_](source-release|sources|source|src)(?=[-_]|$)")


class FileType(enum.Enum):
    BINARY = "binary"
    DISALLOWED = "disallowed"
    METADATA = "metadata"
    SOURCE = "source"


def classify(
    path: pathlib.Path,
    base_path: pathlib.Path | None = None,
    source_matcher: Callable[[str], bool] | None = None,
    binary_matcher: Callable[[str], bool] | None = None,
) -> FileType:
    if (path.name in analysis.DISALLOWED_FILENAMES) or (path.suffix in analysis.DISALLOWED_SUFFIXES):
        return FileType.DISALLOWED

    path_str = str(path)

    search = re.search(analysis.extension_pattern(), path_str)
    if search and search.group("metadata"):
        return FileType.METADATA

    if any(path_str.endswith(s) for s in analysis.STANDALONE_METADATA_SUFFIXES):
        return FileType.METADATA

    if search and search.group("artifact"):
        abs_str = str(base_path / path) if (base_path is not None) else None
        if (source_matcher is not None) and (abs_str is not None) and source_matcher(abs_str):
            return FileType.SOURCE
        if (binary_matcher is not None) and (abs_str is not None) and binary_matcher(abs_str):
            return FileType.BINARY
        stem = path_str[: search.start()]
        if _SOURCE_STEM.search(stem):
            return FileType.SOURCE
        if _BINARY_STEM.search(stem):
            return FileType.BINARY
        if any(path_str.endswith(suffix) for suffix in detection.QUARANTINE_ARCHIVE_SUFFIXES):
            return FileType.SOURCE

    return FileType.BINARY


def matchers_from_policy(
    source_artifact_paths: list[str],
    binary_artifact_paths: list[str],
    base_path: pathlib.Path,
) -> tuple[Callable[[str], bool] | None, Callable[[str], bool] | None]:
    # TODO: Arguably this should just go into classify(...)
    # Then it could take a release policy or None
    source_matcher = util.create_path_matcher(source_artifact_paths, None, base_path) if source_artifact_paths else None
    binary_matcher = util.create_path_matcher(binary_artifact_paths, None, base_path) if binary_artifact_paths else None
    return source_matcher, binary_matcher
