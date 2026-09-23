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

import pathlib
import unittest.mock as mock

import htpy
import pytest

import atr.get.compose as compose
import atr.models.attestable as attestable
import atr.models.sql as sql


def test_banner_html_ongoing_only():
    html = compose._banner_html(quarantine_pending=0, ongoing=2)
    assert "background verification tasks" in html
    assert "2" in html


def test_banner_html_pending_quarantine_only():
    html = compose._banner_html(quarantine_pending=1, ongoing=0)
    assert "Archive validation is in progress" in html
    assert 'id="ongoing-tasks-count"' in html


@pytest.mark.parametrize("override", [None, "b" * 40])
async def test_commit_hash_form_shows_default_and_submits_only_override(
    monkeypatch: pytest.MonkeyPatch, override: str | None
):
    release = sql.Release(project_key="test", version="1.0")
    selected = attestable.SourceV2(repository="apache/test", default="a" * 40, override=override)
    monkeypatch.setattr(compose.source, "current", mock.AsyncMock(return_value=selected))
    monkeypatch.setattr(compose.util, "as_url", mock.Mock(return_value="/compose/test/1.0"))
    render = mock.AsyncMock(return_value=htpy.div["form"])
    monkeypatch.setattr(compose.form, "render", render)
    await compose._commit_hash_form_html(release)
    options = render.await_args.kwargs
    submitted = options["model_cls"](csrf_token="test", **options["defaults"])
    assert str(submitted.commit_hash or "") == (override or "")
    details = str(options["pre_submit"])
    assert ("Explicitly supplied" if override else "Assumed from workflow") in details


def test_compose_polling_active_neither():
    assert compose._compose_polling_active(0, 0) is False


def test_compose_polling_active_ongoing_only():
    assert compose._compose_polling_active(2, 0) is True


def test_compose_polling_active_pending_quarantine_only():
    assert compose._compose_polling_active(0, 1) is True


def test_compose_template_wires_status_endpoint():
    template_path = pathlib.Path(__file__).resolve().parents[2] / "atr" / "templates" / "check-selected.html"
    text = template_path.read_text(encoding="utf-8")
    assert "data-status-url=" in text
    assert "get.compose.status_selected" in text
    assert "data-polling-active=" in text
    assert "data-quarantine-pending-count=" in text


def test_empty_files_template_renders_alert():
    template_path = pathlib.Path(__file__).resolve().parents[2] / "atr" / "templates" / "check-selected-files.html"
    text = template_path.read_text(encoding="utf-8")
    assert "alert-info" in text
    assert "does not have any files yet" in text
