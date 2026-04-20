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
import hashlib
import os
import pathlib
from typing import Any, Final

import aiofiles
import blake3

_HASH_CHUNK_SIZE: Final[int] = 4 * 1024 * 1024


def compute_dict_hash(to_hash: dict[Any, Any]) -> str:
    hasher = blake3.blake3()
    for k in sorted(to_hash.keys()):
        hasher.update(str(k).encode("utf-8"))
        hasher.update(str(to_hash[k]).encode("utf-8"))
    return f"blake3:{hasher.hexdigest()}"


async def compute_file_hash(path: str | os.PathLike) -> str:
    path = pathlib.Path(path)
    hasher = blake3.blake3()
    async with aiofiles.open(path, "rb") as f:
        while chunk := await f.read(_HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return f"blake3:{hasher.hexdigest()}"


def compute_file_hash_sync(path: str | os.PathLike) -> str:
    path = pathlib.Path(path)
    hasher = blake3.blake3()
    with open(path, "rb") as f:
        while chunk := f.read(_HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return f"blake3:{hasher.hexdigest()}"


def compute_sha3_256(file_data: bytes) -> str:
    """Compute SHA3-256 hash of file data."""
    return hashlib.sha3_256(file_data).hexdigest()


async def compute_sha512(file_path: pathlib.Path) -> str:
    """Compute SHA-512 hash of a file."""
    sha512 = hashlib.sha512()
    async with aiofiles.open(file_path, "rb") as f:
        while chunk := await f.read(_HASH_CHUNK_SIZE):
            sha512.update(chunk)
    return sha512.hexdigest()


async def compute_sha512_and_content_hash(path: str | os.PathLike) -> tuple[str, str]:
    """Compute SHA-512 hex digest and BLAKE3 content hash in a single streaming pass."""
    path = pathlib.Path(path)
    sha512 = hashlib.sha512()
    blake3_hasher = blake3.blake3()
    async with aiofiles.open(path, "rb") as f:
        while chunk := await f.read(_HASH_CHUNK_SIZE):
            sha512.update(chunk)
            blake3_hasher.update(chunk)
    return sha512.hexdigest(), f"blake3:{blake3_hasher.hexdigest()}"


async def file_sha3(path: str) -> str:
    """Compute SHA3-256 hash of a file."""
    sha3 = hashlib.sha3_256()
    async with aiofiles.open(path, "rb") as f:
        while chunk := await f.read(4096):
            sha3.update(chunk)
    return sha3.hexdigest()


def filesystem_archives_key(content_hash: str) -> str:
    return content_hash.replace(":", "_")
