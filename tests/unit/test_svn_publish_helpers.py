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

import atr.config as config
import atr.models.args as args
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util


def test_svn_production_target_url() -> None:
    assert util.svn_production_target_url("https://dist.apache.org/repos/dist/release/") is True
    assert util.svn_production_target_url("https://dist.apache.org/repos/dist/atr/") is False


def test_svn_production_target_url_rejects_unsupported_urls() -> None:
    for url in (
        "svn://dist.apache.org/repos/dist/atr",
        "https://dist.apache.org/repos/dist/dev/",
        "https://example.invalid/somewhere",
    ):
        with pytest.raises(ValueError):
            util.svn_production_target_url(url)


def test_svn_publish_models_round_trip() -> None:
    task_args = args.SvnPublish(
        asf_uid="alice",
        project_key="apple",
        version_key="1.0",
        revision_number="00001",
        target_url="https://dist.apache.org/repos/dist/atr/apple",
    )
    result = results.SvnPublish(
        kind="svn_publish",
        svn_revision=12345,
        target_url=task_args.target_url,
        message="ok",
    )
    restored = results.ResultsAdapter.validate_python(result.model_dump())

    assert str(task_args.project_key) == "apple"
    assert task_args.download_path_suffix is None
    assert isinstance(restored, results.SvnPublish)
    assert restored.svn_revision == 12345
    assert sql.TaskType.SVN_PUBLISH.label == "SVN publish"


def test_svn_publish_target_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", "https://dist.apache.org/repos/dist/atr")
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    target_url = util.svn_publish_target_url(committee, safe.RelPath("apple-1.0"))

    assert target_url == "https://dist.apache.org/repos/dist/atr/apple/apple-1.0"


def test_svn_publish_target_url_rejects_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", None)
    committee = sql.Committee(key="apple", name="Apple", is_podling=False)

    with pytest.raises(ValueError):
        util.svn_publish_target_url(committee, None)
