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
import json
import logging

import atr.log as log

_TEXT = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQIN\n-----END PGP PUBLIC KEY BLOCK-----\n"


def test_keys_submitted_prefers_an_explicit_uid_and_omits_absent_context() -> None:
    log.clear_context()
    captured = _capture()

    log.keys_submitted("api:key/add", _TEXT, asfuid="bob", committee_keys=[])

    entry = json.loads(captured[-1])
    assert entry["request_user_id"] == "bob"
    assert not ({"request_id", "source_ip", "admin_user_id"} & entry.keys())


def test_keys_submitted_records_the_text_its_digest_and_the_request_context() -> None:
    log.clear_context()
    log.add_context(request_id="req-1", source_ip="203.0.113.7", user_id="alice", admin_id="root")
    captured = _capture()
    try:
        log.keys_submitted("web:keys/upload", _TEXT, committee_keys=["tooling"], url="https://example.invalid/KEYS")
    finally:
        log.clear_context()

    entry = json.loads(captured[-1])
    assert entry["source"] == "web:keys/upload"
    assert (entry["request_id"], entry["source_ip"]) == ("req-1", "203.0.113.7")
    assert (entry["request_user_id"], entry["admin_user_id"]) == ("alice", "root")
    assert (entry["committee_keys"], entry["url"]) == (["tooling"], "https://example.invalid/KEYS")
    assert entry["sha3_256"] == hashlib.sha3_256(_TEXT.encode()).hexdigest()
    assert entry["text"] == _TEXT


def _capture() -> list[str]:
    captured: list[str] = []

    class Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger = logging.getLogger("atr.keys.submitted")
    logger.handlers.clear()
    logger.addHandler(Handler())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return captured
