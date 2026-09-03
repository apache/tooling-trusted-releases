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

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql

_CHECKER = "atr.tasks.checks.license.headers"
_PATH = "apache-test-1.0-source.tar.gz"


async def test_checks_tally_counts_every_status_and_loads_only_the_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessions = sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    load_checks = mock.AsyncMock(return_value={_PATH: {_CHECKER: "h1"}, "": {"atr.tasks.checks.paths.check": "h2"}})
    monkeypatch.setattr(interaction.attestable, "load_checks", load_checks)
    release = sql.Release(phase=sql.ReleasePhase.RELEASE_CANDIDATE, version="1.0", project_key="test")
    async with sessions() as data:
        data.add_all(
            [
                _result(sql.CheckResultStatus.NOTE, "h1", member_rel_path="a/one.py"),
                _result(sql.CheckResultStatus.NOTE, "h1", member_rel_path="a/two.py"),
                _result(sql.CheckResultStatus.CONCERN, "h1", member_rel_path="a/three.py"),
                _result(sql.CheckResultStatus.NOTE, "h1"),
                _result(
                    sql.CheckResultStatus.CONCERN, "h2", primary_rel_path=None, checker="atr.tasks.checks.paths.check"
                ),
                _result(sql.CheckResultStatus.NOTE, "stale"),
            ]
        )
        await data.commit()
        tally = await interaction.checks_tally_for(
            release, safe.RevisionNumber("00001"), statuses=sql.CHECK_RESULT_IGNORABLE_STATUSES, caller_data=data
        )
    await engine.dispose()

    expected = [
        interaction.CheckCount(_PATH, _CHECKER, sql.CheckResultStatus.NOTE, True, 2),
        interaction.CheckCount(_PATH, _CHECKER, sql.CheckResultStatus.CONCERN, True, 1),
        interaction.CheckCount(_PATH, _CHECKER, sql.CheckResultStatus.NOTE, False, 1),
        interaction.CheckCount(None, "atr.tasks.checks.paths.check", sql.CheckResultStatus.CONCERN, False, 1),
    ]
    assert sorted(tally.counts, key=_key) == sorted(expected, key=_key)
    assert [(r.status, r.member_rel_path) for r in tally.results] == [
        (sql.CheckResultStatus.CONCERN, "a/three.py"),
        (sql.CheckResultStatus.CONCERN, None),
    ]


def _key(count: interaction.CheckCount) -> tuple[str, str, str, bool]:
    return (count.primary_rel_path or "", count.checker, count.status.value, count.member)


def _result(
    status: sql.CheckResultStatus,
    inputs_hash: str,
    *,
    primary_rel_path: str | None = _PATH,
    member_rel_path: str | None = None,
    checker: str = _CHECKER,
) -> sql.CheckResult:
    return sql.CheckResult(
        release_key="test-1.0",
        revision_number="00001",
        checker=checker,
        checker_version="1",
        primary_rel_path=primary_rel_path,
        member_rel_path=member_rel_path,
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        status=status,
        message="m",
        data={},
        inputs_hash=inputs_hash,
    )
