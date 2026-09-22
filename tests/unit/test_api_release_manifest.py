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
import json
import unittest.mock as mock

import pytest
import quart
import quart_schema
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.attestable as attestable
import atr.blueprints.api
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths


@pytest.fixture
async def app(monkeypatch, tmp_path):
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with maker() as data:
        committee = sql.Committee(key="example", name="Example", committee_members=["member"])
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
    monkeypatch.setattr(paths, "get_attestable_dir", lambda: safe.StatePath(tmp_path))
    app = quart.Quart(__name__)
    app.config["TESTING"] = True
    app.config["QUART_SCHEMA_PYDANTIC_DUMP_OPTIONS"] = {"mode": "json"}
    quart_schema.QuartSchema(app, openapi_path="/api/openapi.json")
    atr.blueprints.api.register(app)
    yield app
    await engine.dispose()


@pytest.mark.parametrize("phase", list(sql.ReleasePhase))
async def test_embargo_blocks_before_manifest_access(app, tmp_path, monkeypatch, phase):
    _write_manifest(tmp_path)
    async with db.session() as data:
        release = await data.release(project_key="example", version="1.0").demand(RuntimeError("missing release"))
        release.expedited = True
        release.phase = phase
        await data.commit()
    loader = mock.AsyncMock(wraps=attestable.load)
    monkeypatch.setattr(attestable, "load", loader)
    response = await app.test_client().get("/api/release/manifest/example/1.0/00001")
    assert response.status_code == (200 if phase == sql.ReleasePhase.RELEASE else 404)
    if phase != sql.ReleasePhase.RELEASE:
        loader.assert_not_awaited()
        assert (await response.get_json())["error"] == "Release does not exist"


async def test_exact_revision_with_no_rows_or_candidate_files(app, tmp_path):
    _write_manifest(tmp_path)
    _write_manifest(tmp_path, revision="00002", payload={"version": 2, "paths": {}, "hashes": {}})
    async with db.session() as data:
        release = await data.release(project_key="example", version="1.0").demand(RuntimeError("missing release"))
        assert release.latest_revision_number is None
        assert await data.revision(release_key=release.key).all() == []
    response = await app.test_client().get("/api/release/manifest/example/1.0/00001")
    assert response.status_code == 200
    assert len((await response.get_json())["files"]) == 3
    response = await app.test_client().get("/api/release/manifest/example/1.0/00002")
    assert response.status_code == 200
    assert await response.get_json() == {
        "endpoint": "/release/manifest",
        "project": "example",
        "version": "1.0",
        "revision": "00002",
        "files": [],
    }
    for suffix in ("", "/00003", "/1", "/latest"):
        response = await app.test_client().get(f"/api/release/manifest/example/1.0{suffix}")
        assert response.status_code == (400 if suffix == "/latest" else 404)
    assert not (tmp_path / "unfinished").exists()


@pytest.mark.parametrize(("version", "digests"), [(1, False), (2, False), (2, True)])
@pytest.mark.parametrize("phase", list(sql.ReleasePhase))
async def test_manifest_projects_only_public_file_identity(app, tmp_path, version, digests, phase):
    _write_manifest(tmp_path, version=version, digests=digests)
    recorded = (tmp_path / "example" / "1.0" / "00001.json").read_bytes()
    async with db.session() as data:
        release = await data.release(project_key="example", version="1.0").demand(RuntimeError("missing release"))
        release.phase = phase
        release.is_archived = phase == sql.ReleasePhase.RELEASE
        await data.commit()
    response = await app.test_client().get("/api/release/manifest/example/1.0/00001")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert await response.get_json() == {
        "endpoint": "/release/manifest",
        "project": "example",
        "version": "1.0",
        "revision": "00001",
        "files": [
            {
                "path": path,
                "size": 100 + index,
                "digest": "sha3-256:" + str(index) * 64 if digests else None,
                "swhid_dir_inner": "swh:1:dir:" + "d" * 40 if digests and (path == "bundle.zip") else None,
            }
            for index, path in reversed(list(enumerate(("bundle.zip.sha512", "bundle.zip.asc", "bundle.zip"))))
        ],
    }
    assert (tmp_path / "example" / "1.0" / "00001.json").read_bytes() == recorded


