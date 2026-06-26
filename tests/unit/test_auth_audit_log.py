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
import json
import logging

import atr.log as log


def test_auth_log_carries_request_id_and_source_ip_from_the_log_context() -> None:
    log.clear_context()
    log.add_context(request_id="req-123", source_ip="203.0.113.7")
    captured = _capture_auth_log()
    try:
        log.auth_failure(type="jwt", reason="bad token", asfuid="bob")

        entry = json.loads(captured[-1])
        assert entry["request_id"] == "req-123"
        assert entry["source_ip"] == "203.0.113.7"
    finally:
        log.clear_context()


def test_auth_log_omits_request_fields_when_no_request_context_is_bound() -> None:
    log.clear_context()
    captured = _capture_auth_log()

    log.auth_success(type="oauth", asfuid="alice")

    entry = json.loads(captured[-1])
    assert "request_id" not in entry
    assert "source_ip" not in entry


def _capture_auth_log() -> list[str]:
    captured: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    logger = logging.getLogger("atr.auth")
    logger.handlers.clear()
    logger.addHandler(_Handler())
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return captured
