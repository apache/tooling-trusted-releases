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
import unittest.mock as mock

import asfquart.base as base
import pytest
import quart
import quart_schema
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.admin as admin
import atr.blueprints.admin
import atr.blueprints.common as common
import atr.blueprints.get
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.get as get
import atr.models.sql as sql
import atr.sessions as sessions
import atr.user as user


async def _add_key(data, committee, fingerprint, created, *, uid=None, deleted=False):
    data.add(
        sql.SigningCertificate(
            fingerprint=fingerprint,
            primary_declared_uid=uid or f"Automated Release Signing <private@{committee}.apache.org>",
            ascii_armored_key="",
            deleted=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC) if deleted else None,
        )
    )
    await data.flush()
    data.add(sql.KeyLink(committee_key=committee, key_fingerprint=fingerprint))
    if created is not None:
        data.add(
            sql.SigningKey(
                fingerprint=fingerprint,
                certificate_fingerprint=fingerprint,
                is_primary=True,
                key_id=fingerprint[-16:],
                algorithm=1,
                length=4096,
                created=datetime.datetime(created, 1, 1, tzinfo=datetime.UTC),
            )
        )


async def _render(_name, **context):
    return str(context["content"])


@pytest.fixture
def app(database, monkeypatch):
    application = quart.Quart(__name__)
    application.config["TESTING"] = True
    quart_schema.QuartSchema(application)
    application.register_error_handler(base.ASFQuartException, errors.action_error_response)
    application.register_blueprint(atr.blueprints.admin._BLUEPRINT)
    application.register_blueprint(atr.blueprints.get._BLUEPRINT)
    monkeypatch.setattr(sessions, "read", mock.AsyncMock(return_value=sql.UserSession(uid="admin")))
    monkeypatch.setattr(user, "is_admin", lambda uid: uid == "admin")
    monkeypatch.setattr(common, "authenticate", mock.AsyncMock())
    monkeypatch.setattr(admin.template, "render", _render)
    return application


@pytest.fixture
async def database(monkeypatch):
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.execute(sqlalchemy.text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    maker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    monkeypatch.setattr(db, "session", maker)
    yield maker
    await engine.dispose()


async def test_keys_preserve_recognition_and_primary_dates(database):
    async with database() as data:
        data.add_all([sql.Committee(key=key) for key in ("good", "missing", "other", "deleted", "secondary")])
        await data.flush()
        await _add_key(data, "good", "a" * 40, 2020, uid="Services RM <private@good.apache.org>")
        await _add_key(data, "missing", "b" * 40, None)
        await _add_key(data, "other", "c" * 40, 2026, uid="Automated Release Signing <private@good.apache.org>")
        await _add_key(data, "deleted", "d" * 40, 2026, deleted=True)
        await _add_key(data, "secondary", "e" * 40, 2026, uid="Ordinary <private@secondary.apache.org>")
        secondary = await data.signing_certificate(fingerprint="e" * 40).get()
        assert secondary is not None
        secondary.secondary_declared_uids = ["Automated Release Signing <private@secondary.apache.org>"]
        good = await data.signing_certificate(fingerprint="a" * 40).get()
        assert good is not None
        good.secondary_declared_uids = [
            "Services RM <private@good.apache.org>",
            "Automated Release Signing <private@good.apache.org>",
        ]
        data.add(sql.KeyLink(committee_key="other", key_fingerprint="a" * 40))
        data.add(
            sql.SigningKey(
                fingerprint="f" * 40,
                certificate_fingerprint="a" * 40,
                is_primary=False,
                key_id="f" * 16,
                algorithm=1,
                length=4096,
                created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            )
        )
        primary = await data.get(sql.SigningKey, "a" * 40)
        assert primary is not None
        primary.revoked = True
        primary.expires = datetime.datetime(2021, 1, 1, tzinfo=datetime.UTC)
        await data.commit()

    keys = await interaction.automated_release_signing_keys()
    assert len(keys) == 2
    assert all(key.created.tzinfo is datetime.UTC for key in keys if key.created)
    assert {(key.committee.key, key.fingerprint, key.created.year if key.created else None) for key in keys} == {
        ("good", "a" * 40, 2020),
        ("missing", "b" * 40, None),
    }
    assert await interaction.automated_release_signing_committees() == {"good", "missing", "test", "tooling"}


async def test_reproducible_tab_empty_and_lazy(app, monkeypatch):
    keys = mock.AsyncMock(return_value=[])
    monkeypatch.setattr(interaction, "automated_release_signing_keys", keys)
    response = await app.test_client().get("/admin/catalog?tab=admin")
    assert response.status_code == 200
    keys.assert_not_awaited()
    response = await app.test_client().get("/admin/catalog?tab=reproducible-builds")
    assert response.status_code == 200
    assert 'colspan="3">No reproducible build signing keys recorded.' in await response.get_data(as_text=True)
    keys.assert_awaited_once()


async def test_reproducible_tab_groups_keys_newest_first_and_links(app, database):
    async with database() as data:
        data.add_all(
            [sql.Committee(key=key, name=name) for key, name in (("a", "A <PMC>"), ("z", "Z"), ("b", "B"))]
            + [sql.Committee(key="missing"), sql.Committee(key="test"), sql.Committee(key="tooling")]
        )
        await data.flush()
        for committee, digit, year in (("a", "1", 2024), ("z", "2", 2026), ("z", "3", 2020), ("b", "4", 2024)):
            await _add_key(data, committee, digit * 40, year)
        await _add_key(data, "missing", "5" * 40, None)
        await data.commit()

    response = await app.test_client().get("/admin/catalog?tab=reproducible-builds&limit=1")
    assert response.status_code == 200
    body = await response.get_data(as_text=True)
    assert body.index("2" * 40) < body.index("3" * 40) < body.index("1" * 40) < body.index("4" * 40)
    assert body.index("4" * 40) < body.index("5" * 40)
    assert 'rowspan="2"' in body
    assert body.count('href="/committees/z"') == 1
    assert "A &lt;PMC&gt;" in body
    assert ">2026-01-01</time>" in body
    assert 'datetime="2026-01-01T00:00:00+00:00"' in body
    assert 'title="' + "2" * 40 + '">' + "2" * 16 + "</a>" in body
    assert "Not recorded" in body
    assert 'href="/committees/test"' not in body
    assert 'href="/committees/tooling"' not in body
    assert 'href="/keys/details/' + "2" * 40 + '"' in body
    routes = app.url_map.bind("localhost")
    assert routes.match("/committees/z")[0] == get.committees.view.endpoint
    assert routes.match("/keys/details/" + "2" * 40)[0] == get.keys.details.endpoint


@pytest.mark.parametrize(("uid", "status"), [(None, 401), ("ordinary", 403)])
async def test_reproducible_tab_requires_admin(app, monkeypatch, uid, status):
    session = sql.UserSession(uid=uid) if uid else None
    monkeypatch.setattr(sessions, "read", mock.AsyncMock(return_value=session))
    keys = mock.AsyncMock()
    monkeypatch.setattr(interaction, "automated_release_signing_keys", keys)
    response = await app.test_client().get("/admin/catalog?tab=reproducible-builds")
    assert response.status_code == status
    keys.assert_not_awaited()
