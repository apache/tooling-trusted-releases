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
import json
import pathlib
import re
import types
import unittest.mock as mock

import asfquart.base as base
import jinja2
import pytest
import quart
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.api as api
import atr.attestable as attestable
import atr.blueprints.common as common
import atr.db as db
import atr.get.manifest as manifest
import atr.get.revisions as revisions
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.template as template
import atr.user as user

_TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "atr" / "templates"


@pytest.fixture
async def app(monkeypatch, tmp_path):
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with maker() as data:
        committee = sql.Committee(key="example", name="Example", committee_members=["member"], committers=["committer"])
        project = sql.Project(key="example", name="Apache Example", committee=committee)
        data.add(
            sql.Release(
                project=project,
                project_key="example",
                version="1.0",
                phase=sql.ReleasePhase.RELEASE,
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        await data.commit()
    monkeypatch.setattr(db, "session", maker)
    monkeypatch.setattr(common, "authenticate_public", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(paths, "get_attestable_dir", lambda: safe.StatePath(tmp_path))
    monkeypatch.setattr(user, "is_admin", lambda uid: uid == "admin")
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    app.jinja_environment = template.SyncEnvironment
    app.jinja_loader = jinja2.ChoiceLoader(
        [
            jinja2.DictLoader(
                {"layouts/base.html": "<title>{% block title %}{% endblock %}</title>{% block content %}{% endblock %}"}
            ),
            jinja2.FileSystemLoader(_TEMPLATES),
        ]
    )
    app.add_url_rule(
        "/api/release/manifest/<project_key>/<version_key>/<revision>",
        endpoint=api.release_manifest.endpoint,
        view_func=api.release_manifest,
    )
    app.add_url_rule(
        "/manifest/<project_key>/<version_key>/<revision_number>",
        endpoint=manifest.selected.endpoint,
        view_func=manifest.selected,
    )
    app.register_error_handler(base.ASFQuartException, _error_response)
    yield app
    await engine.dispose()


@pytest.mark.parametrize("phase", list(sql.ReleasePhase))
@pytest.mark.parametrize("identity", [None, "committer", "member", "asf-member", "admin"])
async def test_embargo_uses_existing_visibility_rules(app, tmp_path, monkeypatch, phase, identity):
    _write_manifest(tmp_path)
    async with db.session() as data:
        release = await data.release(project_key="example", version="1.0").demand(RuntimeError("missing release"))
        release.expedited = True
        release.phase = phase
        await data.commit()
    session = types.SimpleNamespace(uid=identity, is_member=(identity == "asf-member")) if identity else None
    monkeypatch.setattr(common, "authenticate_public", mock.AsyncMock(return_value=session))
    loader = mock.AsyncMock(wraps=attestable.load)
    monkeypatch.setattr(attestable, "load", loader)
    response = await app.test_client().get("/manifest/example/1.0/00001")
    permitted = (phase == sql.ReleasePhase.RELEASE) or (identity in {"member", "asf-member", "admin"})
    assert response.status_code == (200 if permitted else 404)
    if not permitted:
        loader.assert_not_awaited()
    else:
        body = await response.get_data(as_text=True)
        assert ("JSON manifest" in body) == (phase == sql.ReleasePhase.RELEASE)


async def test_empty_manifest(app, tmp_path):
    _write_manifest(tmp_path, payload={"version": 2, "paths": {}, "hashes": {}})
    response = await app.test_client().get("/manifest/example/1.0/00001")
    body = await response.get_data(as_text=True)
    assert response.status_code == 200
    assert "0 files recorded." in body
    assert "This revision contains no files." in body
    assert '<a href="/api/release/manifest/example/1.0/00001">JSON manifest</a>' in body
    assert "File manifest pages" not in body


async def test_exact_revision_without_rows_or_candidate_files(app, tmp_path):
    _write_manifest(tmp_path)
    _write_manifest(tmp_path, revision="00002", payload={"version": 2, "paths": {}, "hashes": {}})
    async with db.session() as data:
        release = await data.release(project_key="example", version="1.0").demand(RuntimeError("missing release"))
        assert release.latest_revision_number is None
        assert await data.revision(release_key=release.key).all() == []
    response = await app.test_client().get("/manifest/example/1.0/00001")
    body = await response.get_data(as_text=True)
    assert response.status_code == 200
    assert "ATR revision <code>00001</code>" in body
    assert "3 files recorded." in body
    assert "bundle.zip" in body
    assert not (tmp_path / "unfinished").exists()
    for number in ("00002", "00003", "1"):
        response = await app.test_client().get(f"/manifest/example/1.0/{number}")
        body = await response.get_data(as_text=True)
        assert response.status_code == (200 if number == "00002" else 404)
        assert "bundle.zip" not in body


@pytest.mark.parametrize("query", ["limit=0", "limit=1001", "limit=no", "offset=-1", "offset=1000001", "offset=no"])
async def test_invalid_pagination(app, tmp_path, query):
    _write_manifest(tmp_path)
    response = await app.test_client().get(f"/manifest/example/1.0/00001?{query}")
    assert response.status_code == 400


async def test_manifest_escapes_paths_and_reports_unknown_size(app, tmp_path):
    _write_manifest(
        tmp_path,
        payload={
            "version": 1,
            "paths": {"<script>alert(1)</script>": "sha256:" + "b" * 64},
            "hashes": {},
        },
    )
    response = await app.test_client().get("/manifest/example/1.0/00001")
    body = await response.get_data(as_text=True)
    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>" not in body
    assert "sha256:" + "b" * 64 in body
    assert "Recorded sizes are unavailable for 1 file." in body
    assert "Unknown" in body


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("phase", list(sql.ReleasePhase))
async def test_manifest_lists_every_path_without_private_metadata(app, tmp_path, version, phase):
    _write_manifest(tmp_path, version=version)
    async with db.session() as data:
        release = await data.release(project_key="example", version="1.0").demand(RuntimeError("missing release"))
        release.phase = phase
        release.is_archived = phase == sql.ReleasePhase.RELEASE
        await data.commit()
    response = await app.test_client().get("/manifest/example/1.0/00001", headers={"Accept": "application/json"})
    body = await response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert "Apache Example" in body
    assert "ATR revision <code>00001</code>" in body
    assert "3 files recorded." in body
    rows = re.findall(r"<tr>(.*?)</tr>", body)
    for index, path in enumerate(("bundle.zip.sha512", "bundle.zip.asc", "bundle.zip")):
        row = next(row for row in rows if f">{path}</code>" in row)
        assert str(123456789 + index) in row
        assert "blake3:" + str(index) * 64 in row
    assert (
        body.index("bundle.zip</code>") < body.index("bundle.zip.asc</code>") < body.index("bundle.zip.sha512</code>")
    )
    assert "user-select-all" in body
    assert "File manifest pages" not in body
    for secret in (
        "private-uploader",
        "private-policy",
        "private-provenance",
        "private-classification",
        "obsolete.zip",
    ):
        assert secret not in body
    assert "download/path" not in body


@pytest.mark.parametrize("payload", [None, "{bad", '{"version": 2, "paths": []}', "[]"])
async def test_missing_or_malformed_manifest(app, tmp_path, payload):
    _write_manifest(tmp_path, revision="00002")
    if payload is not None:
        (tmp_path / "example" / "1.0" / "00001.json").write_text(payload)
    response = await app.test_client().get("/manifest/example/1.0/00001")
    assert response.status_code == 404
    assert "No file manifest is available for this ATR revision" in await response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("query", "limit", "offset", "shown", "previous_offset", "next_offset"),
    [
        ("", 250, 0, 250, None, 250),
        ("?limit=100&offset=100", 100, 100, 100, 0, 200),
        ("?offset=500", 250, 500, 13, 250, None),
        ("?offset=2000", 250, 2000, 0, 500, None),
    ],
)
async def test_pagination(app, tmp_path, query, limit, offset, shown, previous_offset, next_offset):
    digest = "blake3:" + "a" * 64
    _write_manifest(
        tmp_path,
        payload={
            "version": 1,
            "paths": {f"file-{index:04}.txt": digest for index in reversed(range(513))},
            "hashes": {digest: {"size": 7, "uploaders": []}},
        },
    )
    response = await app.test_client().get(f"/manifest/example/1.0/00001{query}")
    body = html.unescape(await response.get_data(as_text=True))
    assert response.status_code == 200
    assert "513 files recorded." in body
    assert '<a href="/api/release/manifest/example/1.0/00001">JSON manifest</a>' in body
    assert [int(index) for index in re.findall(r">file-(\d+)\.txt</code>", body)] == list(range(offset, offset + shown))
    if shown:
        assert f"Showing files {offset + 1} to {offset + shown} of 513." in body
    else:
        assert "This page contains no files." in body
    for label, target in (("Previous", previous_offset), ("Next", next_offset)):
        if target is None:
            assert f'<li class="page-item disabled"><span class="page-link">{label}</span>' in body
        else:
            url = f"/manifest/example/1.0/00001?limit={limit}&offset={target}"
            assert f'href="{url}">{label}</a>' in body


async def test_revision_card_links_exact_manifest(app, monkeypatch):
    monkeypatch.setattr(revisions, "_render_tag_form", lambda *args: None)
    revision = sql.Revision(
        key="example-1.0 00001",
        release_key="example-1.0",
        seq=1,
        number="00001",
        asfuid="member",
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime.now(datetime.UTC),
    )
    page = htm.Block()
    async with app.test_request_context("/"):
        await revisions._render_revision_card(
            page, revision, revisions.FilesDiff(added=[], removed=[], modified=[]), "00002", "preview", "example", "1.0"
        )
    assert '<a href="/manifest/example/1.0/00001">File manifest</a>' in str(page.collect())


async def test_unknown_release_does_not_load_manifest(app, monkeypatch):
    loader = mock.AsyncMock()
    monkeypatch.setattr(attestable, "load", loader)
    response = await app.test_client().get("/manifest/unknown/1.0/00001")
    assert response.status_code == 404
    loader.assert_not_awaited()


def _error_response(error):
    return str(error), error.errorcode


def _write_manifest(tmp_path, *, version=2, revision="00001", payload=None):
    if payload is None:
        path_hashes = {
            name: "blake3:" + str(index) * 64
            for index, name in enumerate(("bundle.zip.sha512", "bundle.zip.asc", "bundle.zip"))
        }
        payload = {
            "version": version,
            "paths": path_hashes
            if version == 1
            else {
                name: {
                    "content_hash": digest,
                    "classification": "private-classification",
                    "provenance": {
                        "generator": "SHA512_from_content",
                        "metadata": {"private-provenance": True},
                    },
                }
                for name, digest in path_hashes.items()
            },
            "hashes": {
                digest: {"size": 123456789 + index, "uploaders": [["private-uploader", "00001"]]}
                for index, digest in enumerate(path_hashes.values())
            },
            "policy": {"private-policy": True},
        }
        payload["hashes"]["blake3:" + "a" * 64] = {"size": 1, "uploaders": [], "basenames": ["obsolete.zip"]}
    directory = tmp_path / "example" / "1.0"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{revision}.json").write_text(json.dumps(payload))
