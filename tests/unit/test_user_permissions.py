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

import unittest.mock as mock
from collections.abc import AsyncIterator

import asfquart.session
import pytest
import quart
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.blueprints.common as common
import atr.config as config
import atr.db as db
import atr.get.test as get_test
import atr.get.user as get_user
import atr.models.sql as sql
import atr.sessions as sessions
import atr.web as web


@pytest.fixture
async def sqlite_global_db() -> AsyncIterator[None]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    db._global_atr_sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(
        bind=engine, class_=db.Session, expire_on_commit=False
    )
    yield
    await engine.dispose()
    db._global_atr_sessionmaker = None


@pytest.fixture
def permissions_app():
    app = quart.Quart(__name__)
    app.secret_key = "test-key"
    app.add_url_rule(
        "/user/permissions",
        endpoint=get_user.permissions.endpoint,
        view_func=get_user.permissions,
    )
    return app


@pytest.mark.asyncio
async def test_user_permissions_committer_only(
    permissions_app: quart.Quart, sqlite_global_db: None, monkeypatch: pytest.MonkeyPatch
):
    render_mock = mock.AsyncMock(side_effect=lambda title, content: str(content))
    monkeypatch.setattr("atr.template.blank", render_mock)
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Test)

    async with db.session() as data:
        data.add(
            sql.Committee(
                key="test",
                name="Test Committee",
                is_podling=False,
                keys_mode=sql.KeysMode.AUTOMATIC,
                committee_members=["alice"],
                committers=["alice", "bob"],
                release_managers=[],
            )
        )
        await data.commit()

    session = sql.UserSession(
        uid="bob",
        fullname="Bob Committer",
        member_committees=[],
        participant_committees=["test"],
    )
    monkeypatch.setattr(asfquart.session, "read", mock.AsyncMock(return_value=session))
    monkeypatch.setattr(common, "authenticate", mock.AsyncMock(return_value=web.Committer(session)))

    response = await permissions_app.test_client().get("/user/permissions")
    assert response.status_code == 200
    result = await response.get_data(as_text=True)

    assert "User permissions" in result
    assert "Bob Committer" in result
    assert "What you can do" in result
    assert "What you cannot do" in result
    assert "Cannot cast binding votes" in result
    assert "Cannot start formal release votes" in result
    assert "Committer" in result
    assert "Non-binding" in result


@pytest.mark.asyncio
async def test_user_permissions_pmc_member(
    permissions_app: quart.Quart, sqlite_global_db: None, monkeypatch: pytest.MonkeyPatch
):
    render_mock = mock.AsyncMock(side_effect=lambda title, content: str(content))
    monkeypatch.setattr("atr.template.blank", render_mock)
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Test)

    async with db.session() as data:
        data.add(
            sql.Committee(
                key="test",
                name="Test Committee",
                is_podling=False,
                keys_mode=sql.KeysMode.AUTOMATIC,
                committee_members=["alice"],
                committers=["alice"],
                release_managers=[],
            )
        )
        await data.commit()

    session = sql.UserSession(
        uid="alice",
        fullname="Alice PMC",
        member_committees=["test"],
        participant_committees=["test"],
    )
    monkeypatch.setattr(asfquart.session, "read", mock.AsyncMock(return_value=session))
    monkeypatch.setattr(common, "authenticate", mock.AsyncMock(return_value=web.Committer(session)))

    response = await permissions_app.test_client().get("/user/permissions")
    assert response.status_code == 200
    result = await response.get_data(as_text=True)

    assert "Alice PMC" in result
    assert "PMC member" in result
    assert "Cast binding votes" in result
    assert "Designate and remove project release managers" in result
    assert "Binding" in result


@pytest.mark.asyncio
async def test_user_permissions_designated_release_manager(
    permissions_app: quart.Quart, sqlite_global_db: None, monkeypatch: pytest.MonkeyPatch
):
    render_mock = mock.AsyncMock(side_effect=lambda title, content: str(content))
    monkeypatch.setattr("atr.template.blank", render_mock)
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Test)

    async with db.session() as data:
        data.add(
            sql.Committee(
                key="test",
                name="Test Committee",
                is_podling=False,
                keys_mode=sql.KeysMode.AUTOMATIC,
                committee_members=["alice"],
                committers=["alice", "charlie"],
                release_managers=["charlie"],
            )
        )
        await data.commit()

    session = sql.UserSession(
        uid="charlie",
        fullname="Charlie RM",
        member_committees=[],
        participant_committees=["test"],
    )
    monkeypatch.setattr(asfquart.session, "read", mock.AsyncMock(return_value=session))
    monkeypatch.setattr(common, "authenticate", mock.AsyncMock(return_value=web.Committer(session)))

    response = await permissions_app.test_client().get("/user/permissions")
    assert response.status_code == 200
    result = await response.get_data(as_text=True)

    assert "Charlie RM" in result
    assert "Designated release manager" in result
    assert "Start formal release votes" in result
    assert "Cannot cast binding votes" in result
    assert "Non-binding" in result


