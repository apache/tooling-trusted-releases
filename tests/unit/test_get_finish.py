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

import inspect
import types
import unittest.mock as mock

import pytest
import quart

import atr.config as config
import atr.db as db
import atr.get.distribution as distribution
import atr.get.finish as finish
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.util as util


@pytest.mark.parametrize(
    ("local", "published", "status", "available", "missing", "message"),
    [
        (False, False, None, False, False, "Cannot announce until SVN and download area publications are complete."),
        (True, False, None, False, False, "Cannot announce until SVN publication is complete."),
        (True, True, None, False, False, ""),
        (False, True, sql.TaskStatus.COMPLETED, True, False, ""),
        *[
            (
                False,
                True,
                status,
                available,
                False,
                "Published to SVN dist. Cannot announce until the files appear in the download area.",
            )
            for status, available in [
                (None, False),
                (sql.TaskStatus.QUEUED, False),
                (sql.TaskStatus.ACTIVE, True),
                (sql.TaskStatus.FAILED, True),
                (sql.TaskStatus.BROKEN, False),
                (sql.TaskStatus.COMPLETED, False),
            ]
        ],
        *[
            (
                local,
                True,
                sql.TaskStatus.COMPLETED,
                True,
                True,
                "This release cannot be announced until 1 distribution is found or ignored.",
            )
            for local in [False, True]
        ],
    ],
)
async def test_announce_gate_reads_publication_status(
    monkeypatch, local, published, status, available, missing, message
) -> None:
    kind = config.SvnPublishKind.LOCAL_REPOSITORY if local else config.SvnPublishKind.ASF_DISTRIBUTION
    monkeypatch.setattr(config, "svn_publish_kind", lambda: kind)
    project, version, revision = safe.ProjectKey("example"), safe.VersionKey("1.0"), safe.RevisionNumber("00001")
    release = types.SimpleNamespace(
        key="example-1.0",
        safe_project_key=project,
        safe_version_key=version,
        safe_latest_revision_number=revision,
        release_policy=None,
        project=sql.Project(key="example"),
        committee=None,
        distributions=[_not_found()] if missing else [],
        expedited=False,
    )
    session = mock.Mock(prevent_confusing_ui_display=mock.AsyncMock(), release=mock.AsyncMock(return_value=release))
    publication = mock.AsyncMock(return_value=types.SimpleNamespace(id=42) if published else None)
    monkeypatch.setattr(finish.interaction, "release_completed_svn_publish_task_for_revision", publication)
    monkeypatch.setattr(finish.interaction, "release_distribution_checks_held", mock.AsyncMock(return_value=False))
    monkeypatch.setattr(finish.interaction, "release_in_flight_svn_publish_task", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(util, "as_url", lambda *_args, **_kwargs: "/distribution/list")
    monkeypatch.setattr(finish.interaction, "release_queued_announcement", mock.AsyncMock(return_value=None))
    monitor = types.SimpleNamespace(status=status, result=results.DownloadsCheck(available=available))
    data = mock.MagicMock()
    data.__aenter__.return_value = data
    data.task.return_value.get = mock.AsyncMock(return_value=monitor if status else None)
    data.artifact.return_value.all = mock.AsyncMock(return_value=[object()] if published else [])
    monkeypatch.setattr(db, "session", lambda: data)
    probe = mock.AsyncMock()
    monkeypatch.setattr(util, "check_propagation", probe)
    async with quart.Quart(__name__).app_context():
        response = await inspect.getclosurevars(inspect.unwrap(finish.downloads)).nonlocals["func"](
            session, "finish", "downloads", project, version, revision
        )
        body = await response.get_json()
        steps_html = body.pop("steps_html")
        assert body == {
            "message": message,
            "ready": not message,
            "svn_html": None,
            "download_page_ready": published,
        }
        # The last step says the release is ready exactly when the gate is open
        assert ("Ready. Use Announce above." in steps_html) == (not message)
    session.release.assert_awaited_once_with(
        project,
        version,
        phase=sql.ReleasePhase.RELEASE_PREVIEW,
        latest_revision_number=revision,
        with_distributions=True,
        with_release_policy=True,
        with_project_release_policy=True,
    )
    publication.assert_awaited_once_with(project, version, revision)
    if published and (not local):
        data.task.assert_called_once_with(task_type=sql.TaskType.DOWNLOADS_CHECK, task_args={"publish_task_id": 42})
    else:
        data.task.assert_not_called()
    probe.assert_not_awaited()


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


@pytest.mark.parametrize("uid", ["member", "manager", "committer"])
async def test_downloads_svn_fragment_preserves_role_gate_and_form_action(monkeypatch, uid) -> None:
    committee = sql.Committee(
        key="example", committee_members=["member"], committers=["manager", "committer"], release_managers=["manager"]
    )
    project, version, revision = safe.ProjectKey("example"), safe.VersionKey("1.0"), safe.RevisionNumber("00001")
    release = types.SimpleNamespace(
        safe_project_key=project,
        safe_version_key=version,
        safe_latest_revision_number=revision,
        latest_revision_number="00001",
        committee=committee,
        project=sql.Project(key="example", committee_key="example"),
        version="1.0",
        download_path_suffix="example/1.0",
        is_embargoed=False,
    )
    session = mock.Mock(
        uid=uid, prevent_confusing_ui_display=mock.AsyncMock(), release=mock.AsyncMock(return_value=release)
    )
    waiting = finish.shared.announce.AnnounceStep(
        "svn", "Published", finish.shared.announce.StepState.TO_DO, "", "Waiting"
    )
    monkeypatch.setattr(finish.shared.announce, "steps", mock.AsyncMock(return_value=[waiting]))
    monkeypatch.setattr(finish, "_render_announce_steps", mock.AsyncMock(return_value=""))
    monkeypatch.setattr(finish.interaction, "release_queued_announcement", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(finish, "_download_page_checkable", mock.AsyncMock(return_value=False))
    monkeypatch.setattr(
        finish.interaction, "release_completed_svn_publish_task_for_revision", mock.AsyncMock(return_value=None)
    )
    monkeypatch.setattr(finish.interaction, "release_in_flight_svn_publish_task", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(
        finish.interaction,
        "release_latest_failed_svn_publish_task",
        mock.AsyncMock(return_value=types.SimpleNamespace(error="<failed>")),
    )
    monkeypatch.setattr(finish.interaction, "latest_catalogued_directory", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(finish.form, "flash_error_data_pop", mock.AsyncMock(return_value={}))
    monkeypatch.setattr(finish.form.utils, "generate_csrf", lambda: "test")
    url = mock.Mock(return_value="/finish/example/1.0")
    monkeypatch.setattr(util, "as_url", url)
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.RELEASE)
    async with quart.Quart(__name__).test_request_context("/finish/downloads/example/1.0/00001"):
        response = await inspect.getclosurevars(inspect.unwrap(finish.downloads)).nonlocals["func"](
            session, "finish", "downloads", project, version, revision
        )
        html = (await response.get_json())["svn_html"]
    if uid == "committer":
        assert html is None
        url.assert_not_called()
        return
    assert "Publish to ASF Distribution Area" in html
    assert "&lt;failed&gt;" in html
    assert 'action="/finish/example/1.0"' in html
    assert 'name="download_path_suffix"' in html
    assert "finish-svn-publishing" not in html
    assert '<span class="input-group-text">dist/release/example/</span>' in html
    assert '<span id="publish-location-tail" class="fw-bold bg-success-subtle px-1 rounded">example/1.0/</span>' in html
    url.assert_any_call(finish.post.finish.selected, project_key="example", version_key="1.0")


def _svn_step(state: str) -> finish.shared.announce.AnnounceStep:
    return finish.shared.announce.AnnounceStep(
        "svn", "Published", finish.shared.announce.StepState(state), "", "Waiting"
    )


def test_render_svn_step_omits_promotion_for_release_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.RELEASE)
    label, detail = finish._render_svn_step(_svn_step("done"), can_publish=True)
    html = str(label) + "".join(str(item) for item in detail)
    assert "dist/release" in html
    assert "dist/atr" not in html
    assert "<a " not in html


def test_render_svn_step_links_to_the_publish_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.RELEASE)
    label, detail = finish._render_svn_step(_svn_step("to_do"), can_publish=True)
    assert 'href="#finish-svn-publish"' in str(label)
    assert detail == ["Not started. Use the publish form below."]
    # Someone who cannot see the form is not sent to it
    label, _detail = finish._render_svn_step(_svn_step("to_do"), can_publish=False)
    assert "<a " not in str(label)


def test_render_svn_step_keeps_the_promotion_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.ATR)
    monkeypatch.setattr(util, "as_url", lambda *_args, **_kwargs: "/docs")
    label, detail = finish._render_svn_step(_svn_step("in_progress"), can_publish=True)
    html = str(label) + "".join(str(item) for item in detail)
    assert "dist/atr" in html
    assert "svn mv" in html
    assert "#finish-svn-publish" not in html


