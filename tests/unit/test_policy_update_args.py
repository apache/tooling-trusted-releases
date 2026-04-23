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


def _policy_update(**kwargs: object) -> api.PolicyUpdateArgs:
    return api.PolicyUpdateArgs.model_validate({"project": "tooling", **kwargs})


@pytest.mark.parametrize("min_hours", [0, 72, 144])
def test_policy_update_args_accepts_valid_min_hours(min_hours: int) -> None:
    args = _policy_update(min_hours=min_hours)

    assert args.min_hours == min_hours


@pytest.mark.parametrize("min_hours", [-1, 1, 71, 145])
def test_policy_update_args_rejects_invalid_min_hours(min_hours: int) -> None:
    with pytest.raises(pydantic.ValidationError, match="Minimum voting period"):
        _policy_update(min_hours=min_hours)


def test_policy_update_args_rejects_github_repository_name_with_slash() -> None:
    with pytest.raises(pydantic.ValidationError, match="must not contain a slash"):
        _policy_update(github_repository_name="apache/tooling")


@pytest.mark.parametrize(
    "field",
    [
        "github_compose_workflow_path",
        "github_vote_workflow_path",
        "github_finish_workflow_path",
    ],
)
def test_policy_update_args_rejects_invalid_workflow_paths(field: str) -> None:
    with pytest.raises(pydantic.ValidationError, match="must start with"):
        _policy_update(**{field: ["build.yml"]})


def test_policy_update_args_allows_partial_trusted_publishing_update() -> None:
    args = _policy_update(github_repository_branch="release")

    assert args.github_repository_branch == "release"


def test_policy_update_args_validates_normalised_workflow_paths() -> None:
    args = _policy_update(github_vote_workflow_path=[" .github/workflows/vote.yml ", "  "])

    assert args.github_vote_workflow_path == [" .github/workflows/vote.yml ", "  "]


def test_policy_update_args_validates_mailto_addresses() -> None:
    args = _policy_update(mailto_addresses=[" test@example.org "])

    assert args.mailto_addresses == ["test@example.org"]


def test_policy_update_args_rejects_malformed_mailto_addresses() -> None:
    with pytest.raises(pydantic.ValidationError, match="valid email address"):
        _policy_update(mailto_addresses=["not-an-email"])
