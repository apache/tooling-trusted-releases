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

"""Cycle resolution for releases.

A release belongs to exactly one cycle. For projects with no `cycle_match`
set (today, every project) there's only the "default" cycle. For semver
or calver projects `cycle_match` is a regex applied to the version string
via re.fullmatch; capture-group 1 is the cycle name.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import atr.models.sql as sql

_DEFAULT_CYCLE: Final[str] = "default"


def cycle_name_for_version(project: sql.Project, version: str) -> str:
    """Resolve which cycle a version belongs to.

    Returns the cycle name (not the FK key). Projects with no `cycle_match`
    set always return "default". Otherwise the regex is fullmatched against
    the version string and capture-group 1 is returned.

    Raises ValueError if the version doesn't match, the pattern has no
    capture groups, or capture-group 1 captured the empty string.
    """
    if project.cycle_match is None:
        return _DEFAULT_CYCLE

    match = re.fullmatch(project.cycle_match, version)
    if match is None:
        raise ValueError(f"Version {version!r} does not match cycle_match for project {project.key!r}")

    if not match.groups():
        raise ValueError(f"cycle_match for project {project.key!r} has no capture groups")

    cycle = match.group(1)
    if not cycle:
        raise ValueError(f"cycle_match for project {project.key!r} captured empty string from version {version!r}")

    return cycle
