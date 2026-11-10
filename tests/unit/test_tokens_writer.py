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
from collections.abc import AsyncIterator
from typing import Final

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.github as github
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.tokens as tokens

# Far enough ahead that a project JWT row is still live
_LATER: Final[int] = 2**31 - 1


@pytest.fixture
async def sqlite_sessionmaker() -> AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    yield sessionmaker
    await engine.dispose()


async def test_consume_workflow_jti_commits_and_then_refuses_the_same_token(
    sqlite_sessionmaker, github_payload: github.TrustedPublisherPayload
) -> None:
    async with sqlite_sessionmaker() as data:
        writer = object.__new__(tokens.CommitteeParticipant)
        writer._CommitteeParticipant__data = data
        await writer.consume_workflow_jti(github_payload, 0)
        # The caller goes on to fail, and never commits anything itself
        await data.rollback()

    async with sqlite_sessionmaker() as data:
        assert [row.jti for row in await data.query_all(sqlmodel.select(sql.WorkflowJti))] == ["test-jti"]
        writer = object.__new__(tokens.CommitteeParticipant)
        writer._CommitteeParticipant__data = data
        with pytest.raises(storage.AccessError, match="already been used") as exc_info:
            await writer.consume_workflow_jti(github_payload, 0)
        assert exc_info.value.status == 409


async def test_revoke_all_users_tokens_keeps_system_tokens(sqlite_sessionmaker) -> None:
    expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
    async with sqlite_sessionmaker() as data:
        data.add(sql.PersonalAccessToken(asfuid="alice", created_by="alice", token_hash="a", expires=expires))
        data.add(sql.PersonalAccessToken(asfuid="bob", created_by="bob", token_hash="b", expires=expires))
        data.add(sql.PersonalAccessToken(created_by="admin", token_hash="s", expires=expires, is_system=True))
        await data.commit()

        write_as = mock.MagicMock()
        writer = object.__new__(tokens.FoundationAdmin)
        writer._FoundationAdmin__write_as = write_as
        writer._FoundationAdmin__data = data
        writer._FoundationAdmin__asf_uid = "admin"

        data.add(sql.PersonalAccessToken(asfuid="alice", created_by="alice", token_hash="a2", expires=expires))
        data.add(sql.PersonalAccessToken(asfuid="admin", created_by="admin", token_hash="c", expires=expires))
        await data.commit()
        revocation = await writer.revoke_all_users_tokens()

        assert revocation is not None
        assert revocation.count == 4
        assert revocation.owners == ["admin", "alice", "bob"]
        remaining = (await data.execute(sqlmodel.select(sql.PersonalAccessToken))).scalars().all()
        assert [token.token_hash for token in remaining] == ["s"]
        write_as.append_to_audit_log.assert_called_once_with(tokens_revoked=4, revocation_id=revocation.id)
        assert await writer.pat_revocation(revocation.id) == revocation
        assert await writer.revoke_all_users_tokens() is None

        data.add(sql.PersonalAccessToken(asfuid="carol", created_by="carol", token_hash="d", expires=expires))
        await data.commit()
        later = await writer.revoke_all_users_tokens()
        assert later is not None
        assert later.id != revocation.id
        assert later.owners == ["carol"]

        assert await writer.notify_revoked_pat_owners(revocation.id, "Notice", "Replace your PATs.") == 3
        assert await writer.notify_revoked_pat_owners(revocation.id, "Again", "Again") is None
        assert await writer.pat_revocation(revocation.id) is None
        assert await writer.pat_revocation(later.id) == later
        tasks = await data.task(task_type=sql.TaskType.MESSAGE_SEND).all()
        assert len(tasks) == 1
        assert tasks[0].asf_uid == "admin"
        assert tasks[0].task_args["email_sender"] == "admin@apache.org"
        assert tasks[0].task_args["email_to"] == "admin@apache.org"
        assert tasks[0].task_args["email_bcc"] == ["alice@apache.org", "bob@apache.org"]
        assert tasks[0].task_args["email_cc"] == []
        assert tasks[0].task_args["pat_revocation_notice"] is True


