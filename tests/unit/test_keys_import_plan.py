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

import collections.abc
import datetime
import importlib.util
import pathlib
import types

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "keys_import.py"
_STALE = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)


@pytest.fixture
async def sqlite_data() -> collections.abc.AsyncIterator[db.Session]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with sessionmaker() as data:
        yield data
    await engine.dispose()


def _args(allow_refresh: bool = False, allow_undelete: bool = False) -> types.SimpleNamespace:
    return types.SimpleNamespace(apply=False, committee=[], allow_refresh=allow_refresh, allow_undelete=allow_undelete)


def _certificate(armored: str, deleted: datetime.datetime | None = None) -> sql.SigningCertificate:
    key, _ = pgp.openpgp.composed.SignedPublicKey.from_armor(armored)
    return sql.SigningCertificate(
        fingerprint=key.fingerprint.lower(),
        latest_self_signature=pgp.latest_self_signature_created_at(key),
        primary_declared_uid=next(iter(key.user_ids)),
        secondary_declared_uids=[],
        apache_uid="alice",
        ascii_armored_key=armored,
        deleted=deleted,
    )


def _script():
    spec = importlib.util.spec_from_file_location("keys_import", _SCRIPT)
    assert (spec is not None) and (spec.loader is not None)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _seed(data: db.Session) -> tuple[str, dict[str, set[str]]]:
    linked = _certificate(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC)
    deleted = _certificate(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC, deleted=_STALE)
    stale = _certificate(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)
    stale.latest_self_signature = _STALE
    current = _certificate(pgp_fixtures.REVOKED_PRIMARY_UID_PUBLIC_KEY_ASC)
    data.add_all([linked, deleted, stale, current])
    await data.commit()
    canonical = "\n\n".join(
        [
            pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC,
            pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC,
            pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC,
            pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC,
            pgp_fixtures.REVOKED_PRIMARY_UID_PUBLIC_KEY_ASC,
        ]
    )
    links = {
        linked.fingerprint: {"beta"},
        deleted.fingerprint: {"alpha", "beta"},
        stale.fingerprint: {"alpha"},
        current.fingerprint: {"alpha"},
    }
    return canonical, links


def test_canonical_certificates_splits_a_two_key_block() -> None:
    script = _script()
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )

    canonical = script._canonical_certificates(block + "\n\n" + pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)

    assert set(canonical) == {
        pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT,
        pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT,
        pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT,
    }
    assert canonical[pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT] != [block]
    assert (
        canonical[pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT][0].strip()
        == pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC.strip()
    )


@pytest.mark.asyncio
async def test_plan_committee_refreshes_against_the_copy_the_importer_would_keep(sqlite_data: db.Session) -> None:
    script = _script()
    full = pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    stripped = pgp_fixtures.block_without_signature_type(full, 0x28)
    stored = _certificate(stripped)
    sqlite_data.add(stored)
    await sqlite_data.commit()
    canonical = "\n\n".join([stripped, full])
    links = {stored.fingerprint: {"alpha"}}

    selections = await script.plan_committee(sqlite_data, "alpha", canonical, links, _args(allow_refresh=True))

    assert [s.action for s in selections] == ["refresh"]
    assert "key facts changed" in selections[0].note
    assert selections[0].actionable is True
    assert selections[0].armored.count("BEGIN PGP PUBLIC KEY BLOCK") == 2


@pytest.mark.asyncio
async def test_plan_committee_refreshes_only_rows_which_differ(sqlite_data: db.Session) -> None:
    script = _script()
    canonical, links = await _seed(sqlite_data)

    selections = await script.plan_committee(sqlite_data, "alpha", canonical, links, _args(allow_refresh=True))

    refreshed = [s for s in selections if s.action == "refresh"]
    assert [s.fingerprint for s in refreshed] == [pgp_fixtures.REVOKED_UID_FINGERPRINT]
    assert "self-signature date differs" in refreshed[0].note
    assert [s.action for s in selections].count("undelete") == 0


@pytest.mark.asyncio
async def test_plan_committee_selects_only_missing_rows_and_links_by_default(sqlite_data: db.Session) -> None:
    script = _script()
    canonical, links = await _seed(sqlite_data)

    selections = await script.plan_committee(sqlite_data, "alpha", canonical, links, _args())

    assert [(s.action, s.fingerprint) for s in selections] == [
        ("import", pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT),
        ("link", pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT),
    ]
    assert selections[1].armored == pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC


@pytest.mark.asyncio
async def test_plan_committee_surfaces_incomparable_canonical_copies(sqlite_data: db.Session) -> None:
    script = _script()
    full = pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    unbound = pgp_fixtures.block_without_signature_type(full, 0x18)
    stored = _certificate(full)
    sqlite_data.add(stored)
    await sqlite_data.commit()
    canonical = "\n\n".join([full, unbound])
    links = {stored.fingerprint: {"alpha"}}

    selections = await script.plan_committee(sqlite_data, "alpha", canonical, links, _args(allow_refresh=True))

    assert [s.action for s in selections] == ["refresh"]
    assert "neither supersedes" in selections[0].note
    assert selections[0].actionable is False


@pytest.mark.asyncio
async def test_plan_committee_undeletes_with_stored_text_and_names_other_committees(sqlite_data: db.Session) -> None:
    script = _script()
    canonical, links = await _seed(sqlite_data)

    selections = await script.plan_committee(sqlite_data, "alpha", canonical, links, _args(allow_undelete=True))

    undelete = next(s for s in selections if s.action == "undelete")
    assert undelete.fingerprint == pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT
    assert undelete.armored == pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC
    assert undelete.republishes == ("beta",)
    assert [s.action for s in selections].count("refresh") == 0
