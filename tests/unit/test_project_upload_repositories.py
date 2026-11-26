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
from types import SimpleNamespace

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.api as api
import atr.models.sql as sql
import atr.storage.writers.project as project_writer

_BEFORE = {"apache/first"}


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("opted_in", "repositories"),
    [
        pytest.param(False, ["https://github.com/apache/second"], id="pmc-not-opted-in"),
        pytest.param(True, ["https://github.com/apache/first"], id="same-repositories"),
        pytest.param(True, ["https://github.com/apache/FIRST.git", "https://example.org/x"], id="same-set-respelt"),
    ],
)
async def test_change_is_not_recorded(opted_in: bool, repositories: list[str]) -> None:
    data = _data(opted_in)

    recorded = await project_writer._upload_repositories_change_no_commit(
        data, _project(repositories), _BEFORE, "alice"
    )

    assert recorded == {}
    data.add.assert_not_called()


@pytest.mark.asyncio
async def test_change_under_an_opted_in_pmc_is_audited_and_notified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_writer.util, "absolute_url", lambda path: f"https://atr.example{path}")
    data = _data(opted_in=True)
    project = _project(["https://github.com/apache/second", "https://github.com/apache/third"])

    recorded = await project_writer._upload_repositories_change_no_commit(data, project, _BEFORE, "alice")

    assert recorded == {
        "upload_repositories_before": ["apache/first"],
        "upload_repositories_after": ["apache/second", "apache/third"],
    }
    (task,) = data.add.call_args.args
    assert task.task_type == sql.TaskType.MESSAGE_SEND
    assert task.task_args["email_to"] == "private@one.apache.org"
    assert "apache/second" in task.task_args["body"]
    assert "apache/first" in task.task_args["body"]


@pytest.mark.asyncio
async def test_edit_metadata_puts_the_change_in_its_audit_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_writer.util, "absolute_url", lambda path: f"https://atr.example{path}")
    data = _data(opted_in=True)
    data.commit = mock.AsyncMock()
    project = _project(["https://github.com/apache/first"])
    data.project.return_value.demand = mock.AsyncMock(return_value=project)
    write_as = mock.MagicMock()
    writer = object.__new__(project_writer.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__write_as = write_as
    writer._ReleaseManager__asf_uid = "alice"
    writer._ReleaseManager__committee_key = "one"
    form = SimpleNamespace(
        project_key="one",
        display_name="One",
        description="",
        short_description="",
        homepage=None,
        lifecycle_page=None,
        download_page=None,
        bug_database=None,
        mailing_lists=None,
        repositories=["https://github.com/apache/second"],
        standards=[],
    )

    await writer.edit_metadata(form)

    write_as.append_to_audit_log.assert_called_once_with(
        asf_uid="alice",
        project_key="one",
        upload_repositories_before=["apache/first"],
        upload_repositories_after=["apache/second"],
    )
    data.add.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sync_mode", "project_args", "after"),
    [
        pytest.param("merge", {"repositories": ["https://github.com/apache/second"]}, ["apache/second"], id="merge"),
        pytest.param("full", {"description": "Kept"}, [], id="full-sync-omitting-the-list"),
    ],
)
async def test_config_sync_puts_the_change_in_its_audit_entry(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch, sync_mode: str, project_args: dict, after: list[str]
) -> None:
    monkeypatch.setattr(project_writer.util, "absolute_url", lambda path: f"https://atr.example{path}")
    async with sqlite_sessionmaker() as data:
        committee = sql.Committee(key="one", name="One", github_oidc_enabled=True)
        data.add(
            sql.Project(key="one", name="One", committee=committee, repositories=["https://github.com/apache/first"])
        )
        await data.commit()
        write_as = SimpleNamespace(
            append_to_audit_log=mock.MagicMock(),
            policy=SimpleNamespace(
                edit_no_commit=mock.AsyncMock(), cycle_overrides_sync_no_commit=mock.AsyncMock(return_value=[])
            ),
        )
        writer = object.__new__(project_writer.FoundationAdmin)
        writer._FoundationAdmin__data = data
        writer._FoundationAdmin__asf_uid = "system"
        writer._FoundationAdmin__write_as = write_as

        await writer.upsert_config(
            api.ProjectConfigArgs(project_key="one", committee_key="one", sync_mode=sync_mode, project=project_args),
            sql.UpdateType.ASFYAML,
        )

        write_as.append_to_audit_log.assert_called_once_with(
            asf_uid="system",
            committee_key="one",
            project_key="one",
            created=False,
            upload_repositories_before=["apache/first"],
            upload_repositories_after=after,
        )
        notices = await data.task(task_type=sql.TaskType.MESSAGE_SEND).all()
        assert [task.task_args["email_to"] for task in notices] == ["private@one.apache.org"]


@pytest.mark.parametrize(
    ("repositories", "recognised"),
    [
        pytest.param(["https://github.com/apache/Example.git"], {"apache/example"}, id="lower-cased"),
        pytest.param(["https://github.com/someone/example"], set(), id="not-under-apache"),
        pytest.param(["https://gitbox.apache.org/repos/asf/example.git"], set(), id="gitbox"),
        pytest.param(["git+ssh://git@github.com:apache/example.git"], set(), id="ssh"),
        pytest.param(
            ["https://github.com/apache/a/", "https://github.com/apache/b"], {"apache/a", "apache/b"}, id="several"
        ),
    ],
)
def test_upload_repositories_are_github_repositories_under_apache(
    repositories: list[str], recognised: set[str]
) -> None:
    assert _project(repositories).upload_repositories == recognised


def _data(opted_in: bool) -> mock.MagicMock:
    data = mock.MagicMock()
    committee = sql.Committee(key="one", name="One", github_oidc_enabled=opted_in)
    data.committee.return_value.get = mock.AsyncMock(return_value=committee)
    return data


def _project(repositories: list[str]) -> sql.Project:
    return sql.Project(key="one", name="One", committee_key="one", repositories=repositories)
