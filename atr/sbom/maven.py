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
import tempfile
from typing import Any, Final

import yyjson

from . import constants

_CACHE_PATH: Final[pathlib.Path] = pathlib.Path(tempfile.gettempdir()) / "sbomtool-cache.json"


def cache_read() -> dict[str, Any]:
    if not constants.maven.USE_CACHE:
        return {}
    try:
        with open(_CACHE_PATH) as file:
            return yyjson.load(file)
    except Exception:
        return {}


def cache_write(cache: dict[str, Any]) -> None:
    if not constants.maven.USE_CACHE:
        return
    try:
        with open(_CACHE_PATH, "w") as file:
            yyjson.dump(cache, file)
    except FileNotFoundError:
        pass


def version_as_of(isotime: str) -> str | None:
    # Given these mappings:
    # {
    #     t3: v3
    #     t2: v2
    #     t1: v1
    # }
    # If the input is after t3, then the output is v3
    # If the input is between t2 and t1, then the output is v2
    # If the input is between t1 and t2, then the output is v1
    # If the input is before t1, then the output is None
    for date, version in sorted(constants.maven.PLUGIN_VERSIONS.items(), reverse=True):
        if isotime >= date:
            return version
    return None