@pytest.mark.parametrize("payload", [None, "{bad", '{"version": 2, "paths": []}', "[]"])
async def test_missing_or_malformed_manifest_is_json_404(app, tmp_path, payload):
    _write_manifest(tmp_path, revision="00002")
    if payload is not None:
        (tmp_path / "example" / "1.0" / "00001.json").write_text(payload)
    response = await app.test_client().get("/api/release/manifest/example/1.0/00001")
    assert response.status_code == 404
    assert (await response.get_json())["error"] == "No file manifest is available for this ATR revision"


@pytest.mark.parametrize("query", ["", "?limit=1&offset=1000"])
async def test_no_pagination_or_truncation(app, tmp_path, query):
    digest = "blake3:" + "a" * 64
    _write_manifest(
        tmp_path,
        payload={
            "version": 1,
            "paths": {f"file-{index:04}.txt": digest for index in reversed(range(1251))},
            "hashes": {digest: {"size": 7, "uploaders": []}},
        },
    )
    response = await app.test_client().get(f"/api/release/manifest/example/1.0/00001{query}")
    assert response.status_code == 200
    body = await response.get_json()
    assert body["files"] == [
        {"path": f"file-{index:04}.txt", "size": 7, "digest": None, "swhid_dir_inner": None} for index in range(1251)
    ]


async def test_openapi_declares_required_revision_and_response(app):
    response = await app.test_client().get("/api/openapi.json")
    assert response.status_code == 200
    document = await response.get_json()
    operation = document["paths"]["/api/release/manifest/{project_key}/{version_key}/{revision}"]["get"]
    assert {(param["name"], param["in"], param["required"]) for param in operation["parameters"]} == {
        ("project_key", "path", True),
        ("version_key", "path", True),
        ("revision", "path", True),
    }
    assert not operation.get("security")
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["title"] == "ReleaseManifestResults"
    components = document["components"]["schemas"]
    assert set(schema["properties"]) == {"endpoint", "project", "version", "revision", "files"}
    assert schema["properties"]["files"]["items"]["$ref"].endswith("/ReleaseManifestFile")
    properties = components["ReleaseManifestFile"]["properties"]
    assert set(properties) == {"path", "size", "digest", "swhid_dir_inner"}
    assert {entry["type"] for entry in properties["digest"]["anyOf"]} == {"string", "null"}


async def test_unknown_release_does_not_load_manifest(app, monkeypatch):
    loader = mock.AsyncMock()
    monkeypatch.setattr(attestable, "load", loader)
    response = await app.test_client().get("/api/release/manifest/unknown/1.0/00001")
    assert response.status_code == 404
    assert (await response.get_json())["error"] == "Release does not exist"
    loader.assert_not_awaited()


async def test_unknown_size_and_unusual_paths_preserve_values(app, tmp_path):
    path = "dir/Über <test> & source.zip"
    digest = "sha256:" + "b" * 64
    _write_manifest(tmp_path, payload={"version": 1, "paths": {path: digest}, "hashes": {}})
    response = await app.test_client().get("/api/release/manifest/example/1.0/00001")
    assert response.status_code == 200
    assert (await response.get_json())["files"] == [
        {"path": path, "size": None, "digest": None, "swhid_dir_inner": None}
    ]


def _write_manifest(tmp_path, *, version=2, revision="00001", payload=None, digests=False):
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
                    "provenance": {"generator": "SHA512_from_content", "metadata": {"private-provenance": True}},
                }
                for name, digest in path_hashes.items()
            },
            "hashes": {
                digest: {"size": 100 + index, "uploaders": [["private-uploader", "00001"]]}
                for index, digest in enumerate(path_hashes.values())
            },
            "policy": {"private-policy": True},
        }
        payload["hashes"]["blake3:" + "a" * 64] = {"size": 1, "uploaders": [], "basenames": ["obsolete.zip"]}
        if digests:
            for index, (path, content_hash) in enumerate(path_hashes.items()):
                payload["hashes"][content_hash]["sha3_256"] = str(index) * 64
                if path == "bundle.zip":
                    payload["hashes"][content_hash]["swhid_dir_inner"] = "swh:1:dir:" + "d" * 40
    directory = tmp_path / "example" / "1.0"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{revision}.json").write_text(json.dumps(payload))
