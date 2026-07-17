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
from types import SimpleNamespace

import pytest
import sqlalchemy
import sqlalchemy.exc
import sqlalchemy.ext.asyncio
import sqlalchemy.orm as orm
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.storage.datatypes as datatypes
import atr.storage.writers.keys as keys_writer
import atr.tasks.checks as checks

ALPHA_FINGERPRINT = "a" * 40
BETA_FINGERPRINT = "b" * 40


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


async def test_committee_signing_keys_exclude_deleted(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT)).result_or_raise()
    monkeypatch.setattr(checks.db, "session", lambda: sqlite_sessionmaker())
    release = SimpleNamespace(committee=SimpleNamespace(key="tooling"))

    fingerprints = await checks._resolve_committee_signing_keys(release)

    assert fingerprints == [BETA_FINGERPRINT]


async def test_delete_committee_keys_deletes_orphans(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        data.add(sql.Committee(key="other"))
        data.add(sql.KeyLink(committee_key="other", key_fingerprint=BETA_FINGERPRINT))
        await data.commit()
        writer = _admin_writer(data)

        num_unlinked, num_deleted, _ = await writer.delete_committee_keys()

        assert (num_unlinked, num_deleted) == (2, 1)
        alpha = await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT, deleted=db.NOT_SET).get()
        beta = await data.public_signing_key(fingerprint=BETA_FINGERPRINT).get()
        assert alpha is not None
        assert alpha.deleted is not None
        assert beta is not None
        assert beta.deleted is None


async def test_delete_key_retains_row_and_links(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)

        oc = await writer.delete_key(ALPHA_FINGERPRINT)

        oc.result_or_raise()
        assert await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT).get() is None
        key = await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT, deleted=True).get()
        assert key is not None
        assert key.ascii_armored_key == "armored alpha"
        links = await data.execute(sqlmodel.select(sql.KeyLink).where(sql.KeyLink.key_fingerprint == ALPHA_FINGERPRINT))
        assert len(links.all()) == 1


async def test_delete_key_twice_errors(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)

        (await writer.delete_key(ALPHA_FINGERPRINT)).result_or_raise()
        second = await writer.delete_key(ALPHA_FINGERPRINT)

        assert second.error_or_none() is not None


async def test_deleted_key_hidden_from_committee_relationship(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT)).result_or_raise()

        committee = (
            (
                await data.execute(
                    sqlmodel.select(sql.Committee)
                    .options(orm.selectinload(sql.validate_instrumented_attribute(sql.Committee.public_signing_keys)))
                    .where(sql.validate_instrumented_attribute(sql.Committee.key) == "tooling")
                )
            )
            .scalars()
            .one()
        )

        assert [k.fingerprint for k in committee.public_signing_keys] == [BETA_FINGERPRINT]


async def test_keys_file_text_excludes_deleted(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT)).result_or_raise()

        text = await writer.keys_file_text("tooling")

        assert "armored beta" in text
        assert "armored alpha" not in text


async def test_restrict_prevents_hard_delete_of_referenced_key(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await data.execute(sqlalchemy.text("PRAGMA foreign_keys=ON"))
        await _seed_keys(data)
        data.add(sql.Project(key="tooling", committee_key="tooling"))
        await data.commit()
        data.add(
            sql.Artifact(
                project_key="tooling",
                version="1.0.0",
                artifact_path="tooling-1.0.0.tar.gz",
                key_fingerprint=ALPHA_FINGERPRINT,
            )
        )
        await data.commit()
        key = await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT).get()
        assert key is not None

        await data.delete(key)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await data.commit()


async def test_reupload_undeletes_and_restores_associations(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT)).result_or_raise()
        key = datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=_key(ALPHA_FINGERPRINT, "alice", "armored alpha"),
        )

        oc, publications = await writer._FoundationCommitter__database_add_model(key)

        assert oc.result_or_raise().status == datatypes.KeyStatus.INSERTED
        assert set(publications) == {"tooling"}
        row = await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT).get()
        assert row is not None
        assert row.deleted is None
        committee = await data.committee(key="tooling", _public_signing_keys=True).get()
        assert committee is not None
        assert sorted(k.fingerprint for k in committee.public_signing_keys) == [ALPHA_FINGERPRINT, BETA_FINGERPRINT]


async def test_signature_hints_consume_flags_and_deletes(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        data.add(sql.SignatureHint(hint="1234567890abcdef"))
        data.add(sql.SignatureHint(hint="feedfacefeedface"))
        await data.commit()
        writer = _committer_writer(data)
        key_model = await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT).get()
        assert key_model is not None
        key = datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=key_model,
            member_ids=[ALPHA_FINGERPRINT, "1234567890abcdef"],
        )

        flagged = await writer._FoundationCommitter__signature_hints_consume([key])
        await data.commit()

        assert flagged == [ALPHA_FINGERPRINT]
        refreshed = await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT).get()
        assert refreshed is not None
        assert refreshed.historic_use is True
        assert await data.signature_hint(hint="1234567890abcdef").get() is None
        assert await data.signature_hint(hint="feedfacefeedface").get() is not None


async def test_undelete_keys_clears_deleted(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT)).result_or_raise()

        undeleted = await writer._FoundationCommitter__undelete_keys([ALPHA_FINGERPRINT, BETA_FINGERPRINT])
        await data.commit()

        assert undeleted == [ALPHA_FINGERPRINT]
        key = await data.public_signing_key(fingerprint=ALPHA_FINGERPRINT).get()
        assert key is not None
        assert key.deleted is None


def _admin_writer(data: db.Session) -> keys_writer.FoundationAdmin:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    writer = keys_writer.FoundationAdmin(write, mock.MagicMock(), data, "tooling")
    writer._sync_committee_keys_file = mock.AsyncMock(return_value=(None, None))
    writer._recheck_committee_drafts = mock.AsyncMock()
    return writer


def _committer_writer(data: db.Session) -> keys_writer.FoundationCommitter:
    write = mock.MagicMock()
    write.authorisation.asf_uid = "alice"
    writer = keys_writer.FoundationCommitter(write, mock.MagicMock(), data)
    writer._sync_committee_keys_file = mock.AsyncMock(return_value=(None, None))
    writer._recheck_committee_drafts = mock.AsyncMock()
    return writer


def _key(fingerprint: str, apache_uid: str, armored: str) -> sql.PublicSigningKey:
    return sql.PublicSigningKey(
        fingerprint=fingerprint,
        algorithm=1,
        length=4096,
        created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        primary_declared_uid=f"{apache_uid} <{apache_uid}@apache.org>",
        apache_uid=apache_uid,
        ascii_armored_key=armored,
    )


async def _seed_keys(data: db.Session) -> None:
    data.add(sql.Committee(key="tooling"))
    data.add(_key(ALPHA_FINGERPRINT, "alice", "armored alpha"))
    data.add(_key(BETA_FINGERPRINT, "bob", "armored beta"))
    await data.commit()
    data.add(sql.KeyLink(committee_key="tooling", key_fingerprint=ALPHA_FINGERPRINT))
    data.add(sql.KeyLink(committee_key="tooling", key_fingerprint=BETA_FINGERPRINT))
    await data.commit()
