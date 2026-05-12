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

import pydantic
import pytest

import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql


def test_check_result_ignore_has_project_key_field() -> None:
    cri = sql.CheckResultIgnore(
        project_key="test",
        release_glob="test-1.0.*",
    )  # pyright: ignore[reportCallIssue]
    assert cri.project_key == "test"


@pytest.mark.parametrize("phase", ["compose", "vote", "finish"])
def test_distribute_ssh_register_args_accepts_trusted_workflow_phase(phase: str) -> None:
    args = api.DistributeSshRegisterArgs.model_validate(
        {
            "publisher": "user",
            "jwt": "token",
            "ssh_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample",
            "phase": phase,
            "asf_uid": "user",
            "project_key": "tooling",
            "version": "0.0.1",
            "task_id": "32",
        }
    )
    assert args.phase == phase


def test_distribute_ssh_register_args_rejects_invalid_trusted_workflow_phase() -> None:
    with pytest.raises(pydantic.ValidationError, match=r"compose|vote|finish"):
        api.DistributeSshRegisterArgs.model_validate(
            {
                "publisher": "user",
                "jwt": "token",
                "ssh_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIexample",
                "phase": "published",
                "asf_uid": "user",
                "project_key": "tooling",
                "version": "0.0.1",
                "task_id": "32",
            }
        )


@pytest.mark.parametrize("phase", ["compose", "vote", "finish"])
def test_distribution_record_from_workflow_args_accepts_trusted_workflow_phase(phase: str) -> None:
    args = api.DistributionRecordFromWorkflowArgs.model_validate(
        {
            "asf_uid": "user",
            "publisher": "user",
            "jwt": "token",
            "project": "tooling",
            "version": "0.0.1",
            "platform": "ARTIFACT_HUB",
            "distribution_owner_namespace": "example",
            "distribution_package": "example",
            "distribution_version": "0.0.1",
            "phase": phase,
            "staging": False,
            "details": False,
            "task_id": "32",
        }
    )
    assert args.phase == phase


def test_distribution_record_from_workflow_args_rejects_invalid_trusted_workflow_phase() -> None:
    with pytest.raises(pydantic.ValidationError, match=r"compose|vote|finish"):
        api.DistributionRecordFromWorkflowArgs.model_validate(
            {
                "asf_uid": "user",
                "publisher": "user",
                "jwt": "token",
                "project": "tooling",
                "version": "0.0.1",
                "platform": "ARTIFACT_HUB",
                "distribution_owner_namespace": "example",
                "distribution_package": "example",
                "distribution_version": "0.0.1",
                "phase": "published",
                "staging": False,
                "details": False,
                "task_id": "32",
            }
        )


def test_ignore_add_args_accepts_all_fields() -> None:
    args = api.IgnoreAddArgs(
        project_key=safe.ProjectKey("example"),
        release_glob="example-1.0.*",
        revision_number="00001",
        checker_glob="atr.tasks.checks.rat.*",
        primary_rel_path_glob="*.tar.gz",
        member_rel_path_glob="*.java",
        status=sql.CheckResultStatusIgnore.SUGGESTION,
        message_glob="*warning*",
    )
    assert args.project_key == safe.ProjectKey("example")
    assert args.release_glob == "example-1.0.*"
    assert args.status == sql.CheckResultStatusIgnore.SUGGESTION


def test_ignore_add_args_rejects_invalid_pattern() -> None:
    with pytest.raises(ValueError):
        api.IgnoreAddArgs(project_key=safe.ProjectKey("test"), checker_glob="^(?=lookahead)$")


def test_ignore_add_args_requires_project_key() -> None:
    args = api.IgnoreAddArgs(project_key=safe.ProjectKey("test"), checker_glob="atr.tasks.*")
    assert str(args.project_key) == "test"


def test_ignore_delete_args_requires_project_key() -> None:
    args = api.IgnoreDeleteArgs(project_key=safe.ProjectKey("test"), id=1)
    assert str(args.project_key) == "test"
    assert args.id == 1
