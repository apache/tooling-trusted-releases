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
import types
import unittest.mock as mock
from collections.abc import AsyncIterator

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.db as db
import atr.models.api as api
import atr.models.distribution as models_distribution
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.writers.distributions as distributions
import atr.storage.writers.project as project_writer

_MAVEN = sql.DistributionPlatform.MAVEN
_NAMESPACE = "org.apache.project"
_PROJECT = safe.ProjectKey("project")
_RELEASE = safe.ReleaseKey("project-1.0.0")
_UPLOADED = datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC)


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
async def test_change_version_replaces_staging_row(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        data.add(_distribution("core", sql.DistributionStatus.FOUND, version="1.0", staging=True))
        await data.commit()

        await _release_manager(data).change_version(
            _RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", safe.VersionKey("1.0")
        )

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.version, row.staging, row.status) == ("1.0", False, sql.DistributionStatus.CHECKING)


@pytest.mark.asyncio
async def test_change_version_replaces_the_row(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING, retries=7))
        await data.commit()

        await _release_manager(data).change_version(
            _RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", safe.VersionKey("1.0")
        )

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.version, row.status, row.retries) == ("1.0", sql.DistributionStatus.CHECKING, 0)


@pytest.mark.asyncio
async def test_check_drops_row_outside_finish_at_retry_limit(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING, retries=5))
        await data.commit()

        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        assert await data.distribution(release_key=str(_RELEASE)).all() == []


@pytest.mark.asyncio
async def test_check_drops_staging_row_at_retry_limit(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING, staging=True, retries=5))
        await data.commit()

        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        assert await data.distribution(release_key=str(_RELEASE)).all() == []


@pytest.mark.asyncio
async def test_check_finds_staged_row_as_a_release(sqlite_sessionmaker, monkeypatch) -> None:
    found = models_distribution.Metadata(
        api_url="https://api.example/core", result={}, upload_date=_UPLOADED, web_url=None
    )
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING, staging=True, retries=9))
        await data.commit()
        look_up = mock.AsyncMock(return_value=found)
        monkeypatch.setattr(distributions, "_look_up", look_up)

        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (look_up.await_args.args[1], row.staging, row.is_found) == (False, False, True)


@pytest.mark.asyncio
async def test_check_leaves_embargoed_release_alone(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        release = await _seed_release(data)
        release.expedited = True
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()
        look_up = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(distributions, "_look_up", look_up)

        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (look_up.await_count, row.last_checked, row.retries) == (0, None, 0)


@pytest.mark.asyncio
async def test_check_leaves_row_ignored_during_lookup(sqlite_sessionmaker, monkeypatch) -> None:
    found = models_distribution.Metadata(
        api_url="https://api.example/core", result={}, upload_date=_UPLOADED, web_url=None
    )
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()

        async def ignore_during_lookup(*_args):
            await _release_manager(data).ignore(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", "Not built")
            return found

        monkeypatch.setattr(distributions, "_look_up", ignore_during_lookup)
        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.status, row.ignored_reason, row.upload_date) == (sql.DistributionStatus.IGNORED, "Not built", None)


@pytest.mark.asyncio
async def test_check_leaves_version_changed_during_lookup(sqlite_sessionmaker, monkeypatch) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()

        async def change_during_lookup(*_args):
            await _release_manager(data).change_version(
                _RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", safe.VersionKey("1.0")
            )
            return None

        monkeypatch.setattr(distributions, "_look_up", change_during_lookup)
        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.version, row.status, row.retries) == ("1.0", sql.DistributionStatus.CHECKING, 0)


@pytest.mark.asyncio
async def test_check_looks_up_row_outside_finish(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(distributions, "_look_up", mock.AsyncMock(return_value=None))
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()

        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert row.retries == 1


@pytest.mark.asyncio
async def test_check_now_makes_outstanding_rows_due(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING, retries=20, last_checked=_UPLOADED))
        data.add(_distribution("api", sql.DistributionStatus.FOUND, retries=1, last_checked=_UPLOADED))
        await data.commit()

        assert await _release_manager(data).check_now(_RELEASE) == 1

        rows = {row.package: row for row in await data.distribution(release_key=str(_RELEASE)).all()}
        assert (rows["core"].retries, rows["core"].last_checked) == (0, None)
        assert (rows["api"].retries, rows["api"].last_checked) == (1, _UPLOADED)


