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

import asyncio
import os
from typing import TYPE_CHECKING

import aiofiles.os

import atr.attestable as attestable
import atr.hashes as hashes
import atr.models.safe as safe
import atr.util as util

if TYPE_CHECKING:
    import pathlib


async def merge(
    base_inodes: dict[str, int],
    base_hashes: dict[str, str],
    prior_dir: pathlib.Path,
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    prior_revision_number: safe.RevisionNumber,
    temp_dir: pathlib.Path,
    n_inodes: dict[str, int],
    n_hashes: dict[str, str],
    n_sizes: dict[str, int],
) -> None:
    # Note: the present function modifies n_hashes and n_sizes in place
    # This happens in the _add_from_prior and _replace_with_prior calls somewhat below
    prior_inodes = await asyncio.to_thread(util.paths_to_inodes, prior_dir)
    prior_hashes: dict[str, str] | None = None

    # Collect implicit directory paths from new (N) files for type conflict detection
    n_dirs: set[str] = set()
    for file_path in n_inodes:
        parts = file_path.split("/")
        for i in range(1, len(parts)):
            n_dirs.add("/".join(parts[:i]))

    all_paths = base_inodes.keys() | prior_inodes.keys() | n_inodes.keys()

    for path in sorted(all_paths):
        b_ino = base_inodes.get(path)
        p_ino = prior_inodes.get(path)
        n_ino = n_inodes.get(path)

        # Case 9: only the prior revision introduced this path
        if (b_ino is None) and (p_ino is not None) and (n_ino is None):
            if _has_type_conflict(path, n_inodes, n_dirs):
                continue
            if await aiofiles.os.path.isdir(temp_dir / path):
                continue
            prior_hashes = await _add_from_prior(
                prior_dir,
                temp_dir,
                path,
                n_hashes,
                n_sizes,
                prior_hashes,
                project_name,
                version_name,
                prior_revision_number,
            )
            continue

        # The prior revision deleted a path that both base and new have
        if (b_ino is not None) and (p_ino is None) and (n_ino is not None):
            if _content_matches(b_ino, n_ino, base_hashes[path], n_hashes[path]):
                # Case 10: new still has the base content so the deletion applies
                await aiofiles.os.remove(temp_dir / path)
                # Update n_hashes and n_sizes in place
                n_hashes.pop(path, None)
                n_sizes.pop(path, None)
            # Case 13: new has different content so new wins
            continue

        # Cases 4, 5, 6, 8, 11, and 15: all three revisions have this path
        if (b_ino is not None) and (p_ino is not None) and (n_ino is not None):
            prior_hashes = await _merge_all_present(
                base_inodes,
                base_hashes,
                prior_dir,
                temp_dir,
                path,
                b_ino,
                p_ino,
                n_ino,
                n_hashes,
                n_sizes,
                prior_hashes,
                project_name,
                version_name,
                prior_revision_number,
            )


async def _add_from_prior(
    prior_dir: pathlib.Path,
    temp_dir: pathlib.Path,
    path: str,
    n_hashes: dict[str, str],
    n_sizes: dict[str, int],
    prior_hashes: dict[str, str] | None,
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    prior_revision_number: safe.RevisionNumber,
) -> dict[str, str] | None:
    target = temp_dir / path
    await asyncio.to_thread(_makedirs_with_permissions, target.parent, temp_dir)
    await aiofiles.os.link(prior_dir / path, target)
    if prior_hashes is None:
        prior_hashes = await attestable.load_paths(project_name, version_name, prior_revision_number)
    # Update n_hashes and n_sizes in place
    if (prior_hashes is not None) and (path in prior_hashes):
        n_hashes[path] = prior_hashes[path]
    else:
        n_hashes[path] = await hashes.compute_file_hash(target)
    stat_result = await aiofiles.os.stat(target)
    n_sizes[path] = stat_result.st_size
    return prior_hashes


def _content_matches(
    b_ino: int,
    n_ino: int,
    b_hash: str,
    n_hash: str,
) -> bool:
    if b_ino == n_ino:
        return True
    return b_hash == n_hash


def _has_type_conflict(path: str, n_inodes: dict[str, int], n_dirs: set[str]) -> bool:
    if path in n_dirs:
        return True
    parts = path.split("/")
    return any("/".join(parts[:i]) in n_inodes for i in range(1, len(parts)))


def _makedirs_with_permissions(target_parent: pathlib.Path, root: pathlib.Path) -> None:
    os.makedirs(target_parent, exist_ok=True)
    current = target_parent
    while current != root:
        os.chmod(current, util.DIRECTORY_PERMISSIONS)
        current = current.parent


async def _merge_all_present(
    _base_inodes: dict[str, int],
    base_hashes: dict[str, str],
    prior_dir: pathlib.Path,
    temp_dir: pathlib.Path,
    path: str,
    b_ino: int,
    p_ino: int,
    n_ino: int,
    n_hashes: dict[str, str],
    n_sizes: dict[str, int],
    prior_hashes: dict[str, str] | None,
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    prior_revision_number: safe.RevisionNumber,
) -> dict[str, str] | None:
    # Cases 6, 8: prior and new share an inode so they already agree
    if p_ino == n_ino:
        return prior_hashes

    # Cases 4, 5: base and prior share an inode so there was no intervening change
    if b_ino == p_ino:
        return prior_hashes

    # Case 11 via inode: base and new share an inode so prior wins
    if b_ino == n_ino:
        return await _replace_with_prior(
            prior_dir,
            temp_dir,
            path,
            n_hashes,
            n_sizes,
            prior_hashes,
            project_name,
            version_name,
            prior_revision_number,
        )

    # Cases 4, 5, 8, 11, 15: all inodes differ, so use hash to distinguish
    b_hash = base_hashes[path]
    n_hash = n_hashes[path]
    if b_hash == n_hash:
        if prior_hashes is None:
            prior_hashes = await attestable.load_paths(project_name, version_name, prior_revision_number)
        if (prior_hashes is not None) and (path in prior_hashes):
            p_hash = prior_hashes[path]
        else:
            p_hash = await hashes.compute_file_hash(prior_dir / path)
        if p_hash != b_hash:
            # Case 11 via hash: base and new have the same content but prior differs
            return await _replace_with_prior(
                prior_dir,
                temp_dir,
                path,
                n_hashes,
                n_sizes,
                prior_hashes,
                project_name,
                version_name,
                prior_revision_number,
            )

    # Cases 4, 5, 8, 15: no merge action needed so new wins
    return prior_hashes


async def _replace_with_prior(
    prior_dir: pathlib.Path,
    temp_dir: pathlib.Path,
    path: str,
    n_hashes: dict[str, str],
    n_sizes: dict[str, int],
    prior_hashes: dict[str, str] | None,
    project_name: safe.ProjectName,
    version_name: safe.VersionName,
    prior_revision_number: safe.RevisionNumber,
) -> dict[str, str] | None:
    await aiofiles.os.remove(temp_dir / path)
    await aiofiles.os.link(prior_dir / path, temp_dir / path)
    if prior_hashes is None:
        prior_hashes = await attestable.load_paths(project_name, version_name, prior_revision_number)
    # Update n_hashes and n_sizes in place
    file_path = temp_dir / path
    if (prior_hashes is not None) and (path in prior_hashes):
        n_hashes[path] = prior_hashes[path]
    else:
        n_hashes[path] = await hashes.compute_file_hash(file_path)
    stat_result = await aiofiles.os.stat(file_path)
    n_sizes[path] = stat_result.st_size
    return prior_hashes
