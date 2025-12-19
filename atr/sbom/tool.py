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

import datetime
from typing import TYPE_CHECKING, Any, Final

import semver

from . import maven, models, utilities

if TYPE_CHECKING:
    from collections.abc import Callable


_KNOWN_TOOLS: Final[dict[str, models.tool.Tool]] = {
    # name in file: ( canonical name, friendly name, version callable )
    "cyclonedx-maven-plugin": models.tool.Tool("cyclonedx-maven-plugin", "CycloneDX Maven Plugin", maven.version_as_of),
    "cyclonedx maven plugin": models.tool.Tool("cyclonedx-maven-plugin", "CycloneDX Maven Plugin", maven.version_as_of),
    "apache trusted releases": models.tool.Tool(
        "apache trusted releases", "Apache Trusted Releases platform", lambda _: utilities.get_atr_version()
    ),
}


def plugin_outdated_version(bom_value: models.bom.Bom) -> list[models.tool.Outdated] | None:
    if bom_value.metadata is None:
        return [models.tool.OutdatedMissingMetadata()]
    timestamp = bom_value.metadata.timestamp
    if timestamp is None:
        # This quite often isn't available
        # We could use the file mtime, but that's extremely heuristic
        # return OutdatedMissingTimestamp()
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    tools: list[Any] = []
    tools_value = bom_value.metadata.tools
    if isinstance(tools_value, list):
        tools = tools_value
    elif tools_value:
        tools = tools_value.components or []
        services = tools_value.services or []
        tools.extend(services)
    errors = []
    for tool in tools:
        name_or_description = (tool.name or tool.description or "").lower()
        if name_or_description not in _KNOWN_TOOLS:
            continue
        if tool.version is None:
            errors.append(models.tool.OutdatedMissingVersion(name=name_or_description))
            continue
        tool_data = _KNOWN_TOOLS[name_or_description]
        available_version = outdated_version_core(timestamp, tool.version, tool_data.version_function)
        if available_version is not None:
            errors.append(
                models.tool.OutdatedTool(
                    name=tool_data.friendly_name,
                    used_version=tool.version,
                    available_version=str(available_version),
                )
            )
    return errors


def outdated_version_core(
    isotime: str, version: str, version_as_of: Callable[[str], str | None]
) -> semver.VersionInfo | None:
    expected_version = version_as_of(isotime)
    if expected_version is None:
        return None
    if version == expected_version:
        return None
    expected_version_comparable = version_parse(expected_version)
    version_comparable = version_parse(version)
    if expected_version_comparable is None or version_comparable is None:
        # Couldn't parse the version
        return None
    # If the version used is less than the version available
    if version_comparable < expected_version_comparable:
        # Then note the version available
        return expected_version_comparable
    # Otherwise, the user is using the latest version
    return None


def version_parse(version_str: str) -> semver.VersionInfo | None:
    try:
        return semver.VersionInfo.parse(version_str.lstrip("v"))
    except ValueError:
        return None