@pytest.mark.asyncio
async def test_check_records_found(sqlite_sessionmaker, monkeypatch) -> None:
    found = models_distribution.Metadata(
        api_url="https://api.example/core", result={}, upload_date=_UPLOADED, web_url="https://web.example/core"
    )
    monkeypatch.setattr(distributions, "_look_up", mock.AsyncMock(return_value=found))
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()

        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert row.status == sql.DistributionStatus.FOUND
        assert (row.upload_date, row.web_url) == (_UPLOADED, "https://web.example/core")
        assert row.last_checked is not None


@pytest.mark.asyncio
async def test_check_records_not_found(sqlite_sessionmaker, monkeypatch) -> None:
    monkeypatch.setattr(distributions, "_look_up", mock.AsyncMock(return_value=None))
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING, retries=2))
        await data.commit()

        await _admin(data).check(str(_RELEASE), _MAVEN, _NAMESPACE, "core", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.status, row.retries) == (sql.DistributionStatus.CHECKING, 3)
        assert row.last_checked is not None


@pytest.mark.asyncio
async def test_check_without_a_namespace(sqlite_sessionmaker, monkeypatch) -> None:
    looked_up = mock.AsyncMock(return_value=None)
    monkeypatch.setattr(distributions, "_look_up", looked_up)
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(
            _distribution(
                "project", sql.DistributionStatus.CHECKING, platform=sql.DistributionPlatform.PYPI, owner_namespace=""
            )
        )
        await data.commit()

        await _admin(data).check(str(_RELEASE), sql.DistributionPlatform.PYPI, "", "project", "1.0.0")
        data.expire_all()

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert row.retries == 1
        assert looked_up.await_args.args[0].owner_namespace is None


