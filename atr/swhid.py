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
import dataclasses
import enum
import hashlib
import os
import stat
from typing import Final

import dulwich.objectspec as objectspec
import dulwich.refs as refs
import dulwich.repo as repo

_BLOB: Final = b"blob"
_BROWSER_BASE: Final = "https://archive.softwareheritage.org"
_CHUNK: Final = 1 << 16
_DIGEST_LENGTH: Final = 40
_HEX_DIGITS: Final = frozenset("0123456789abcdef")
_MODE_DIR: Final = b"40000"
_MODE_EXEC: Final = b"100755"
_MODE_FILE: Final = b"100644"
_MODE_LINK: Final = b"120000"
_SCHEME: Final = "swh"
_TREE: Final = b"tree"
_VERSION: Final = "1"

type Path = str | os.PathLike[str]
type Commit = str | bytes


class SwhidError(ValueError):
    pass


class ObjectType(enum.Enum):
    CONTENT = "cnt"
    DIRECTORY = "dir"
    REVISION = "rev"
    RELEASE = "rel"
    SNAPSHOT = "snp"


@dataclasses.dataclass(frozen=True, slots=True)
class Identifier:
    object_type: ObjectType
    digest: str

    def __str__(self) -> str:
        return f"{_SCHEME}:{_VERSION}:{self.object_type.value}:{self.digest}"


@dataclasses.dataclass(frozen=True, slots=True)
class Comparison:
    matched: bool
    left: Identifier
    right: Identifier


def browser_url(identifier: Identifier | str) -> str:
    return f"{_BROWSER_BASE}/{identifier}"


async def commit_directory_id(repo_path: Path, committish: Commit) -> Identifier:
    return await asyncio.to_thread(_commit_directory_id, repo_path, committish)


async def compare_archive_to_commit(archive_root: Path, repo_path: Path, committish: Commit) -> Comparison:
    archive, commit_tree = await asyncio.gather(
        directory_id(archive_root),
        commit_directory_id(repo_path, committish),
    )
    return Comparison(archive == commit_tree, archive, commit_tree)


async def compare_directories(left: Path, right: Path) -> Comparison:
    left_id, right_id = await asyncio.gather(directory_id(left), directory_id(right))
    return Comparison(left_id == right_id, left_id, right_id)


def content_id(data: bytes) -> Identifier:
    return Identifier(ObjectType.CONTENT, _git_object_digest(_BLOB, data).hex())


async def content_id_from_file(path: Path) -> Identifier:
    return await asyncio.to_thread(_content_id_from_file, path)


async def directory_id(root: Path) -> Identifier:
    return await asyncio.to_thread(directory_id_sync, root)


def directory_id_sync(root: Path) -> Identifier:
    digest = _tree_digest(root)
    if digest is None:
        digest = _git_object_digest(_TREE, b"")
    return Identifier(ObjectType.DIRECTORY, digest.hex())


def parse(text: str) -> Identifier:
    if ";" in text:
        raise SwhidError(f"qualified SWHIDs are not supported: {text}")
    parts = text.split(":")
    if len(parts) != 4:
        raise SwhidError(f"malformed SWHID: {text}")
    scheme, version, tag, digest = parts
    if scheme != _SCHEME:
        raise SwhidError(f"expected scheme {_SCHEME!r}: {text}")
    if version != _VERSION:
        raise SwhidError(f"unsupported SWHID version: {version}")
    try:
        object_type = ObjectType(tag)
    except ValueError:
        raise SwhidError(f"unknown object type: {tag}") from None
    if (len(digest) != _DIGEST_LENGTH) or (not _HEX_DIGITS.issuperset(digest)):
        raise SwhidError(f"invalid digest: {digest}")
    return Identifier(object_type, digest)


async def release_id(repo_path: Path, tag: Commit) -> Identifier:
    return await asyncio.to_thread(_release_id, repo_path, tag)


async def revision_id(repo_path: Path, committish: Commit) -> Identifier:
    return await asyncio.to_thread(_revision_id, repo_path, committish)


def _as_bytes(value: Commit) -> bytes:
    if isinstance(value, bytes):
        return value
    return value.encode()


def _blob_digest_of_file(path: Path, size: int) -> bytes:
    hasher = hashlib.sha1(b"blob %d\x00" % size, usedforsecurity=False)
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return hasher.digest()


def _commit_directory_id(repo_path: Path, committish: Commit) -> Identifier:
    _, tree = _commit_id_and_tree(repo_path, committish)
    return Identifier(ObjectType.DIRECTORY, tree)


def _commit_id_and_tree(repo_path: Path, committish: Commit) -> tuple[str, str]:
    with repo.Repo(os.fspath(repo_path)) as store:
        try:
            commit = objectspec.parse_commit(store, _as_bytes(committish))
        except KeyError:
            raise SwhidError(f"commit not found: {committish!r}") from None
        return commit.id.decode(), commit.tree.decode()


def _content_id_from_file(path: Path) -> Identifier:
    size = os.stat(path).st_size
    return Identifier(ObjectType.CONTENT, _blob_digest_of_file(path, size).hex())


def _entry_sort_key(entry: tuple[bytes, bytes, bytes]) -> bytes:
    name, mode, _ = entry
    # Git orders a directory as though its name carried a trailing slash
    if mode == _MODE_DIR:
        return name + b"/"
    return name


def _git_object_digest(kind: bytes, payload: bytes) -> bytes:
    hasher = hashlib.sha1(b"%s %d\x00" % (kind, len(payload)), usedforsecurity=False)
    hasher.update(payload)
    return hasher.digest()


def _release_id(repo_path: Path, tag: Commit) -> Identifier:
    ref = refs.Ref(b"refs/tags/" + _as_bytes(tag))
    with repo.Repo(os.fspath(repo_path)) as store:
        try:
            sha = store.refs[ref]
        except KeyError:
            raise SwhidError(f"tag not found: {tag!r}") from None
        if store[sha].type_name != b"tag":
            raise SwhidError(f"not an annotated tag: {tag!r}")
        return Identifier(ObjectType.RELEASE, sha.decode())


def _revision_id(repo_path: Path, committish: Commit) -> Identifier:
    commit, _ = _commit_id_and_tree(repo_path, committish)
    return Identifier(ObjectType.REVISION, commit)


def _tree_digest(path: Path) -> bytes | None:
    entries: list[tuple[bytes, bytes, bytes]] = []
    with os.scandir(path) as scan:
        for entry in scan:
            name = os.fsencode(entry.name)
            info = entry.stat(follow_symlinks=False)
            mode = info.st_mode
            if stat.S_ISLNK(mode):
                target = os.fsencode(os.readlink(entry.path))
                entries.append((name, _MODE_LINK, _git_object_digest(_BLOB, target)))
            elif stat.S_ISDIR(mode):
                child = _tree_digest(entry.path)
                # Git cannot represent an empty directory, so it is dropped
                if child is not None:
                    entries.append((name, _MODE_DIR, child))
            elif stat.S_ISREG(mode):
                file_mode = _MODE_EXEC if (mode & 0o111) else _MODE_FILE
                blob = _blob_digest_of_file(entry.path, info.st_size)
                entries.append((name, file_mode, blob))
    if not entries:
        return None
    entries.sort(key=_entry_sort_key)
    manifest = b"".join(mode + b" " + name + b"\x00" + digest for name, mode, digest in entries)
    return _git_object_digest(_TREE, manifest)
