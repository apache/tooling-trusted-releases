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
import html
import pathlib
import re
import types
import unittest.mock as mock
import urllib.parse as parse

import asfquart.base as base
import jinja2
import pytest
import quart
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.api as api
import atr.blueprints.common as common
import atr.db as db
import atr.get.catalog as catalog
import atr.get.release as release
import atr.models.sql as sql
import atr.template as template
import atr.util as util

_TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "atr" / "templates"


def _link(body: str, label: str) -> str:
    match = re.search(r'<a[^>]*href="([^"]+)"[^>]*>\s*' + re.escape(label) + r"\s*</a>", body)
    assert match is not None
    return html.unescape(match[1])


async def _seed(data: db.Session) -> None:
    data.add(sql.Committee(key="example", name="Example", catalog_reviewed=True))
    await data.commit()
    data.add_all([sql.Project(key=key, name=f"Apache {key}", committee_key="example") for key in ("example", "empty")])
    await data.commit()
    for version, is_archived in (("3.0", False), ("2.0", True)):
        data.add(
            sql.Release(
                key=f"example-{version}",
                project_key="example",
                version=version,
                cycle_key="example-default",
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2026, 1, int(version[0]), tzinfo=datetime.UTC),
                is_archived=is_archived,
            )
        )
    await data.commit()
    for version, name, release_key in (
        ("3.0", "a-released.tar.gz", None),
        ("3.0", "b-released.tar.gz", "example-3.0"),
        ("3.0", "c-released.tar.gz", "example-3.0"),
        ("2.0", "a-archived.tar.gz", None),
        ("2.0", "b-archived.tar.gz", "example-2.0"),
        ("1.0", "orphan.tar.gz", None),
    ):
        data.add(sql.Artifact(project_key="example", version=version, artifact_path=name, release_key=release_key))
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
    monkeypatch.setattr(common, "authenticate_public", mock.AsyncMock(return_value=None))
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    app.jinja_environment = template.SyncEnvironment
    app.jinja_loader = jinja2.ChoiceLoader(
        [
            jinja2.DictLoader(
                {"layouts/base.html": "{% block content %}{% endblock %}{% block javascripts %}{% endblock %}"}
            ),
            jinja2.FileSystemLoader(_TEMPLATES),
        ]
    )
    app.jinja_env.globals.update(
        as_url=util.as_url,
        get=types.SimpleNamespace(catalog=catalog, release=release),
        static_url=lambda path: f"/static/{path}",
    )
    app.add_url_rule("/catalog/<project_key>", endpoint=catalog.project.endpoint, view_func=catalog.project)
    app.add_url_rule("/releases", endpoint=release.releases.endpoint)
    app.add_url_rule("/api/cle/project/<project_key>", endpoint=api.cle_project.endpoint)
    app.add_url_rule("/api/cle/release/<project_key>/<version_key>", endpoint=api.cle_release.endpoint)
    yield app
    await engine.dispose()


@pytest.mark.parametrize(
    ("query", "count", "present", "absent"),
    [
        ("", 6, ["a-released", "b-released", "c-released", "a-archived", "b-archived", "orphan"], []),
        ("?status=released", 3, ["a-released", "b-released", "c-released"], ["a-archived", "b-archived", "orphan"]),
        ("?status=archived", 3, ["a-archived", "b-archived", "orphan"], ["a-released", "b-released", "c-released"]),
    ],
)
async def test_catalog_filters_whole_versions(app, query, count, present, absent):
    response = await app.test_client().get(f"/catalog/example{query}")
    assert response.status_code == 200
    body = await response.get_data(as_text=True)
    assert f"Showing artifacts 1 to {count} of {count}." in body
    for name in present:
        assert f"{name}.tar.gz" in body
    for name in absent:
        assert f"{name}.tar.gz" not in body


@pytest.mark.parametrize("status", ["released", "archived"])
async def test_catalog_paging_preserves_status(app, status):
    client = app.test_client()
    first = await client.get(f"/catalog/example?status={status}&limit=1")
    first_body = await first.get_data(as_text=True)
    assert "Showing artifacts 1 to 1 of 3." in first_body
    second = await client.get(_link(first_body, "Next"))
    body = await second.get_data(as_text=True)
    assert "Showing artifacts 2 to 2 of 3." in body
    assert f"b-{status}.tar.gz" in body
    assert f'aria-current="true">{status.capitalize()}</a>' in body
    for label, offset in (("Previous", "0"), ("Next", "2")):
        assert parse.parse_qs(parse.urlsplit(_link(body, label)).query) == {
            "limit": ["1"],
            "offset": [offset],
            "status": [status],
        }
    for value in ("all", "released", "archived"):
        assert parse.parse_qs(parse.urlsplit(_link(body, value.capitalize())).query) == {
            "limit": ["1"],
            "offset": ["0"],
            "status": [value],
        }
    previous = await client.get(_link(body, "Previous"))
    assert await previous.get_data(as_text=True) == first_body


@pytest.mark.parametrize("status", ["unknown", "Released", ""])
async def test_catalog_rejects_invalid_status(app, status):
    async with app.test_request_context(f"/catalog/example?status={status}"):
        with pytest.raises(base.ASFQuartException) as error:
            await catalog.project(project_key="example")
    assert error.value.errorcode == 400


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/catalog/empty?status=released", "No released artifacts for Apache empty."),
        ("/catalog/example?status=released&offset=200", "No artifacts to show on this page (3 total)."),
    ],
)
async def test_catalog_retains_filters_without_results(app, path, message):
    response = await app.test_client().get(path)
    assert response.status_code == 200
    body = await response.get_data(as_text=True)
    assert message in body
    assert 'aria-current="true">Released</a>' in body
    response = await app.test_client().get(_link(body, "All"))
    assert response.status_code == 200
