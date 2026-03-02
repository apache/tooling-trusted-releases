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

import pytest

import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql


def test_check_result_ignore_has_project_name_field() -> None:
    cri = sql.CheckResultIgnore(
        project_name="test",
        release_glob="test-1.0.*",
    )  # pyright: ignore[reportCallIssue]
    assert cri.project_name == "test"


def test_ignore_add_args_accepts_all_fields() -> None:
    args = api.IgnoreAddArgs(
        project_name=safe.ProjectName("example"),
        release_glob="example-1.0.*",
        revision_number="00001",
        checker_glob="atr.tasks.checks.rat.*",
        primary_rel_path_glob="*.tar.gz",
        member_rel_path_glob="*.java",
        status=sql.CheckResultStatusIgnore.WARNING,
        message_glob="*warning*",
    )
    assert args.project_name == safe.ProjectName("example")
    assert args.release_glob == "example-1.0.*"
    assert args.status == sql.CheckResultStatusIgnore.WARNING


def test_ignore_add_args_rejects_invalid_pattern() -> None:
    with pytest.raises(ValueError):
        api.IgnoreAddArgs(project_name=safe.ProjectName("test"), checker_glob="^(?=lookahead)$")


def test_ignore_add_args_requires_project_name() -> None:
    args = api.IgnoreAddArgs(project_name=safe.ProjectName("test"), checker_glob="atr.tasks.*")
    assert str(args.project_name) == "test"


def test_ignore_delete_args_requires_project_name() -> None:
    args = api.IgnoreDeleteArgs(project_name=safe.ProjectName("test"), id=1)
    assert str(args.project_name) == "test"
    assert args.id == 1
