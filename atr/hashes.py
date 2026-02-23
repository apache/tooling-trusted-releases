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

import pathlib
from typing import Any, Final

import aiofiles
import aiofiles.os
import blake3

_HASH_CHUNK_SIZE: Final[int] = 4 * 1024 * 1024


def compute_dict_hash(to_hash: dict[Any, Any]) -> str:
    hasher = blake3.blake3()
    for k in sorted(to_hash.keys()):
        hasher.update(str(k).encode("utf-8"))
        hasher.update(str(to_hash[k]).encode("utf-8"))
    return f"blake3:{hasher.hexdigest()}"


async def compute_file_hash(path: str | pathlib.Path) -> str:
    path = pathlib.Path(path)
    hasher = blake3.blake3()
    async with aiofiles.open(path, "rb") as f:
        while chunk := await f.read(_HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return f"blake3:{hasher.hexdigest()}"
