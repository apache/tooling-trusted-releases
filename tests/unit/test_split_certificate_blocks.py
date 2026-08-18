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
import importlib.util
import pathlib

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "split_certificate_blocks.py"


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


def _certificate(fingerprint: str, armored: str) -> sql.SigningCertificate:
    return sql.SigningCertificate(
        fingerprint=fingerprint,
        latest_self_signature=None,
        primary_declared_uid="Alice <alice@example.org>",
        secondary_declared_uids=[],
        apache_uid="alice",
        ascii_armored_key=armored,
    )


def _script():
    spec = importlib.util.spec_from_file_location("split_certificate_blocks", _SCRIPT)
    assert (spec is not None) and (spec.loader is not None)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_repair_skips_a_row_changed_underneath_and_still_repairs_the_next(sqlite_data: db.Session) -> None:
    script = _script()
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )
    first = _certificate(pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT, block)
    second = _certificate(pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT, block)
    sqlite_data.add_all([first, second])
    await sqlite_data.commit()
    findings = [script._certificate_finding(first, ()), script._certificate_finding(second, ())]
    via = sql.validate_instrumented_attribute
    await sqlite_data.execute(
        sqlmodel.update(sql.SigningCertificate)
        .where(via(sql.SigningCertificate.fingerprint) == first.fingerprint)
        .values(ascii_armored_key=pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    )
    await sqlite_data.commit()

    outcomes = [await script._repair(sqlite_data, finding) for finding in findings]

    assert outcomes == [False, True]
    repaired = await sqlite_data.signing_certificate(fingerprint=second.fingerprint).get()
    assert repaired is not None
    own = pgp.certificate_for_fingerprint(repaired.ascii_armored_key, second.fingerprint)
    assert (own is not None) and (repaired.ascii_armored_key != block)
    signing_keys = await sqlite_data.execute(
        sqlmodel.select(via(sql.SigningKey.fingerprint)).where(
            via(sql.SigningKey.certificate_fingerprint) == second.fingerprint
        )
    )
    assert pgp_fixtures.REVOKED_SUBKEY_SIGNING_FINGERPRINT in set(signing_keys.scalars().all())
