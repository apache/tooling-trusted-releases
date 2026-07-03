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
import re
import subprocess

import pytest
import yaml

import atr.jwtoken as jwtoken
import atr.models.args as args
import atr.models.sql as sql
import atr.post.distribution as distribution
import atr.tasks.gha as gha
import tests.contract.jqbody as jqbody

_ACTIONS = [
    "atr-distribute-test/action.yml",
    "record-atr-distribution/action.yml",
    "release-on-atr/action.yml",
    "upload-to-atr/action.yml",
]
_AUDIENCE_RE = re.compile(r'&audience=https://\$\{\w+\}/"')
_JWT_MARKER = "ACTIONS_ID_TOKEN_REQUEST_URL"
_PIN_RE = re.compile(r"apache/tooling-actions/upload-to-atr@([0-9a-f]{40})")


def test_audience_format():
    root = _actions_root()
    assert jwtoken._GITHUB_OIDC_AUDIENCE.endswith("/")
    checked = 0
    for path in [root / action for action in _ACTIONS] + _distribute_workflows(root):
        for step in jqbody.steps(yaml.safe_load(path.read_text())):
            run = step.get("run") if isinstance(step, dict) else None
            if (not isinstance(run, str)) or (_JWT_MARKER not in run):
                continue
            checked += 1
            assert _AUDIENCE_RE.search(run), f"{path.name} builds a non-conforming OIDC audience URL"
    assert checked


def test_dispatch_contract():
    root = _actions_root()
    non_str = []
    for platform, staging in _dispatch_cases():
        task_args = args.DistributionWorkflow(
            namespace="org.apache.example",
            package="example",
            version="0.0.1",
            staging=staging,
            project_key="example",
            version_key="0.0.1",
            phase="compose",
            asf_uid="user",
            committee_key="example",
            platform=platform.name,
            arguments={},
            name="example-0.0.1",
        )
        workflow, payload = gha.dispatch_workflow_and_payload(task_args, 32, "atr-dist-example-0.0.1-x")
        path = root / ".github/workflows" / workflow
        assert path.is_file(), f"{workflow} missing from the actions checkout"
        doc = yaml.safe_load(path.read_text())
        declared = doc.get("on", doc.get(True, {}))["workflow_dispatch"]["inputs"]
        sent = payload["inputs"]
        extras = sorted(set(sent) - set(declared))
        assert not extras, f"{workflow} does not declare dispatch inputs {extras}"
        required = {name for name, spec in declared.items() if spec.get("required")}
        missing = sorted(required - set(sent))
        assert not missing, f"dispatch payload omits required {workflow} inputs {missing}"
        non_str.extend(f"{workflow}: {name}={value!r}" for name, value in sent.items() if not isinstance(value, str))
    assert non_str == [], f"dispatch input values must be strings: {non_str}"


def test_pinned_upload_snippet_fresh():
    source = (pathlib.Path(__file__).parents[2] / "atr/get/upload.py").read_text()
    match = _PIN_RE.search(source)
    assert match, "no pinned upload-to-atr SHA in atr/get/upload.py"
    sha = match.group(1)
    root = _actions_root()
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        pytest.skip("actions checkout is not a git repository")
    if _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("actions checkout is shallow")
    assert _git(root, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0, f"pinned SHA {sha} not in actions history"
    diff = _git(root, "diff", "--quiet", sha, "HEAD", "--", "upload-to-atr/")
    assert diff.returncode == 0, f"upload-to-atr/ changed since pinned SHA {sha}"


def test_run_name_is_atr_id():
    for path in _distribute_workflows(_actions_root()):
        doc = yaml.safe_load(path.read_text())
        assert doc.get("run-name") == "${{ inputs.atr-id }}", f"{path.name} run-name breaks run discovery"


def test_workflow_env_matches_filename():
    mismatches = []
    for path in _distribute_workflows(_actions_root()):
        doc = yaml.safe_load(path.read_text())
        values = []
        for job in doc.get("jobs", {}).values():
            if isinstance(job, dict) and ("WORKFLOW" in job.get("env", {})):
                values.append(job["env"]["WORKFLOW"])
        assert values, f"{path.name} sets no WORKFLOW env"
        mismatches.extend(f"{path.name}: WORKFLOW={value}" for value in values if value != path.name)
    assert mismatches == []


def _actions_root():
    return pathlib.Path(os.environ["TOOLING_ACTIONS_PATH"])


def _dispatch_cases():
    allowlists = ((distribution._AUTOMATED_PLATFORMS, False), (distribution._AUTOMATED_PLATFORMS_STAGE, True))
    cases = []
    for platforms, staging in allowlists:
        for platform in platforms:
            cases.append((sql.DistributionPlatform[platform.name], staging))
    return cases


def _distribute_workflows(root):
    paths = sorted(root.glob(".github/workflows/distribute-*.yml"))
    assert paths, "no distribute workflows in the actions checkout"
    return paths


def _git(root, *arguments):
    return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False)
