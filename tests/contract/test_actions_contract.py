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

import os
import pathlib

import pytest
import yaml

import atr.models.api as api

_ACTIONS_ENV = "TOOLING_ACTIONS_PATH"

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


def _field_names(literal):
    fields = set()
    for pair in literal[1:-1].split(","):
        name = pair.split(":", 1)[0].strip()
        if name:
            fields.add(name)
    return fields


def _object_literals(run):
    literals = []
    i = 0
    while i < len(run):
        start = run.find("'", i)
        if start == -1:
            break
        end = run.find("'", start + 1)
        if end == -1:
            break
        literal = run[start + 1 : end]
        if literal.startswith("{") and literal.endswith("}"):
            literals.append(literal)
        i = end + 1
    return literals


def _steps(doc):
    runs = doc.get("runs")
    if isinstance(runs, dict):
        steps = runs.get("steps")
        return steps if isinstance(steps, list) else []
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        flat = []
        for job in jobs.values():
            if isinstance(job, dict):
                job_steps = job.get("steps")
                if isinstance(job_steps, list):
                    flat.extend(job_steps)
        return flat
    return []


_PAIRS = _contract_pairs()


@pytest.mark.parametrize(
    ("file", "endpoint", "model"),
    _PAIRS,
    ids=[f"{file} {endpoint}" for file, endpoint, _ in _PAIRS],
)
def test_action_contract(file, endpoint, model):
    root = _actions_root()
    doc = yaml.safe_load((root / file).read_text())
    occurrences = []
    for step in _steps(doc):
        if not isinstance(step, dict):
            continue
        run = step.get("run")
        if not isinstance(run, str) or endpoint not in run:
            continue
        fields = set()
        for literal in _object_literals(run):
            fields |= _field_names(literal)
        occurrences.append(fields)
    assert occurrences, f"no step in {file} posts to {endpoint}"
    model_fields = set(model.model_fields)
    required = {name for name, field in model.model_fields.items() if field.is_required()}
    for fields in occurrences:
        assert fields, f"step in {file} posts to {endpoint} but sends no jq object literal"
        extras = fields - model_fields
        assert not extras, f"{file} sends extra {sorted(extras)} not in {model.__name__}"
        missing = required - fields
        assert not missing, f"{file} omits required {sorted(missing)} for {model.__name__}"
