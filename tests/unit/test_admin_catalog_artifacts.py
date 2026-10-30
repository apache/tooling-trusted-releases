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

import datetime
import pathlib
import unittest.mock as mock

import jinja2
import pytest
import quart
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.admin as admin_module
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.sessions as sessions
import atr.template as template
import atr.util as util

_TEMPLATES = pathlib.Path(admin_module.__file__).parent / "templates"
_MANAGED = "maven-jar-plugin-3.5.1-source-release.zip"
_TWIN = "maven-jar-plugin-3.5.1-source-release.tar.gz"


async def _seed(data: db.Session) -> None:
    # One ATR-managed row stamped with the default directory ATR published to, and one
    # unmanaged row the watcher catalogued - the #1775 shape
    data.add(sql.Committee(key="maven", name="Maven", is_podling=False))
    data.add(sql.Project(key="maven-jar-plugin", name="Maven JAR Plugin", committee_key="maven"))
    data.add(sql.Project(key="maven-doxia", name="Maven Doxia", committee_key="maven"))
    await data.commit()
    data.add(
        sql.Release(
            key="maven-jar-plugin-3.5.1",
            project_key="maven-jar-plugin",
            version="3.5.1",
            cycle_key="maven-jar-plugin-default",
            phase=sql.ReleasePhase.RELEASE,
            created=datetime.datetime(2026, 7, 22, tzinfo=datetime.UTC),
        )
    )
    await data.commit()
    for name, directory, managed in ((_MANAGED, "maven/maven-jar-plugin-3.5.1", True), (_TWIN, "maven/plugins", False)):
        data.add(
            sql.Artifact(
                project_key="maven-jar-plugin",
                version="3.5.1",
                artifact_path=name,
                release_key="maven-jar-plugin-3.5.1",
                download_path_suffix=directory,
                signature_path=f"{name}.asc",
                managed=managed,
            )
        )
    await data.commit()


@pytest.fixture
async def app(monkeypatch):
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(sqlalchemy.text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with maker() as data:
        await _seed(data)
    monkeypatch.setattr(db, "session", maker)
    # Form rendering pops any flashed validation errors from the user session, which needs the
    # real asfquart app; there is none here and nothing was flashed
    monkeypatch.setattr(sessions, "form_error_pop", mock.AsyncMock(return_value={}))
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    # The rendered forms carry a CSRF token, which wants a signing key
    app.config["SECRET_KEY"] = "test"
    app.jinja_environment = template.SyncEnvironment
    app.jinja_loader = jinja2.ChoiceLoader(
        [
            jinja2.DictLoader({"layouts/base-admin.html": "{% block content %}{% endblock %}"}),
            jinja2.FileSystemLoader(_TEMPLATES),
        ]
    )
    app.jinja_env.globals.update(as_url=util.as_url, static_url=lambda path: f"/static/{path}")
    yield app
    await engine.dispose()


async def test_artifacts_page_lists_rows_and_greys_out_a_managed_remove(app) -> None:
    async with app.test_request_context("/admin/catalog/maven/project/maven-jar-plugin/release/3.5.1/artifacts"):
        body = await admin_module._catalog_release_artifacts_page(
            safe.CommitteeKey("maven"), safe.ProjectKey("maven-jar-plugin"), safe.VersionKey("3.5.1"), check=False
        )
    assert _MANAGED in body
    assert "maven/maven-jar-plugin-3.5.1" in body
    assert "maven/plugins" in body
    assert f"closer.lua/maven/plugins/{_TWIN}?action=download" in body
    assert f"downloads.apache.org/maven/plugins/{_TWIN}.asc" in body
    assert "artifact/relocate?path=maven-jar-plugin-3.5.1-source-release.zip" in body
    # No check has run, so no badge yet. The managed row's Move and Remove are greyed, the twin's are not
    assert "no answer" not in body
    assert body.count("disabled") == 2
    assert body.count(f"artifact/move?path={_TWIN}") == 1
    assert f"artifact/move?path={_MANAGED}" not in body


async def test_check_marks_each_location_from_its_head_status(app, monkeypatch) -> None:
    async def _statuses(urls):
        return {url: (404 if "maven-jar-plugin-3.5.1/" in url else 200) for url in urls}

    monkeypatch.setattr(admin_module, "_catalog_head_statuses", _statuses)
    async with app.test_request_context("/admin/catalog/maven/project/maven-jar-plugin/release/3.5.1/artifacts"):
        body = await admin_module._catalog_release_artifacts_page(
            safe.CommitteeKey("maven"), safe.ProjectKey("maven-jar-plugin"), safe.VersionKey("3.5.1"), check=True
        )
    # The managed row's artifact and signature both 404 at the stale directory; the twin's two answer
    assert body.count("missing") == 2
    assert body.count(">ok<") == 2


async def test_relocate_page_shows_the_current_location_and_prefills_the_form(app, monkeypatch) -> None:
    monkeypatch.setattr(admin_module, "_catalog_head_statuses", mock.AsyncMock(return_value={}))
    async with app.test_request_context(
        "/admin/catalog/maven/project/maven-jar-plugin/release/3.5.1/artifact/relocate"
    ):
        body = await admin_module._catalog_artifact_relocate_page(
            safe.CommitteeKey("maven"), safe.ProjectKey("maven-jar-plugin"), safe.VersionKey("3.5.1"), _MANAGED
        )
    assert f"Relocate {_MANAGED}" in body
    assert 'value="maven/maven-jar-plugin-3.5.1"' in body
    assert f'value="{_MANAGED}"' in body
    assert "no answer" in body


async def test_move_page_offers_every_other_project(app) -> None:
    async with app.test_request_context("/admin/catalog/maven/project/maven-jar-plugin/release/3.5.1/artifact/move"):
        body = await admin_module._catalog_artifact_move_page(
            safe.CommitteeKey("maven"), safe.ProjectKey("maven-jar-plugin"), safe.VersionKey("3.5.1"), _TWIN
        )
    assert f"Move {_TWIN}" in body
    assert f'value="{_TWIN}"' in body
    assert 'name="target_project"' in body
    assert 'value="maven-doxia"' in body
    assert 'value="maven-jar-plugin"' not in body
