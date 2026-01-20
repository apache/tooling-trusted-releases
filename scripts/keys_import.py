#!/usr/bin/env python3
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

# Usage: poetry run python3 scripts/keys_import.py

import asyncio
import contextlib
import os
import pathlib
import sys
import time
import traceback
from typing import TYPE_CHECKING

sys.path.append(".")


import atr.config as config
import atr.db as db
import atr.storage as storage
import atr.storage.outcome as outcome
import atr.storage.types as types
import atr.util as util

if TYPE_CHECKING:
    from types import TracebackType


def find_project_root() -> pathlib.Path:
    current = pathlib.Path(__file__).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "atr").is_dir():
            return candidate
    return current.parent


PROJECT_ROOT = find_project_root()


def is_atr_path(path: str) -> bool:
    try:
        resolved = pathlib.Path(path).resolve()
    except OSError:
        return False
    if any((part == ".venv") for part in resolved.parts):
        return False
    try:
        relative = resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    return "atr" in relative.parts


def print_and_flush(message: str) -> None:
    print(message)
    sys.stdout.flush()


def format_exception_location(exc: BaseException) -> str:
    tb = exc.__traceback__
    frames: list[TracebackType] = []
    while tb is not None:
        frames.append(tb)
        tb = tb.tb_next
    if not frames:
        return f"{type(exc).__name__}: {exc}"

    chosen_tb = None
    for frame_tb in reversed(frames):
        filename = frame_tb.tb_frame.f_code.co_filename
        if is_atr_path(filename):
            chosen_tb = frame_tb
            break
    if chosen_tb is None:
        chosen_tb = frames[-1]

    frame = chosen_tb.tb_frame
    filename_path = pathlib.Path(frame.f_code.co_filename).resolve()
    try:
        filename_relative = filename_path.relative_to(PROJECT_ROOT)
    except ValueError:
        filename_relative = filename_path.name
    filename = str(filename_relative)
    lineno = chosen_tb.tb_lineno
    func = frame.f_code.co_name
    return f"{type(exc).__name__} at {filename}:{lineno} in {func}: {exc}"


def log_outcome_errors(outcomes: outcome.List[types.Key], committee_name: str) -> None:
    for error in outcomes.errors():
        fingerprint = "unknown"
        detail_exception: BaseException = error
        if isinstance(error, types.PublicKeyError):
            fingerprint = error.key.key_model.fingerprint
            detail_exception = error.original_error
        elif isinstance(error, BaseException):
            detail_exception = error
        else:
            print_and_flush(f"ERROR! fingerprint={fingerprint} committee={committee_name} detail={error!r}")
            continue

        detail = format_exception_location(detail_exception)
        print_and_flush(f"ERROR! fingerprint={fingerprint} committee={committee_name} detail={detail}")


@contextlib.contextmanager
def log_to_file(conf: config.AppConfig):
    log_file_path = os.path.join(conf.STATE_DIR, "keys-import.log")
    # This should not be required
    os.makedirs(conf.STATE_DIR, exist_ok=True)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with open(log_file_path, "a") as f:
        sys.stdout = f
        sys.stderr = f
        try:
            yield
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


async def keys_import(conf: config.AppConfig, asf_uid: str) -> None:
    # Runs as a standalone script, so we need a worker style database connection
    await db.init_database_for_worker()
    # Print the time and current PID
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} by pid {os.getpid()} ---")
    sys.stdout.flush()

    # Get all email addresses in LDAP
    # We'll discard them when we're finished
    start = time.perf_counter_ns()
    email_to_uid = await util.email_to_uid_map()
    end = time.perf_counter_ns()
    print_and_flush(f"LDAP search took {(end - start) / 1000000} ms")
    print_and_flush(f"Email addresses from LDAP: {len(email_to_uid)}")

    # Get the KEYS file of each committee
    async with db.session() as data:
        committees = await data.committee().all()
    committees = list(committees)
    committees.sort(key=lambda c: c.name.lower())

    urls = []
    for committee in committees:
        if committee.is_podling:
            url = f"https://downloads.apache.org/incubator/{committee.name}/KEYS"
        else:
            url = f"https://downloads.apache.org/{committee.name}/KEYS"
        urls.append(url)

    total_yes = 0
    total_no = 0
    async for url, status, content in util.get_urls_as_completed(urls):
        # For each remote KEYS file, check that it responded 200 OK
        # Extract committee name from URL
        # This works for both /committee/KEYS and /incubator/committee/KEYS
        committee_name = url.rsplit("/", 2)[-2]
        if status != 200:
            print_and_flush(f"{committee_name} error: {status}")
            continue

        # Parse the KEYS file and add it to the database
        # We use a separate storage.write() context for each committee to avoid transaction conflicts
        async with storage.write(asf_uid) as write:
            wafa = write.as_foundation_admin(committee_name)
            keys_file_text = content.decode("utf-8", errors="replace")
            outcomes = await wafa.keys.ensure_associated(keys_file_text)
            log_outcome_errors(outcomes, committee_name)
            yes = outcomes.result_count
            no = outcomes.error_count

            # Print and record the number of keys that were okay and failed
            print_and_flush(f"{committee_name} {yes} {no}")
            total_yes += yes
            total_no += no
    print_and_flush(f"Total okay: {total_yes}")
    print_and_flush(f"Total failed: {total_no}")
    end = time.perf_counter_ns()
    print_and_flush(f"Script took {(end - start) / 1000000} ms")
    print_and_flush("")


async def amain() -> None:
    conf = config.AppConfig()
    with log_to_file(conf):
        try:
            await keys_import(conf, sys.argv[1])
        except Exception as e:
            detail = format_exception_location(e)
            print_and_flush(f"Error: {detail}")
            traceback.print_exc()
            sys.stdout.flush()
            sys.exit(1)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