@pytest.mark.parametrize("failure", ["wrong_admin", "commit"])
async def test_revocation_notice_failed_queue_preserves_audience(sqlite_sessionmaker, monkeypatch, failure) -> None:
    async with sqlite_sessionmaker() as data:
        data.add(
            sql.PersonalAccessToken(
                asfuid="admin", created_by="admin", token_hash="a", expires=datetime.datetime.now(datetime.UTC)
            )
        )
        await data.commit()
        writer = object.__new__(tokens.FoundationAdmin)
        writer._FoundationAdmin__write_as = mock.Mock()
        writer._FoundationAdmin__data = data
        writer._FoundationAdmin__asf_uid = "admin"
        revocation = await writer.revoke_all_users_tokens()
        assert revocation is not None
        revocation_id = revocation.id
        with monkeypatch.context() as patch:
            if failure == "wrong_admin":
                patch.setattr(writer, "_FoundationAdmin__asf_uid", "otheradmin")
                with pytest.raises(storage.AccessError):
                    await writer.pat_revocation(revocation_id)
                error = storage.AccessError
            else:
                patch.setattr(data, "commit", mock.AsyncMock(side_effect=RuntimeError("commit failed")))
                error = RuntimeError
            with pytest.raises(error):
                await writer.notify_revoked_pat_owners(revocation_id, "Notice", "Replace your PATs.")
            await data.rollback()
        assert await writer.pat_revocation(revocation_id) is not None
        assert await data.task(task_type=sql.TaskType.MESSAGE_SEND).all() == []
        assert await writer.notify_revoked_pat_owners(revocation_id, "Notice", "Replace your PATs.") == 1
        task = await data.task(task_type=sql.TaskType.MESSAGE_SEND).get()
        assert task is not None
        assert task.task_args["email_to"] == "admin@apache.org"
        assert task.task_args["email_bcc"] == []
        assert "pat_revocation_notice" not in task.task_args


async def test_revoke_all_project_jwts_revokes_and_records_the_live_ones(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        for jti, expires in [("a", _LATER), ("b", _LATER), ("c", 0)]:
            data.add(
                sql.WorkflowJwt(
                    jti=jti, project_key="example", asf_uid="alice", github_uid="alice", github_nid=1, expires=expires
                )
            )
        await data.commit()
        write_as = mock.MagicMock()
        writer = object.__new__(tokens.FoundationAdmin)
        writer._FoundationAdmin__write_as = write_as
        writer._FoundationAdmin__data = data
        writer._FoundationAdmin__asf_uid = "admin"

        assert await writer.revoke_all_project_jwts() == 2

        rows = {row.jti: row.revoked for row in await data.workflow_jwt().all()}
        assert rows == {"a": True, "b": True, "c": False}
        write_as.append_to_audit_log.assert_called_once_with(project_jwts_revoked=2)
        assert await writer.revoke_all_project_jwts() == 0
        write_as.append_to_audit_log.assert_called_once()


async def test_revoke_all_user_tokens_revokes_project_jwts(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        # Row c has expired
        for jti, asf_uid, expires in [("a", "alice", _LATER), ("b", "bob", _LATER), ("c", "alice", 0)]:
            data.add(
                sql.WorkflowJwt(
                    jti=jti, project_key="example", asf_uid=asf_uid, github_uid=asf_uid, github_nid=1, expires=expires
                )
            )
        await data.commit()
        write_as = mock.MagicMock()
        write_as.mail.send = mock.AsyncMock()
        writer = object.__new__(tokens.FoundationAdmin)
        writer._FoundationAdmin__write_as = write_as
        writer._FoundationAdmin__data = data
        writer._FoundationAdmin__asf_uid = "admin"

        assert await writer.revoke_all_user_tokens("alice") == 1

        rows = {row.jti: row.revoked for row in await data.workflow_jwt().all()}
        assert rows == {"a": True, "b": False, "c": False}
        write_as.append_to_audit_log.assert_called_once_with(
            target_asf_uid="alice", tokens_revoked=0, project_jwts_revoked=1
        )
        assert await writer.revoke_all_user_tokens("alice") == 0
