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
import atr.log as log
import atr.models.sql as sql
import atr.pgp as pgp
import atr.storage.datatypes as datatypes
import atr.storage.writers.keys as keys_writer
import atr.tasks.checks as checks
import tests.unit.pgp_fixtures as pgp_fixtures

ALPHA_BLOCK = pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
ALPHA_FINGERPRINT = pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT
BETA_BLOCK = pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC
BETA_FINGERPRINT = pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT


@pytest.fixture(autouse=True)
def request_context():
    log.add_context(request_id="req-1")
    yield
    log.clear_context()


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
        (await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)).result_or_raise()
    monkeypatch.setattr(checks.db, "session", lambda: sqlite_sessionmaker())
    release = SimpleNamespace(committee=SimpleNamespace(key="tooling"))

    signing_keys = await checks._resolve_committee_signing_keys(release)

    assert signing_keys == [f"{BETA_FINGERPRINT}:{BETA_BLOCK}"]


async def test_delete_committee_keys_deletes_orphans(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        data.add(sql.Committee(key="other"))
        data.add(sql.KeyLink(committee_key="other", key_fingerprint=BETA_FINGERPRINT))
        await data.commit()
        writer = _admin_writer(data)

        num_unlinked, num_deleted, _ = await writer.delete_committee_keys(datatypes.KeySource.WEB)

        assert (num_unlinked, num_deleted) == (2, 1)
        alpha = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT, deleted=db.NOT_SET).get()
        beta = await data.signing_certificate(fingerprint=BETA_FINGERPRINT).get()
        assert alpha is not None
        assert alpha.deleted is not None
        assert beta is not None
        assert beta.deleted is None


async def test_delete_key_retains_row_and_links(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)

        oc = await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)

        oc.result_or_raise()
        assert await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT).get() is None
        key = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT, deleted=True).get()
        assert key is not None
        assert key.ascii_armored_key == ALPHA_BLOCK
        links = await data.execute(sqlmodel.select(sql.KeyLink).where(sql.KeyLink.key_fingerprint == ALPHA_FINGERPRINT))
        assert len(links.all()) == 1
        rows = await data.key_attestable(fingerprint=ALPHA_FINGERPRINT).all()
        assert [(row.seq, row.operation, row.source) for row in rows] == [
            (1, sql.KeyOperation.REVISE, "web:seed"),
            (2, sql.KeyOperation.DELETE, "web:req-1"),
        ]


async def test_delete_key_twice_errors(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)

        (await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)).result_or_raise()
        second = await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)

        assert second.error_or_none() is not None


async def test_deleted_key_hidden_from_committee_relationship(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)).result_or_raise()

        committee = (
            (
                await data.execute(
                    sqlmodel.select(sql.Committee)
                    .options(orm.selectinload(sql.validate_instrumented_attribute(sql.Committee.signing_certificates)))
                    .where(sql.validate_instrumented_attribute(sql.Committee.key) == "tooling")
                )
            )
            .scalars()
            .one()
        )

        assert [k.fingerprint for k in committee.signing_certificates] == [BETA_FINGERPRINT]


async def test_keys_file_text_excludes_deleted(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)).result_or_raise()

        text = await writer.keys_file_text("tooling")

        assert BETA_FINGERPRINT.upper() in text
        assert ALPHA_FINGERPRINT.upper() not in text


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
        key = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT).get()
        assert key is not None

        await data.delete(key)
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await data.commit()


async def test_reupload_undeletes_and_restores_associations(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        (await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)).result_or_raise()
        key = datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=_key(ALPHA_FINGERPRINT, "alice", ALPHA_BLOCK),
        )

        oc, publications = await writer._FoundationCommitter__database_add_model(key, "web:req-2")

        assert oc.result_or_raise().status == datatypes.KeyStatus.INSERTED | datatypes.KeyStatus.RESTORED
        assert set(publications) == {"tooling"}
        row = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT).get()
        assert row is not None
        assert row.deleted is None
        committee = await data.committee(key="tooling", _signing_certificates=True).get()
        assert committee is not None
        assert sorted(k.fingerprint for k in committee.signing_certificates) == sorted(
            [ALPHA_FINGERPRINT, BETA_FINGERPRINT]
        )
        rows = await data.key_attestable(fingerprint=ALPHA_FINGERPRINT).all()
        assert [(row.seq, row.operation, row.source) for row in rows] == [
            (1, sql.KeyOperation.REVISE, "web:seed"),
            (2, sql.KeyOperation.DELETE, "web:req-1"),
            (3, sql.KeyOperation.RESTORE, "web:req-2"),
        ]


async def test_reupload_with_a_changed_block_refreshes_it(sqlite_sessionmaker) -> None:
    stripped = pgp_fixtures.block_without_signature_type(ALPHA_BLOCK, 0x28)
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data, alpha_block=stripped)
        writer = _committer_writer(data)
        key = datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=_key(ALPHA_FINGERPRINT, "alice", ALPHA_BLOCK),
            input=pgp._dearmored(ALPHA_BLOCK),
        )

        oc, _publications = await writer._FoundationCommitter__database_add_model(key, "web:req-1")

        assert oc.result_or_raise().status == datatypes.KeyStatus.REFRESHED
        row = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT).get()
        assert row is not None
        assert row.ascii_armored_key == pgp.merge_certificate_blocks([ALPHA_BLOCK])
        rows = await data.key_attestable(fingerprint=ALPHA_FINGERPRINT).all()
        assert [(row.seq, row.source, row.deletions, row.input) for row in rows] == [
            (1, "web:seed", None, None),
            (2, "web:req-1", None, pgp._dearmored(ALPHA_BLOCK)),
        ]
        assert rows[1].additions is not None
        assert [tag for tag, _ in pgp._frames(rows[1].additions)] == [14, 2]
        assert pgp.fold_deltas((row.deletions, row.additions) for row in rows) == pgp.certificate_placements(
            row.ascii_armored_key
        )
        # A changed block can flip a signature check, so the key's committees must be rechecked
        writer._recheck_committee_drafts.assert_awaited()


