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

import collections.abc as abc
import datetime
import pathlib
import shutil
import subprocess
import types
import unittest.mock as mock

import pytest
import rfc8785
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.config as config
import atr.constants as constants
import atr.daylog as daylog
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.sealing as sealing
import atr.tasks as tasks
import atr.tasks.audit as audit
import atr.tasks.task as task

REQUIRES_SLH = pytest.mark.skipif(
    (shutil.which("openssl") is None)
    or (
        sealing.ALGORITHM.encode()
        not in subprocess.run(["openssl", "list", "-signature-algorithms"], capture_output=True, check=False).stdout
    ),
    reason="OpenSSL with SLH-DSA-SHAKE-256s is required",
)
THROUGH = datetime.date(2020, 1, 3)


@pytest.fixture
def directories(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    daily, mount = tmp_path / "daily", tmp_path / "sealing"
    daily.mkdir()
    mount.mkdir(mode=0o700)
    record = {"event": {}, "level": "info", "logger": "atr.auth", "timestamp": "2020-01-01T00:00:00.000000Z"}
    (daily / "2020-01-01.jsonl").write_bytes(rfc8785.dumps(record) + b"\n")
    (daily / "2020-01-04.jsonl").write_bytes(b"")
    monkeypatch.setattr(paths, "get_audit_log_dir", mock.Mock(return_value=safe.StatePath(daily)))
    monkeypatch.setattr(paths, "get_sealing_dir", mock.Mock(return_value=mount))
    monkeypatch.setattr(config, "is_production_mode", mock.Mock(return_value=True))
    clock = mock.Mock(wraps=datetime.datetime)
    clock.now.return_value = datetime.datetime(2020, 1, 4, 12, tzinfo=datetime.UTC)
    monkeypatch.setattr(audit, "datetime", types.SimpleNamespace(**(vars(datetime) | {"datetime": clock})))
    return daily, mount


@pytest.fixture
async def sqlite_sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> abc.AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    monkeypatch.setattr(db, "session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


def test_missing_mount(directories, monkeypatch: pytest.MonkeyPatch) -> None:
    daily, mount = directories
    mount.rmdir()
    monkeypatch.setattr(config, "is_production_mode", mock.Mock(return_value=False))
    assert audit._seal_day(THROUGH) is False
    assert (daily / "closed").read_bytes() == b"2020-01-03\n"
    monkeypatch.setattr(config, "is_production_mode", mock.Mock(return_value=True))
    with pytest.raises(FileNotFoundError):
        audit._seal_day(THROUGH)
    assert not mount.exists()
    assert not (daily / "root.der").exists()


@pytest.mark.parametrize("outcome", ["done", "more", "error"])
@pytest.mark.parametrize(
    ("instant", "closed_day", "next_day"),
    [("00:00:00", 2, 4), ("00:04:59.999999", 2, 4), ("00:05:00", 3, 5), ("12:00:00", 3, 5)],
)
async def test_scheduling(
    directories,
    sqlite_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    instant: str,
    closed_day: int,
    next_day: int,
) -> None:
    now = datetime.datetime.fromisoformat(f"2020-01-04T{instant}Z")
    monkeypatch.setattr(audit.datetime.datetime, "now", mock.Mock(return_value=now))
    await tasks.audit_seal(constants.SYSTEM_SERVICE_UID)
    await tasks.audit_seal(constants.SYSTEM_SERVICE_UID)
    async with sqlite_sessionmaker() as data:
        queued = (await data.execute(sqlmodel.select(sql.Task))).scalar_one()
        assert queued.scheduled is None
    work = mock.Mock(return_value=outcome == "more")
    if outcome == "error":
        work.side_effect = ValueError("broken chain")
    monkeypatch.setattr(audit, "_seal_day", work)
    handler = tasks.resolve(sql.TaskType.AUDIT_SEAL)
    arguments = {"asf_uid": constants.SYSTEM_SERVICE_UID}
    if outcome == "more":
        with pytest.raises(task.DeferredError) as error:
            await handler(arguments)
        assert error.value.seconds == 0
    elif outcome == "error":
        with pytest.raises(ValueError, match="broken chain"):
            await handler(arguments)
    else:
        assert await handler(arguments) is None
    work.assert_called_once_with(datetime.date(2020, 1, closed_day))
    async with sqlite_sessionmaker() as data:
        queued = (await data.execute(sqlmodel.select(sql.Task))).scalar_one()
        assert queued.task_type == sql.TaskType.AUDIT_SEAL
        assert queued.task_args == arguments
        assert queued.scheduled == datetime.datetime(2020, 1, next_day, 0, 5, tzinfo=datetime.UTC)
    await tasks.clear_scheduled()
    async with sqlite_sessionmaker() as data:
        assert not (await data.execute(sqlmodel.select(sql.Task))).first()


@REQUIRES_SLH
def test_seal_catches_up_and_preserves_chain(directories) -> None:
    daily, mount = directories
    for remaining in (True, True, False):
        assert audit._seal_day(THROUGH) is remaining
    public = (daily / "root.der").read_bytes()
    for day in range(1, 4):
        data = daily / f"2020-01-{day:02}.jsonl"
        public = sealing.verify(data, public)
        if day > 1:
            assert data.read_bytes() == b""
    assert not (daily / "2020-01-04.seal.json").exists()
    before = {path: path.stat().st_ino for path in [*daily.iterdir(), mount / "keys/current.pem"]}
    assert audit._seal_day(THROUGH) is False
    assert {path: path.stat().st_ino for path in before} == before


@REQUIRES_SLH
@pytest.mark.parametrize(
    "failure", ["initialisation", "next", "root", "keys", "rollback", "seal_gap", "day", "last_day"]
)
def test_seal_refuses_inconsistent_state(directories, tmp_path: pathlib.Path, failure: str) -> None:
    daily, mount = directories
    keys = mount / "keys"
    if failure == "initialisation":
        daylog.close(daily, THROUGH)
        keys.mkdir()
    else:
        audit._seal_day(THROUGH)
        if failure == "next":
            (keys / "next.pem").write_bytes(b"interrupted")
        elif failure == "root":
            (daily / "root.der").unlink()
        elif failure == "keys":
            (keys / "current.pem").unlink()
        elif failure == "rollback":
            (daily / "2020-01-01.seal.json").unlink()
        elif failure == "seal_gap":
            audit._seal_day(THROUGH)
            (daily / "2020-01-01.seal.json").unlink()
        elif failure == "day":
            (daily / "2020-01-02.jsonl").unlink()
        elif failure == "last_day":
            (daily / "2020-01-03.jsonl").unlink()
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises((ValueError, FileExistsError, FileNotFoundError)):
        audit._seal_day(THROUGH)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
