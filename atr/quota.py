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

"""Limits on what a release and a project can hold, applied by the storage writers."""

import dataclasses
import enum
from collections.abc import Mapping
from typing import Final

import atr.models.safe as safe
import atr.util as util

# Infra's distribution policy gives 1GB as the most the ASF will host for one artifact
LARGE_FILE_BYTES: Final[int] = 1024 * 1024 * 1024
# Also what rsync is told to skip
MAX_FILE_BYTES: Final[int] = 2_000_000_000
MAX_PROJECT_BYTES: Final[int] = 25 * 1024 * 1024 * 1024
MAX_PROJECT_RELEASES: Final[int] = 10
MAX_RELEASE_BYTES: Final[int] = 10 * 1024 * 1024 * 1024
MAX_RELEASE_FILES: Final[int] = 5000


# Each value is also the help ID of its section in the troubleshooting page
class Kind(enum.StrEnum):
    FILE_TOO_LARGE = "file-too-large"
    PROJECT_TOO_LARGE = "project-too-large"
    PROJECT_TOO_MANY_RELEASES = "project-too-many-releases"
    RELEASE_TOO_LARGE = "release-too-large"
    RELEASE_TOO_MANY_FILES = "release-too-many-files"
    SERVER_LOW_ON_SPACE = "server-low-on-space"


# Sizes are refused with a 413, counts with a 400, and a server short of space with a 507
_STATUS: Final[Mapping[Kind, int]] = {
    Kind.FILE_TOO_LARGE: 413,
    Kind.PROJECT_TOO_LARGE: 413,
    Kind.PROJECT_TOO_MANY_RELEASES: 400,
    Kind.RELEASE_TOO_LARGE: 413,
    Kind.RELEASE_TOO_MANY_FILES: 400,
    Kind.SERVER_LOW_ON_SPACE: 507,
}


@dataclasses.dataclass(frozen=True)
class Limits:
    project_bytes: int = MAX_PROJECT_BYTES
    project_releases: int = MAX_PROJECT_RELEASES
    release_bytes: int = MAX_RELEASE_BYTES
    release_files: int = MAX_RELEASE_FILES


@dataclasses.dataclass(frozen=True)
class Violation:
    kind: Kind
    message: str

    @property
    def help_id(self) -> str:
        return self.kind.value

    @property
    def status(self) -> int:
        return _STATUS[self.kind]


DEFAULT_LIMITS: Final[Limits] = Limits()


def free_space_violation(free_bytes: int, minimum_bytes: int) -> Violation | None:
    """Return a violation if the state volume is too full to take a new revision."""
    if free_bytes >= minimum_bytes:
        return None
    return Violation(Kind.SERVER_LOW_ON_SPACE, "ATR is low on storage space, and is not accepting new files.")


def project_bytes_violation(held_bytes: int, adding_bytes: int, limits: Limits) -> Violation | None:
    """Return a violation if new content would take the project's releases in progress past its limit."""
    # A revision that adds no content is allowed even when the project is over its limit
    if (adding_bytes == 0) or ((held_bytes + adding_bytes) <= limits.project_bytes):
        return None
    return Violation(
        Kind.PROJECT_TOO_LARGE,
        f"The project's releases in progress would hold {util.format_file_size(held_bytes + adding_bytes)},"
        f" and its limit is {util.format_file_size(limits.project_bytes)}.",
    )


def project_releases_violation(in_progress: int, limits: Limits) -> Violation | None:
    """Return a violation if the project can't start another release."""
    if in_progress < limits.project_releases:
        return None
    return Violation(
        Kind.PROJECT_TOO_MANY_RELEASES,
        f"The project has {in_progress:,} releases in progress, and its limit is {limits.project_releases:,}.",
    )


def release_violation(path_to_size: Mapping[safe.RelPath, int], limits: Limits) -> Violation | None:
    """Return the first limit that the files of a revision would break, if any."""
    for path, size in sorted(path_to_size.items(), key=lambda item: str(item[0])):
        if size > MAX_FILE_BYTES:
            return Violation(
                Kind.FILE_TOO_LARGE,
                f"{path} is {util.format_file_size(size)}, and the limit for one file"
                f" is {util.format_file_size(MAX_FILE_BYTES)}.",
            )
    file_count = len(path_to_size)
    if file_count > limits.release_files:
        return Violation(
            Kind.RELEASE_TOO_MANY_FILES,
            f"The release would have {file_count:,} files, and its limit is {limits.release_files:,}.",
        )
    total = sum(path_to_size.values())
    if total > limits.release_bytes:
        return Violation(
            Kind.RELEASE_TOO_LARGE,
            f"The release would be {util.format_file_size(total)}, and its limit"
            f" is {util.format_file_size(limits.release_bytes)}.",
        )
    return None
