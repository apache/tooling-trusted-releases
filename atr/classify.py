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

_SOURCE_STEM: Final[re.Pattern[str]] = re.compile(r"[-_](source-release|sources|source|src)$")


class FileType(enum.Enum):
    BINARY = "binary"
    DISALLOWED = "disallowed"
    METADATA = "metadata"
    SOURCE = "source"


def classify(
    path: pathlib.Path,
    base_path: pathlib.Path | None = None,
    source_matcher: Callable[[str], bool] | None = None,
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
        stem = path_str[: search.start()]
        if _SOURCE_STEM.search(stem):
            return FileType.SOURCE
        if (source_matcher is not None) and (base_path is not None):
            if source_matcher(str(base_path / path)):
                return FileType.SOURCE

    return FileType.BINARY
