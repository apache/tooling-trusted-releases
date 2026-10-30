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

import atr.gates as gates
import atr.models.sql as sql

_GATE = sql.GateConfig(
    repository_id=1,
    repository="o/r",
    committee_key="tooling",
    environment="pypi",
    workflow=".github/workflows/publish.yml",
    event="push",
    ref_type="tag",
    ref_pattern="v[0-9]*",
)
_RUN = {"repository": {"id": 1}, "path": ".github/workflows/publish.yml", "event": "push", "head_branch": "v0.0.1"}


def test_pattern_supported() -> None:
    assert gates.pattern_supported("v[0-9]*")
    assert gates.pattern_supported("release/**/*")
    assert not gates.pattern_supported("v[!0-9]*")
    assert not gates.pattern_supported("v[--!!]*")
    assert not gates.pattern_supported("v[^0-9]*")
    assert not gates.pattern_supported("release/**")
    assert not gates.pattern_supported("./v*")


def test_run_problem() -> None:
    assert gates.run_problem(_GATE, 1, _RUN) is None
    assert gates.run_problem(_GATE, 2, _RUN) is not None
    assert gates.run_problem(_GATE, 1, {**_RUN, "path": ".github/workflows/other.yml"}) is not None
    assert gates.run_problem(_GATE, 1, {**_RUN, "event": "workflow_dispatch"}) is not None
    assert gates.run_problem(_GATE, 1, {**_RUN, "head_branch": "main"}) is not None
    assert gates.run_problem(_GATE, 1, {**_RUN, "head_branch": "v-test"}) is not None
    assert gates.run_problem(_GATE, 1, {**_RUN, "head_branch": "evil/v0.0.1"}) is not None


def test_signature_valid() -> None:
    header = "sha256=dc46983557fea127b43af721467eb9b3fde2338fe3e14f51952aa8478c13d355"
    assert gates.signature_valid("secret", b"body", header)
    assert not gates.signature_valid("secret", b"other", header)
    assert not gates.signature_valid("secret", b"body", "")


def test_tally() -> None:
    eligible = {"a", "b"}
    assert gates.tally({"a": True}, eligible) == sql.GateStatus.PENDING
    assert gates.tally({"a": True, "c": True}, eligible) == sql.GateStatus.PENDING
    assert gates.tally({"a": True, "b": True}, eligible) == sql.GateStatus.APPROVED
    assert gates.tally({"a": True, "b": False}, eligible) == sql.GateStatus.REJECTED
