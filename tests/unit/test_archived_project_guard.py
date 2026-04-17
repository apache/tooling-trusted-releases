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

from types import SimpleNamespace

import htpy
import pytest

import atr.models.sql as sql
import atr.shared.web as shared_web
import atr.storage as storage


def test_ensure_project_active_returns_none_for_active() -> None:
    project = SimpleNamespace(key="p", status=sql.ProjectStatus.ACTIVE)
    assert storage.ensure_project_active(project) is None


def test_ensure_project_active_raises_for_retired() -> None:
    project = SimpleNamespace(key="p", status=sql.ProjectStatus.RETIRED)
    with pytest.raises(storage.AccessError, match="archived") as excinfo:
        storage.ensure_project_active(project)
    assert "'p'" in str(excinfo.value)


def test_archived_project_banner_returns_none_for_active() -> None:
    project = SimpleNamespace(status=sql.ProjectStatus.ACTIVE)
    assert shared_web.archived_project_banner(project) is None


def test_archived_project_banner_returns_element_for_retired() -> None:
    project = SimpleNamespace(status=sql.ProjectStatus.RETIRED)
    banner = shared_web.archived_project_banner(project)
    assert isinstance(banner, htpy.Element)
    html = str(banner)
    assert "alert-warning" in html
    assert "archived" in html
