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

import types
import unittest.mock as mock

import pytest

import atr.db as db
import atr.get.distribution as distribution
import atr.get.finish as finish
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util


@pytest.mark.parametrize(
    ("page", "release_phase", "expected_phase"),
    [
        (finish, sql.ReleasePhase.RELEASE_PREVIEW, "finish"),
        (distribution, sql.ReleasePhase.RELEASE_PREVIEW, "finish"),
        (distribution, sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, "compose"),
    ],
)
async def test_distribution_tasks_match_phase_on_retained_revision(
    monkeypatch, page, release_phase, expected_phase
) -> None:
    tasks = [
        sql.Task(
            task_type=sql.TaskType.DISTRIBUTION_WORKFLOW,
            task_args={"phase": phase},
            status=sql.TaskStatus.FAILED,
            asf_uid="alice",
            revision_number="00002",
        )
        for phase in ["compose", "vote", "finish"]
    ]
    release = types.SimpleNamespace(key="project-1.0", phase=release_phase, latest_revision_number="00002")
    data = mock.MagicMock()
    data.__aenter__.return_value = data
    data.release.return_value.demand = mock.AsyncMock(return_value=release)
    data.distribution.return_value.all = mock.AsyncMock(return_value=[])
    data.task.return_value.order_by.return_value.all = mock.AsyncMock(return_value=tasks)
    monkeypatch.setattr(db, "session", lambda: data)

    _release_or_distributions, selected = await page._get_page_data(safe.ProjectKey("project"), safe.VersionKey("1.0"))

    assert [task.task_args["phase"] for task in selected] == [expected_phase]
    assert data.task.call_args.kwargs["revision_number"] == "00002"


def test_render_publish_step_omits_promotion_for_release_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.RELEASE)
    html = "".join(str(item) for item in finish._render_publish_step())
    assert "dist/release" in html
    assert "dist/atr" not in html
    assert "<a " not in html


def test_svn_publish_label_names_release_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.RELEASE)
    assert finish._svn_publish_label() == "Publish to SVN dist/release"
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.ATR)
    assert finish._svn_publish_label() == "Publish to SVN"