@pytest.mark.asyncio
async def test_test_login_endpoints(sqlite_global_db: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Test)
    written_sessions: list[sql.UserSession] = []

    async def mock_write_session(session: sql.UserSession) -> None:
        written_sessions.append(session)

    monkeypatch.setattr("atr.util.write_session", mock_write_session)

    async with db.session() as data:
        data.add(
            sql.Committee(
                key="test",
                name="Test Committee",
                is_podling=False,
                keys_mode=sql.KeysMode.AUTOMATIC,
                committee_members=["test"],
                committers=["test"],
                release_managers=["test"],
            )
        )
        await data.commit()

    app = quart.Quart(__name__)
    app.secret_key = "test-key"
    monkeypatch.setattr(sessions, "read", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(web, "redirect", mock.AsyncMock(return_value="redirected"))

    async with app.test_request_context("/"):
        # 1. Test PMC login
        await get_test.test_login_pmc(None, "test/login-pmc")
        assert len(written_sessions) == 1
        pmc_session = written_sessions[-1]
        assert pmc_session.uid == "test-pmc"
        assert "test" in pmc_session.member_committees

        # 2. Test RM login
        await get_test.test_login_rm(None, "test/login-rm")
        assert len(written_sessions) == 2
        rm_session = written_sessions[-1]
        assert rm_session.uid == "test-rm"
        assert rm_session.member_committees == []
        assert "test" in rm_session.participant_committees

        # Verify committee updated in DB
        async with db.session() as data:
            test_c = await data.committee(key="test").get()
            assert test_c is not None
            assert "test-rm" in test_c.release_managers
            assert "test-rm" in test_c.committers
            assert "test-rm" not in test_c.committee_members

        # 3. Test Committer login
        await get_test.test_login_committer(None, "test/login-committer")
        assert len(written_sessions) == 3
        committer_session = written_sessions[-1]
        assert committer_session.uid == "test-committer"
        assert committer_session.member_committees == []
        assert "test" in committer_session.participant_committees

        # Verify committee updated in DB
        async with db.session() as data:
            test_c = await data.committee(key="test").get()
            assert test_c is not None
            assert "test-committer" not in test_c.release_managers
            assert "test-committer" not in test_c.committee_members
            assert "test-committer" in test_c.committers


@pytest.mark.asyncio
async def test_user_permissions_admin_not_overstated(
    permissions_app: quart.Quart, sqlite_global_db: None, monkeypatch: pytest.MonkeyPatch
):
    render_mock = mock.AsyncMock(side_effect=lambda title, content: str(content))
    monkeypatch.setattr("atr.template.blank", render_mock)
    monkeypatch.setattr("atr.config.get_mode", lambda: config.Mode.Test)

    async with db.session() as data:
        data.add(
            sql.Committee(
                key="test",
                name="Test Committee",
                is_podling=False,
                keys_mode=sql.KeysMode.AUTOMATIC,
                committee_members=["alice"],
                committers=["alice"],
                release_managers=[],
            )
        )
        await data.commit()

    session = sql.UserSession(uid="test", fullname="Test Admin", member_committees=[], participant_committees=[])
    monkeypatch.setattr(asfquart.session, "read", mock.AsyncMock(return_value=session))
    monkeypatch.setattr(common, "authenticate", mock.AsyncMock(return_value=web.Committer(session)))

    response = await permissions_app.test_client().get("/user/permissions")
    assert response.status_code == 200
    result = await response.get_data(as_text=True)

    assert "You are an administrator." in result
    assert "Admin Override" not in result
    assert "Non-binding" not in result


def test_test_uid_sets_are_consistent():
    import atr.ldap as ldap

    assert ldap.TEST_PARTICIPANT_ONLY_UIDS <= ldap.TEST_UIDS
    assert "test-release-manager" not in ldap.TEST_UIDS