@pytest.mark.asyncio
async def test_delete_refuses_an_outstanding_row(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()

        with pytest.raises(storage.AccessError):
            await _release_manager(data).delete_distribution(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0")

        assert len(await data.distribution(release_key=str(_RELEASE)).all()) == 1


@pytest.mark.asyncio
async def test_expect_for_release(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core"))
        data.add(_expected("image", version_pattern="{{VERSION}}-jdk17"))
        data.add(_expected("staged"))
        data.add(_distribution("staged", sql.DistributionStatus.FOUND, staging=True, upload_date=_UPLOADED))
        await data.commit()

        await _release_manager(data).expect_for_release_no_commit(_RELEASE)
        await data.commit()

        rows = {row.package: row for row in await data.distribution(release_key=str(_RELEASE)).all()}
        assert {package: row.version for package, row in rows.items()} == {
            "core": "1.0.0",
            "image": "1.0.0-jdk17",
            "staged": "1.0.0",
        }
        assert all(row.is_outstanding for row in rows.values())
        assert (rows["staged"].staging, rows["staged"].upload_date) == (True, _UPLOADED)
        assert not rows["core"].staging


@pytest.mark.asyncio
async def test_expect_for_release_carries_staged_rows(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core"))
        staged = {"staging": True, "upload_date": _UPLOADED}
        data.add(_distribution("core", sql.DistributionStatus.FOUND, version="1.0.0rc1", **staged))
        data.add(_distribution("extra", sql.DistributionStatus.FOUND, version="1.0.0rc1", **staged))
        data.add(_distribution("unseen", sql.DistributionStatus.CHECKING, staging=True))
        await data.commit()
        writer = _release_manager(data)

        await writer.expect_for_release_no_commit(_RELEASE)
        await data.commit()
        await writer.expect_for_release_no_commit(_RELEASE)
        await data.commit()

        rows = await data.distribution(release_key=str(_RELEASE)).all()
        # The expected version wins for a package the project names, and a staged one it doesn't name is kept
        assert sorted((row.package, row.version, row.staging, row.is_outstanding) for row in rows) == [
            ("core", "1.0.0", False, True),
            ("extra", "1.0.0rc1", True, True),
        ]


@pytest.mark.asyncio
async def test_expected_add_docker_hub_requires_release_version(sqlite_sessionmaker) -> None:
    docker_hub = sql.DistributionPlatform.DOCKER_HUB
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        writer = _release_manager(data)

        with pytest.raises(storage.AccessError):
            await writer.expected_add(_PROJECT, docker_hub, None, [safe.Alphanumeric("core")], "{{MAJOR_VERSION}}")
        added = await writer.expected_add(_PROJECT, docker_hub, None, [safe.Alphanumeric("core")], "{{VERSION}}-jdk17")

        assert added == 1


@pytest.mark.asyncio
async def test_expected_add_from_latest_release(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core"))
        data.add(_distribution("core", sql.DistributionStatus.FOUND))
        data.add(_distribution("api", sql.DistributionStatus.FOUND))
        data.add(_distribution("tool", sql.DistributionStatus.FOUND, version="0.3"))
        data.add(_distribution("pending", sql.DistributionStatus.CHECKING))
        await data.commit()

        data.add(_expected("image", version_pattern="{{VERSION}}-jdk17"))
        data.add(_distribution("image", sql.DistributionStatus.FOUND, version="1.0.0-jdk17"))
        await data.commit()

        assert await _release_manager(data).expected_add_from_latest_release(_PROJECT) == 1

        rows = await data.expected_distribution(project_key=str(_PROJECT)).all()
        assert {(row.package, row.version_pattern) for row in rows} == {
            ("core", "{{VERSION}}"),
            ("api", "{{VERSION}}"),
            ("image", "{{VERSION}}-jdk17"),
        }


@pytest.mark.asyncio
async def test_expected_add_requires_namespace(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)

        with pytest.raises(storage.AccessError):
            await _release_manager(data).expected_add(_PROJECT, _MAVEN, None, [safe.Alphanumeric("core")], "")


@pytest.mark.asyncio
async def test_expected_add_skips_existing(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core"))
        await data.commit()
        packages = [safe.Alphanumeric("core"), safe.Alphanumeric("api"), safe.Alphanumeric("api")]

        added = await _release_manager(data).expected_add(
            _PROJECT, _MAVEN, safe.OwnerNamespace(_NAMESPACE), packages, ""
        )

        assert added == 1
        rows = await data.expected_distribution(project_key=str(_PROJECT)).all()
        assert {(row.package, row.version_pattern) for row in rows} == {("core", "{{VERSION}}"), ("api", "{{VERSION}}")}


@pytest.mark.asyncio
async def test_expected_remove(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core"))
        data.add(_expected("api"))
        await data.commit()

        await _release_manager(data).expected_remove(_PROJECT, _MAVEN, _NAMESPACE, "core", "{{VERSION}}")

        rows = await data.expected_distribution(project_key=str(_PROJECT)).all()
        assert [row.package for row in rows] == ["api"]


@pytest.mark.asyncio
async def test_expected_replace(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core", created_by="alice"))
        data.add(_expected("gone"))
        await data.commit()
        given = [
            api.ProjectConfigDistributionArgs(platform="MAVEN", owner_namespace=_NAMESPACE, packages=["core", "api"]),
            api.ProjectConfigDistributionArgs(platform="PYPI", packages=["project"], version="{{VERSION}}.post1"),
        ]

        await _admin(data).expected_replace_no_commit(_PROJECT, given)
        await data.commit()

        rows = await data.expected_distribution(project_key=str(_PROJECT)).all()
        assert {(row.platform.name, row.package, row.version_pattern, row.created_by) for row in rows} == {
            ("MAVEN", "core", "{{VERSION}}", "alice"),
            ("MAVEN", "api", "{{VERSION}}", None),
            ("PYPI", "project", "{{VERSION}}.post1", None),
        }


@pytest.mark.asyncio
async def test_ignore_requires_reason_and_outstanding_row(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        data.add(_distribution("api", sql.DistributionStatus.FOUND))
        await data.commit()
        writer = _release_manager(data)

        with pytest.raises(storage.AccessError):
            await writer.ignore(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", "  ")
        with pytest.raises(storage.AccessError):
            await writer.ignore(_RELEASE, _MAVEN, _NAMESPACE, "api", "1.0.0", "Not needed")


@pytest.mark.asyncio
async def test_ignore_then_unignore(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING, retries=4, last_checked=_UPLOADED))
        await data.commit()
        writer = _release_manager(data)

        await writer.ignore(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", " Not built this time ")

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.status, row.ignored_by, row.ignored_reason) == (
            sql.DistributionStatus.IGNORED,
            "chair",
            "Not built this time",
        )

        await writer.unignore(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0")

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.status, row.ignored_by, row.ignored_reason) == (sql.DistributionStatus.CHECKING, None, None)
        assert (row.retries, row.last_checked) == (0, None)


@pytest.mark.asyncio
async def test_record_fills_in_default_namespace(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)

        await _release_manager(data).record(
            _RELEASE,
            sql.DistributionPlatform.DOCKER_HUB,
            None,
            safe.Alphanumeric("project"),
            safe.VersionKey("1.0.0"),
            False,
            False,
            _UPLOADED,
        )

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert row.owner_namespace == "library"


@pytest.mark.asyncio
async def test_record_found_clears_ignore(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.IGNORED, ignored_by="chair", ignored_reason="Not built"))
        await data.commit()

        await _record(data, staging=False)

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (row.status, row.ignored_by, row.ignored_reason) == (sql.DistributionStatus.FOUND, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    [sql.ReleasePhase.RELEASE_CANDIDATE, sql.ReleasePhase.RELEASE_PREVIEW, sql.ReleasePhase.RELEASE],
)
async def test_record_from_data_refuses_staging_outside_compose(sqlite_sessionmaker, phase) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase)
        dd = models_distribution.Data(
            platform=_MAVEN,
            owner_namespace=safe.OwnerNamespace(_NAMESPACE),
            package=safe.Alphanumeric("core"),
            version=safe.VersionKey("1.0.0"),
            details=False,
        )

        with pytest.raises(storage.AccessError, match="only be recorded during compose") as exc_info:
            await _release_manager(data).record_from_data(_RELEASE, True, dd)

        assert exc_info.value.status == 409
        assert await data.distribution(release_key=str(_RELEASE)).all() == []


@pytest.mark.asyncio
async def test_record_staging_leaves_an_outstanding_row(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()

        _row, added = await _record(data, staging=True)

        (row,) = await data.distribution(release_key=str(_RELEASE)).all()
        assert (added, row.staging, row.status, row.api_url) == (False, False, sql.DistributionStatus.CHECKING, None)


@pytest.mark.asyncio
async def test_release_actions_refused_while_embargoed(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        release = await _seed_release(data)
        release.expedited = True
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        await data.commit()
        writer = _release_manager(data)

        with pytest.raises(storage.AccessError):
            await writer.ignore(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", "Not needed")
        with pytest.raises(storage.AccessError):
            await writer.change_version(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", safe.VersionKey("1.0"))
        with pytest.raises(storage.AccessError):
            await writer.check_now(_RELEASE)
        with pytest.raises(storage.AccessError):
            await writer.record_from_data(
                _RELEASE, False, _distribution("core", sql.DistributionStatus.CHECKING).distribution_data()
            )


@pytest.mark.asyncio
async def test_release_actions_require_finish_phase(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data, phase=sql.ReleasePhase.RELEASE)
        data.add(_distribution("core", sql.DistributionStatus.CHECKING))
        data.add(_distribution("api", sql.DistributionStatus.IGNORED, ignored_by="chair", ignored_reason="Not built"))
        await data.commit()
        writer = _release_manager(data)

        with pytest.raises(storage.AccessError):
            await writer.ignore(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", "Not needed")
        with pytest.raises(storage.AccessError):
            await writer.unignore(_RELEASE, _MAVEN, _NAMESPACE, "api", "1.0.0")
        with pytest.raises(storage.AccessError):
            await writer.change_version(_RELEASE, _MAVEN, _NAMESPACE, "core", "1.0.0", safe.VersionKey("1.0"))
        with pytest.raises(storage.AccessError):
            await writer.check_now(_RELEASE)


@pytest.mark.asyncio
@pytest.mark.parametrize("sync_mode", ["merge", "full"])
async def test_upsert_config_leaves_expected_alone_unless_given(sqlite_sessionmaker, sync_mode) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core"))
        await data.commit()

        await _config_writer(data).upsert_config(
            api.ProjectConfigArgs(project_key="project", committee_key="project", sync_mode=sync_mode),
            sql.UpdateType.ASFYAML,
        )

        rows = await data.expected_distribution(project_key=str(_PROJECT)).all()
        assert [row.package for row in rows] == ["core"]


@pytest.mark.asyncio
async def test_upsert_config_replaces_expected(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        await _seed_release(data)
        data.add(_expected("core"))
        await data.commit()
        given = [api.ProjectConfigDistributionArgs(platform="pypi", packages=["project"])]

        await _config_writer(data).upsert_config(
            api.ProjectConfigArgs(project_key="project", committee_key="project", distributions=given),
            sql.UpdateType.ASFYAML,
        )

        rows = await data.expected_distribution(project_key=str(_PROJECT)).all()
        assert [(row.platform.name, row.package) for row in rows] == [("PYPI", "project")]


def _admin(data: db.Session) -> distributions.FoundationAdmin:
    writer = object.__new__(distributions.FoundationAdmin)
    writer._FoundationAdmin__data = data
    return writer


def _config_writer(data: db.Session) -> project_writer.FoundationAdmin:
    writer = object.__new__(project_writer.FoundationAdmin)
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__asf_uid = "system"
    writer._FoundationAdmin__write_as = types.SimpleNamespace(
        distributions=_admin(data),
        policy=types.SimpleNamespace(edit_no_commit=mock.AsyncMock()),
        append_to_audit_log=mock.MagicMock(),
    )
    return writer


def _distribution(
    package: str, status: sql.DistributionStatus, *, version: str = "1.0.0", **fields
) -> sql.Distribution:
    return sql.Distribution(
        release_key=str(_RELEASE),
        platform=fields.pop("platform", _MAVEN),
        owner_namespace=fields.pop("owner_namespace", _NAMESPACE),
        package=package,
        version=version,
        status=status,
        **fields,
    )


def _expected(package: str, **fields) -> sql.ExpectedDistribution:
    return sql.ExpectedDistribution(
        project_key=str(_PROJECT), platform=_MAVEN, owner_namespace=_NAMESPACE, package=package, **fields
    )


async def _record(data: db.Session, *, staging: bool) -> tuple[sql.Distribution, bool]:
    return await _release_manager(data).record(
        _RELEASE,
        _MAVEN,
        safe.OwnerNamespace(_NAMESPACE),
        safe.Alphanumeric("core"),
        safe.VersionKey("1.0.0"),
        staging,
        False,
        _UPLOADED,
        api_url="https://api.example/core",
        web_url="https://web.example/core",
    )


def _release_manager(data: db.Session) -> distributions.ReleaseManager:
    writer = object.__new__(distributions.ReleaseManager)
    writer._ReleaseManager__data = data
    writer._ReleaseManager__write = mock.MagicMock()
    writer._ReleaseManager__write_as = mock.MagicMock()
    writer._ReleaseManager__asf_uid = "chair"
    writer._ReleaseManager__committee_key = "project"
    return writer


async def _seed_release(data: db.Session, phase: sql.ReleasePhase = sql.ReleasePhase.RELEASE_PREVIEW) -> sql.Release:
    committee = sql.Committee(key="project", name="Project", committee_members=["chair"], committers=["chair"])
    project = sql.Project(key="project", name="Project", committee=committee)
    release = sql.Release(
        key=str(_RELEASE),
        phase=phase,
        project=project,
        project_key=project.key,
        version="1.0.0",
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )
    data.add(release)
    await data.commit()
    return release
