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

import collections
import datetime
import pathlib
import types
import unittest.mock as mock

import sqlalchemy.ext.asyncio
import sqlalchemy.pool as pool
import sqlmodel

import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.readers.checks as checks

_CHECKS = {"": {}, "a.tgz": {"headers": "blake3:h", "rat": "blake3:r"}}
_NOW = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)


async def test_by_release_path_pages_member_issues_and_counts_notes() -> None:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessions = sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    release = types.SimpleNamespace(
        latest_revision_number="00001",
        safe_latest_revision_number=safe.RevisionNumber("00001"),
        safe_project_key=safe.ProjectKey("proj"),
        safe_version_key=safe.VersionKey("1.0"),
    )
    async with sessions() as data:
        data.add_all(
            [
                _result("headers", None, sql.CheckResultStatus.NOTE, "blake3:h"),
                _result("headers", "src/b.py", sql.CheckResultStatus.NOTE, "blake3:h"),
                _result("headers", "src/a.py", sql.CheckResultStatus.NOTE, "blake3:h"),
                _result("headers", "src/c.py", sql.CheckResultStatus.CONCERN, "blake3:h"),
                _result("rat", "src/b.py", sql.CheckResultStatus.SUGGESTION, "blake3:r"),
                _result("rat", "src/a.py", sql.CheckResultStatus.CONCERN, "blake3:r"),
            ]
        )
        await data.commit()
        read = types.SimpleNamespace(authorisation=types.SimpleNamespace(asf_uid=None))
        reader = checks.GeneralPublic(read, None, data)
        with mock.patch.object(interaction.attestable, "load_checks", new=mock.AsyncMock(return_value=_CHECKS)):
            first = await reader.by_release_path(release, pathlib.Path("a.tgz"), 0, 2)
            second = await reader.by_release_path(release, pathlib.Path("a.tgz"), 2, 2)
    await engine.dispose()

    assert [result.status for result in first.primary_results_list] == [sql.CheckResultStatus.NOTE]
    assert _page(first) == {"src/a.py": ["rat"], "src/b.py": ["rat"]}
    assert _page(second) == {"src/c.py": ["headers"]}
    assert first.member_count == 3
    assert first.member_status_counts == collections.Counter(
        {sql.CheckResultStatus.CONCERN: 2, sql.CheckResultStatus.SUGGESTION: 1}
    )
    assert first.member_note_count == 2


def _page(results: object) -> dict[str, list[str]]:
    return {path: [result.checker for result in rows] for path, rows in results.member_results_list.items()}


def _result(
    checker: str, member_rel_path: str | None, status: sql.CheckResultStatus, inputs_hash: str
) -> sql.CheckResult:
    return sql.CheckResult(
        release_key="proj-1.0",
        revision_number="00001",
        checker=checker,
        primary_rel_path="a.tgz",
        member_rel_path=member_rel_path,
        created=_NOW,
        status=status,
        message="",
        data={},
        inputs_hash=inputs_hash,
    )
