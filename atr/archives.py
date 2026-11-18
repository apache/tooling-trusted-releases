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
import contextlib
import dataclasses
import enum
import hashlib
import os
import posixpath
from typing import Final

import aiofiles.os
import exarch

import atr.config as config
import atr.log as log
import atr.models.safe as safe

MAX_ARCHIVE_MEMBERS: Final[int] = 100_000


class ExtractionError(Exception):
    pass


class MemberDifference(enum.Enum):
    CONTENTS = "contents"
    EXECUTABLE = "executable"
    ONLY_LEFT = "only_left"
    ONLY_RIGHT = "only_right"
    TYPE = "type"


@dataclasses.dataclass(frozen=True)
class TreeComparison:
    differing: list[tuple[str, MemberDifference]]
    matching: list[str]


def compare_trees(
    left_archive: safe.StatePath,
    left_extract_dir: safe.StatePath,
    right_archive: safe.StatePath,
    right_extract_dir: safe.StatePath,
) -> TreeComparison | None:
    left = _tree_members(left_archive, left_extract_dir)
    right = _tree_members(right_archive, right_extract_dir)
    if (left is None) or (right is None):
        return None
    differing: list[tuple[str, MemberDifference]] = []
    matching: list[str] = []
    for path in sorted(left.keys() | right.keys()):
        difference = _member_difference(left.get(path), right.get(path))
        if difference is None:
            matching.append(path)
        else:
            differing.append((path, difference))
    return TreeComparison(differing, matching)


def extract(
    archive_path: safe.StatePath,
    extract_dir: str,
    max_size: int,
    chunk_size: int = 4096,
    track_files: bool | set[str] = False,
) -> tuple[int, list[str]]:
    # chunk_size retained for signature stability; exarch manages buffering internally.
    del chunk_size
    log.info(f"Extracting {archive_path} to {extract_dir}")

    cfg = _build_extraction_config(max_size)
    extracted_paths: list[str] = []

    if isinstance(track_files, set) and track_files:
        try:
            manifest = exarch.list_archive(str(archive_path), cfg)
        except Exception as exc:
            raise ExtractionError(f"Failed to list archive: {exc}") from exc
        for entry in manifest.entries:
            if os.path.basename(entry.path) in track_files:
                extracted_paths.append(entry.path)

    try:
        report = exarch.extract_archive(str(archive_path), extract_dir, cfg)
    except exarch.QuotaExceededError as exc:
        raise ExtractionError(f"Extraction exceeded size limit of {max_size} bytes: {exc}") from exc
    except Exception as exc:
        raise ExtractionError(f"Failed to extract archive: {exc}") from exc

    return report.bytes_written, extracted_paths


def extraction_config() -> exarch.SecurityConfig:
    """Shared secure extraction config for Apache release archives.

    Permits symlinks (that remain within the extraction root), world-writable files,
    and `.env` files — all of which appear legitimately in real Apache releases.
    Rejects hardlinks, absolute paths, and enforces size/depth/compression quotas.
    """
    return _build_extraction_config(config.get().MAX_EXTRACT_SIZE)


async def list_archive(file_path: safe.StatePath) -> list[str] | None:
    """Attempt to list contents of supported archive files."""
    if not await aiofiles.os.path.isfile(file_path):
        return None
    if not file_path.name.endswith((".tar.gz", ".tgz", ".zip")):
        return None

    def _read_archive() -> list[str] | None:
        with contextlib.suppress(Exception):
            manifest = exarch.list_archive(str(file_path), extraction_config())
            return sorted(entry.path for entry in manifest.entries)
        return None

    return await asyncio.to_thread(_read_archive)


def single_root_dir(directory: safe.StatePath) -> str | None:
    subdirs: list[str] = []
    has_other = False
    with os.scandir(directory) as scan:
        for entry in scan:
            if entry.name.startswith("._"):
                continue
            if entry.is_dir(follow_symlinks=False):
                subdirs.append(entry.name)
            else:
                has_other = True
    if (len(subdirs) == 1) and (not has_other):
        return subdirs[0]
    return None


def _build_extraction_config(max_extract_size: int) -> exarch.SecurityConfig:
    cfg = (
        exarch.SecurityConfig()
        .with_max_file_size(max_extract_size)
        .with_max_total_size(max_extract_size)
        .with_max_file_count(MAX_ARCHIVE_MEMBERS)
        .with_max_compression_ratio(500.0)
        .with_max_path_depth(32)
        # Escaping the root is still disallowed by exarch even when symlinks are allowed
        .with_allow_symlinks(True)
        .with_allow_hardlinks(False)
        .with_allow_absolute_paths(False)
        # Too many Apache archives ship world-writable files for us to reject them;
        # we chmod after extraction anyway via util.chmod_files.
        .with_allow_world_writable(True)
    )
    banned = cfg.banned_path_components
    cfg.banned_path_components = [component for component in banned if component.lower() != ".env"]
    return cfg


def _executable_paths(archive: safe.StatePath, root: str) -> set[str]:
    manifest = exarch.list_archive(str(archive), extraction_config())
    prefix = f"{root}/"
    return {
        posixpath.normpath(entry.path).removeprefix(prefix)
        for entry in manifest.entries
        if (entry.entry_type == "File") and ((entry.mode or 0) & 0o111)
    }


def _member_difference(left: tuple[str, bytes] | None, right: tuple[str, bytes] | None) -> MemberDifference | None:
    if right is None:
        return MemberDifference.ONLY_LEFT
    if left is None:
        return MemberDifference.ONLY_RIGHT
    if left == right:
        return None
    if (left[0] == "symlink") != (right[0] == "symlink"):
        return MemberDifference.TYPE
    if left[1] != right[1]:
        return MemberDifference.CONTENTS
    return MemberDifference.EXECUTABLE


def _tree_member(full_path: str, rel_path: str, executable: set[str]) -> tuple[str, bytes] | None:
    if os.path.islink(full_path):
        return "symlink", os.fsencode(os.readlink(full_path))
    if not os.path.isfile(full_path):
        return None
    with open(full_path, "rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").digest()
    return ("executable" if (rel_path in executable) else "file"), digest


def _tree_members(archive: safe.StatePath, extract_dir: safe.StatePath) -> dict[str, tuple[str, bytes]] | None:
    root = single_root_dir(extract_dir)
    if root is None:
        return None
    executable = _executable_paths(archive, root)
    root_path = extract_dir.path / root
    members: dict[str, tuple[str, bytes]] = {}
    for dir_path, dir_names, file_names in os.walk(root_path):
        for name in dir_names + file_names:
            full_path = os.path.join(dir_path, name)
            rel_path = os.path.relpath(full_path, root_path)
            member = _tree_member(full_path, rel_path, executable)
            if member is not None:
                members[rel_path] = member
    return members
