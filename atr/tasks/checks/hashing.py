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
import secrets
from typing import Final

import aiofiles

import atr.log as log
import atr.models.results as results
import atr.tasks.checks as checks
import atr.tasks.task as task

# Release policy fields which this check relies on - used for result caching
INPUT_POLICY_KEYS: Final[list[str]] = []
INPUT_EXTRA_ARGS: Final[list[str]] = ["unsuffixed_file_hash"]
CHECK_VERSION: Final[str] = "4"


async def check(args: checks.FunctionArguments) -> results.Results | None:
    """Check the hash of a file."""
    recorder = await args.recorder(CHECK_VERSION)
    if not (hash_abs_path := await recorder.abs_path()):
        return None

    algorithm = hash_abs_path.path.suffix.lstrip(".")
    if algorithm not in {"sha256", "sha512"}:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    # Remove the hash file suffix to get the artifact path
    # This replaces the last suffix, which is what we want
    # >>> pathlib.Path("a/b/c.d.e.f.g").with_suffix(".x")
    # PosixPath('a/b/c.d.e.f.x')
    # >>> pathlib.Path("a/b/c.d.e.f.g").with_suffix("")
    # PosixPath('a/b/c.d.e.f')
    artifact_abs_path = hash_abs_path.path.with_suffix("")

    log.info(
        f"Checking hash ({algorithm}) for {artifact_abs_path} against {hash_abs_path} (rel: {args.primary_rel_path})"
    )

    hash_func = hashlib.sha256 if (algorithm == "sha256") else hashlib.sha512
    hash_obj = hash_func()
    try:
        async with aiofiles.open(artifact_abs_path, mode="rb") as f:
            while chunk := await f.read(4096):
                hash_obj.update(chunk)
    except FileNotFoundError as e:
        await recorder.blocker("Referenced artifact not found", {"error": str(e)})
        return None
    except OSError as e:
        raise task.CheckRetryableError("Unable to read the artifact file", {"error": str(e)}) from e
    computed_hash = hash_obj.hexdigest()

    try:
        async with aiofiles.open(hash_abs_path) as f:
            expected_hash = await f.read()
    except UnicodeDecodeError as e:
        await recorder.blocker(
            "Malformed checksum file",
            {"error": str(e), "hash_file": str(hash_abs_path.path)},
        )
        return None
    except OSError as e:
        raise task.CheckRetryableError("Unable to read the checksum file", {"error": str(e)}) from e

    if (expected_value := _expected_hash_extract(expected_hash, artifact_abs_path.name)) is None:
        await recorder.blocker(
            "Malformed checksum file",
            {"error": "Empty checksum file", "hash_file": str(hash_abs_path.path)},
        )
        return None

    if secrets.compare_digest(computed_hash, expected_value):
        await recorder.note(
            f"Hash ({algorithm}) matches expected value",
            {"computed_hash": computed_hash, "expected_hash": expected_value},
        )
    else:
        await recorder.blocker(
            f"Hash ({algorithm}) mismatch",
            {"computed_hash": computed_hash, "expected_hash": expected_value},
        )
    return None


def _expected_hash_extract(expected_hash: str, artifact_name: str) -> str | None:
    expected_hash_parts = expected_hash.strip().split()
    if not expected_hash_parts:
        return None
    if (len(expected_hash_parts) > 3) and (expected_hash_parts[0] == "SHA512") and (expected_hash_parts[2] == "="):
        # This convention comes from the FreeBSD family of checksum commands
        return expected_hash_parts[3].lower()
    if expected_hash.startswith(artifact_name):
        # Fineract use the format "FILENAME: HASH HASH\n   HASH HASH\n..."
        trimmed = expected_hash.removeprefix(artifact_name + ":")
        return trimmed.replace(" ", "").replace("\n", "").lower()
    # Otherwise, probably in the format "HASH FILENAME\n"
    # TODO: Check the FILENAME part?
    return expected_hash_parts[0].lower()