async def test_reupload_with_a_subset_block_writes_no_observation(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        writer = _committer_writer(data)
        key = datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=_key(ALPHA_FINGERPRINT, "alice", pgp_fixtures.block_without_signature_type(ALPHA_BLOCK, 0x28)),
        )

        oc, _publications = await writer._FoundationCommitter__database_add_model(key, "web:req-1")

        assert oc.result_or_raise().status == datatypes.KeyStatus.PARSED
        row = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT).get()
        assert row is not None
        assert row.ascii_armored_key == ALPHA_BLOCK
        assert [row.seq for row in await data.key_attestable(fingerprint=ALPHA_FINGERPRINT).all()] == [1]
        writer._recheck_committee_drafts.assert_not_awaited()


async def test_write_to_a_head_without_log_is_refused(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        data.add(sql.Committee(key="tooling"))
        data.add(_key(ALPHA_FINGERPRINT, "alice", ALPHA_BLOCK))
        await data.commit()
        writer = _committer_writer(data)
        key = datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=_key(ALPHA_FINGERPRINT, "alice", ALPHA_BLOCK),
        )

        with pytest.raises(RuntimeError, match="has no log"):
            await writer._FoundationCommitter__database_add_model(key, "web:req-1")
        assert (await writer.delete_key(ALPHA_FINGERPRINT, datatypes.KeySource.WEB)).error_or_none() is not None


async def test_signature_hints_consume_flags_and_deletes(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_keys(data)
        data.add(sql.SignatureHint(hint="1234567890abcdef"))
        data.add(sql.SignatureHint(hint="feedfacefeedface"))
        await data.commit()
        writer = _committer_writer(data)
        key_model = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT).get()
        assert key_model is not None
        key = datatypes.Key(
            status=datatypes.KeyStatus.PARSED,
            key_model=key_model,
            member_ids=[ALPHA_FINGERPRINT, "1234567890abcdef"],
        )

        flagged = await writer._FoundationCommitter__signature_hints_consume([key])
        await data.commit()

        assert flagged == [ALPHA_FINGERPRINT]
        refreshed = await data.signing_certificate(fingerprint=ALPHA_FINGERPRINT).get()
        assert refreshed is not None
        assert refreshed.historic_use is True
        assert await data.signature_hint(hint="1234567890abcdef").get() is None
        assert await data.signature_hint(hint="feedfacefeedface").get() is not None


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


def _signing_key(fingerprint: str) -> sql.SigningKey:
    return sql.SigningKey(
        fingerprint=fingerprint,
        certificate_fingerprint=fingerprint,
        is_primary=True,
        key_id=fingerprint[-16:],
        algorithm=1,
        length=4096,
        created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
    )


def _key(fingerprint: str, apache_uid: str, armored: str) -> sql.SigningCertificate:
    return sql.SigningCertificate(
        fingerprint=fingerprint,
        algorithm=1,
        length=4096,
        created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        primary_declared_uid=f"{apache_uid} <{apache_uid}@apache.org>",
        apache_uid=apache_uid,
        ascii_armored_key=armored,
    )


def _genesis_row(fingerprint: str, armored: str, actor: str) -> sql.KeyAttestable:
    _, additions = pgp.delta_fragments(frozenset(), pgp.certificate_placements(armored))
    return sql.KeyAttestable(
        fingerprint=fingerprint,
        seq=1,
        operation=sql.KeyOperation.REVISE,
        source="web:seed",
        additions=additions,
        updated=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        actor=actor,
        role=sql.KeyRole.USER,
    )


async def _seed_keys(data: db.Session, alpha_block: str = ALPHA_BLOCK) -> None:
    # ATR-managed committee, so the reflect-mode read-only guard doesn't block these delete tests
    data.add(sql.Committee(key="tooling", keys_mode=sql.KeysMode.AUTOMATIC))
    data.add(_key(ALPHA_FINGERPRINT, "alice", alpha_block))
    data.add(_key(BETA_FINGERPRINT, "bob", BETA_BLOCK))
    data.add(_genesis_row(ALPHA_FINGERPRINT, alpha_block, "alice"))
    data.add(_genesis_row(BETA_FINGERPRINT, BETA_BLOCK, "bob"))
    await data.commit()
    # An artifact is attributed to a signing key rather than to the certificate carrying it
    data.add(_signing_key(ALPHA_FINGERPRINT))
    data.add(_signing_key(BETA_FINGERPRINT))
    await data.commit()
    data.add(sql.KeyLink(committee_key="tooling", key_fingerprint=ALPHA_FINGERPRINT))
    data.add(sql.KeyLink(committee_key="tooling", key_fingerprint=BETA_FINGERPRINT))
    await data.commit()
