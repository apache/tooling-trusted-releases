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

import enum
import os
import pathlib

import pytest
import yaml

import atr.models.api as api
import tests.contract.jqbody as jqbody

_ACTIONS_ENV = "TOOLING_ACTIONS_PATH"

_FALLBACK_VALUES = {
    "message": "message",
    "project_key": "tooling",
    "run_id": "12345",
    "workflow": "distribute-mavencentral.yml",
}

_STATIC_PAIRS = [
    ("upload-to-atr/action.yml", "/api/publisher/ssh/register", api.PublisherSshRegisterArgs),
    ("atr-distribute-test/action.yml", "/api/publisher/ssh/register", api.PublisherSshRegisterArgs),
    ("record-atr-distribution/action.yml", "/api/publisher/distribution/record", api.PublisherDistributionRecordArgs),
    ("release-on-atr/action.yml", "/api/publisher/vote/resolve", api.PublisherVoteResolveArgs),
    ("release-on-atr/action.yml", "/api/publisher/release/announce", api.PublisherReleaseAnnounceArgs),
]

_DISTRIBUTE_ENDPOINTS = [
    ("/api/distribute/ssh/register", api.DistributeSshRegisterArgs),
    ("/api/distribute/task/status", api.DistributeStatusUpdateArgs),
    ("/api/distribute/record_from_workflow", api.DistributionRecordFromWorkflowArgs),
]


def _actions_root() -> pathlib.Path:
    return pathlib.Path(os.environ[_ACTIONS_ENV])


def _contract_pairs():
    pairs = list(_STATIC_PAIRS)
    root_env = os.environ.get(_ACTIONS_ENV)
    if root_env and pathlib.Path(root_env).is_dir():
        root = pathlib.Path(root_env)
        for path in sorted(root.glob(".github/workflows/distribute-*.yml")):
            rel = path.relative_to(root).as_posix()
            for endpoint, model in _DISTRIBUTE_ENDPOINTS:
                pairs.append((rel, endpoint, model))
    return pairs


def _representative(model, field):
    info = model.model_fields[field]
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    if "example" in extra:
        return _stringy(extra["example"])
    if "examples" in extra:
        return _stringy(extra["examples"][0])
    if field in _FALLBACK_VALUES:
        return _FALLBACK_VALUES[field]
    raise ValueError(f"no example or fallback for {model.__name__}.{field}")


def _stringy(value):
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, str):
        return value
    return str(value)


_PAIRS = _contract_pairs()


@pytest.mark.parametrize(
    ("file", "endpoint", "model"),
    _PAIRS,
    ids=[f"{file} {endpoint}" for file, endpoint, _ in _PAIRS],
)
def test_action_contract(file, endpoint, model):
    root = _actions_root()
    doc = yaml.safe_load((root / file).read_text())
    bodies = []
    for step in jqbody.steps(doc):
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if not isinstance(run, str) or endpoint not in run:
            continue
        bodies.append(jqbody.parse_body(run))
    assert bodies, f"no step in {file} posts to {endpoint}"
    for body in bodies:
        concrete = {}
        for field, value in body.items():
            if value is not jqbody.DYNAMIC:
                concrete[field] = value
            elif field in model.model_fields:
                concrete[field] = _representative(model, field)
            else:
                concrete[field] = "unexpected"
        model.model_validate(concrete)
