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
import json
import pathlib

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.sql as sql

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "keys_rehome.py"


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


def _certificate(fingerprint: str, deleted: datetime.datetime | None = None) -> sql.SigningCertificate:
    return sql.SigningCertificate(
        fingerprint=fingerprint,
        latest_self_signature=None,
        primary_declared_uid="Alice <alice@apache.org>",
        secondary_declared_uids=[],
        apache_uid="alice",
        ascii_armored_key="",
        deleted=deleted,
    )


def _script():
    spec = importlib.util.spec_from_file_location("keys_rehome", _SCRIPT)
    assert (spec is not None) and (spec.loader is not None)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _seed(data: db.Session) -> None:
    for key, automated in (("incubator", False), ("pulsar", False), ("lucene", True), ("hadoop", False)):
        mode = sql.KeysMode.AUTOMATIC if automated else sql.KeysMode.MANUAL
        data.add(sql.Committee(key=key, keys_mode=mode))
    for fingerprint in ("fp1", "fp2", "fp4", "fp5", "fp6", "fp7"):
        data.add(_certificate(fingerprint))
    data.add(_certificate("fp3", deleted=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)))
    await data.commit()
    links = (
        ("incubator", "fp1"),
        ("lucene", "fp2"),
        ("incubator", "fp3"),
        ("incubator", "fp5"),
        ("incubator", "fp6"),
        ("hadoop", "fp7"),
        ("lucene", "fp7"),
    )
    for committee, fingerprint in links:
        data.add(sql.KeyLink(committee_key=committee, key_fingerprint=fingerprint))
    await data.commit()


def test_load_decisions_rejects_malformed_records(tmp_path):
    script = _script()
    path = tmp_path / "decisions.jsonl"
    for record in (
        {"committee": "a", "fingerprint": "FP", "action": "rehmoe", "targets": ["b"]},
        {"committee": "a", "fingerprint": "FP", "action": "keep", "targets": ["b"]},
        {"committee": "a", "fingerprint": "FP", "action": "rehome", "targets": ["a"]},
    ):
        path.write_text(json.dumps(record) + "\n")
        with pytest.raises(ValueError):
            script._load_decisions(path)
    path.write_text(
        '{"committee": "a", "fingerprint": "FP", "action": "drop", "targets": []}\n'
        '{"committee": "a", "fingerprint": "fp", "action": "drop", "targets": []}\n'
    )
    with pytest.raises(ValueError, match="duplicate record"):
        script._load_decisions(path)
    path.write_text(
        '{"committee": "a", "fingerprint": "fp", "action": "rehome", "targets": ["b"]}\n'
        '{"committee": "b", "fingerprint": "fp", "action": "rehome", "targets": ["c"]}\n'
    )
    with pytest.raises(ValueError, match="both removed and targeted"):
        script._load_decisions(path)


@pytest.mark.asyncio
async def test_plan_and_verify(sqlite_data):
    await _seed(sqlite_data)
    script = _script()
    decisions = [
        script.Decision("incubator", "fp1", "rehome", ("pulsar",)),
        script.Decision("lucene", "fp2", "rehome", ("hadoop",)),
        script.Decision("incubator", "fp6", "drop", ()),
        script.Decision("lucene", "fp7", "keep+add", ("hadoop", "pulsar")),
        script.Decision("hadoop", "fp7", "keep", ()),
        script.Decision("incubator", "fp3", "drop", ()),
        script.Decision("incubator", "fp4", "drop", ()),
        script.Decision("incubator", "fp5", "rehome", ("nowhere",)),
    ]
    plan = script._plan(decisions, await script._state(sqlite_data))

    assert plan.adds == {"pulsar": ["fp1", "fp7"], "hadoop": ["fp2"]}
    assert plan.removals == {"incubator": ["fp1", "fp6"], "lucene": ["fp2"]}
    assert plan.planned == decisions[:5]
    assert plan.skipped == [
        "incubator fp3: certificate missing or deleted",
        "incubator fp4: source link already gone",
        "incubator fp5: target committee missing: nowhere",
    ]
    assert plan.publishing == ["lucene"]

    sqlite_data.add(sql.KeyLink(committee_key="pulsar", key_fingerprint="fp1"))
    sqlite_data.add(sql.KeyLink(committee_key="pulsar", key_fingerprint="fp7"))
    await sqlite_data.commit()
    linked = (await script._state(sqlite_data)).links

    assert script._removals(plan.planned, linked) == {"incubator": ["fp1", "fp6"]}

    await sqlite_data.execute(sqlmodel.delete(sql.KeyLink).where(sql.KeyLink.committee_key == "incubator"))
    await sqlite_data.commit()
    mismatches = script._verify(plan.planned, await script._state(sqlite_data))

    assert mismatches == [
        "lucene fp2: source link should be absent",
        "lucene fp2: not linked to hadoop",
    ]