@pytest.mark.parametrize(
    ("blocked", "queued", "state", "text"),
    [
        ("", False, "to_do", "Ready."),
        ("Waiting", True, "in_progress", "Queued."),
        ("Waiting", False, "to_do", "Announce when ready"),
    ],
)
def test_render_announce_step(blocked: str, queued: bool, state: str, text: str) -> None:
    release = types.SimpleNamespace(expedited=False)
    html = str(finish._render_announce_step(release, blocked, queued=queued))
    marker = {"to_do": "Not yet: ", "in_progress": "In progress: "}[state]
    assert marker in html
    assert text in html
    assert "<a " not in html


def test_svn_publish_label_names_release_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.RELEASE)
    assert finish._svn_publish_label() == "Publish to SVN dist/release"
    monkeypatch.setattr(util, "svn_publish_target", lambda: util.SvnPublishTarget.ATR)
    assert finish._svn_publish_label() == "Publish to SVN"


def _not_found() -> sql.Distribution:
    return sql.Distribution(
        release_key="example-1.0",
        platform=sql.DistributionPlatform.MAVEN,
        owner_namespace="org.apache.example",
        package="example",
        version="1.0",
        status=sql.DistributionStatus.CHECKING,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("completed", "in_flight", "available", "svn", "downloads"),
    [
        (False, False, False, "to_do", "to_do"),
        (False, True, False, "in_progress", "to_do"),
        (True, False, False, "done", "in_progress"),
        (True, False, True, "done", "done"),
    ],
)
async def test_announce_steps_show_work_in_progress(
    monkeypatch, completed, in_flight, available, svn, downloads
) -> None:
    monkeypatch.setattr(config, "svn_publish_kind", lambda: config.SvnPublishKind.ASF_DISTRIBUTION)
    publication = types.SimpleNamespace(id=42) if completed else None
    monkeypatch.setattr(
        finish.interaction, "release_completed_svn_publish_task_for_revision", mock.AsyncMock(return_value=publication)
    )
    monkeypatch.setattr(
        finish.interaction,
        "release_in_flight_svn_publish_task",
        mock.AsyncMock(return_value=object() if in_flight else None),
    )
    monitor = types.SimpleNamespace(status=sql.TaskStatus.COMPLETED, result=results.DownloadsCheck(available=available))
    data = mock.MagicMock()
    data.__aenter__.return_value = data
    data.task.return_value.get = mock.AsyncMock(return_value=monitor)
    monkeypatch.setattr(db, "session", lambda: data)
    release = types.SimpleNamespace(
        safe_project_key=safe.ProjectKey("example"),
        safe_version_key=safe.VersionKey("1.0"),
        safe_latest_revision_number=safe.RevisionNumber("00001"),
        distributions=[_not_found()],
    )

    steps = await finish.shared.announce.steps(release)

    assert [step.state.value for step in steps] == [svn, downloads, "in_progress"]
