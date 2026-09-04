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
from typing import Final

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.config as config
import atr.db as db
import atr.models.args as args
import atr.models.results as results
import atr.models.sql as sql
import atr.storage.datatypes as datatypes
import atr.storage.writers.release as release_writer

INTERNAL_PUBLISH_URL: Final[str] = "https://internal.example.invalid/repos/dist/release"
# The release lives under the committee dir "project" plus its "1.0.0" suffix.
DIST_DIR: Final[str] = "project/1.0.0"
ARTIFACT: Final[str] = "apache-project-1.0.0.tar.gz"
SIGNATURE: Final[str] = "apache-project-1.0.0.tar.gz.asc"
CHECKSUM: Final[str] = "apache-project-1.0.0.tar.gz.sha512"
SBOM: Final[str] = "apache-project-1.0.0.tar.gz.cdx.json"
ALL_FILES: Final[list[str]] = [ARTIFACT, SIGNATURE, CHECKSUM, SBOM]
# svn 1.14 stderr for a listing of a missing directory, over file:// and https.
MISSING_DIR_OUTPUT: Final[str] = (
    "svn: warning: W170000: URL 'https://svn/x' non-existent in revision 87117\n"
    "svn: E200009: Could not display info for all targets because some targets don't exist"
)


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


def _args() -> args.SvnUnpublish:
    return args.SvnUnpublish(asf_uid="alice", project_key="project", version_key="1.0.0")


async def test_unpublish_takes_an_owned_directory_whole(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    # The directory holds nothing but this release's files, so it comes out in one
    # action - its files and the emptied directory together - not file by file.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=ALL_FILES))
    # svnmucc reports the commit in its own format, not svn's "Committed revision N."
    remove_files = mock.AsyncMock(return_value="r7 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert isinstance(result, results.SvnUnpublish)
        assert result.svn_revision == 7
        remove_files.assert_awaited_once()
        base_url, rel_paths = remove_files.await_args.args[0], remove_files.await_args.args[1]
        assert base_url == INTERNAL_PUBLISH_URL
        assert rel_paths == [DIST_DIR]


async def test_unpublish_never_removes_the_directory_itself(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A flat layout may hold other releases' files alongside ours, so removal is
    # by explicit file - the bare directory must never be a removal target.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    listed = [*ALL_FILES, "other-2.0.0.tar.gz"]
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=listed))
    remove_files = mock.AsyncMock(return_value="r8 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        await writer.unpublish_from_svn_execute(_args())

        rel_paths = remove_files.await_args.args[1]
        assert DIST_DIR not in rel_paths
        assert f"{DIST_DIR}/other-2.0.0.tar.gz" not in rel_paths
        assert rel_paths == sorted(f"{DIST_DIR}/{name}" for name in ALL_FILES)


async def test_unpublish_in_a_shared_directory_skips_a_file_already_gone(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A foreign file keeps the directory in place, so removal is by file; a recorded
    # file that has already gone is left out rather than fail the atomic svnmucc commit.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    present = [ARTIFACT, CHECKSUM, SBOM, "other-2.0.0.tar.gz"]
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=present))
    remove_files = mock.AsyncMock(return_value="r9 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        await writer.unpublish_from_svn_execute(_args())

        rel_paths = remove_files.await_args.args[1]
        assert rel_paths == sorted(f"{DIST_DIR}/{name}" for name in (ARTIFACT, CHECKSUM, SBOM))
        assert f"{DIST_DIR}/{SIGNATURE}" not in rel_paths
        assert f"{DIST_DIR}/other-2.0.0.tar.gz" not in rel_paths


async def test_unpublish_takes_an_owned_directory_with_a_subdirectory_whole(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The release put its files in a subdirectory of its own and the directory holds
    # nothing else, so the whole directory - the subdirectory and its files - comes out
    # in one action, leaving no empty directory behind.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(
        release_writer.svn, "list_files", mock.AsyncMock(return_value=[f"binaries/{name}" for name in ALL_FILES])
    )
    remove_files = mock.AsyncMock(return_value="r11 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data, subdir="binaries")
        writer = _admin_writer(data)

        await writer.unpublish_from_svn_execute(_args())

        rel_paths = remove_files.await_args.args[1]
        assert rel_paths == [DIST_DIR]


async def test_unpublish_keeps_a_subdirectory_shared_with_another_release(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A foreign file shares the subdirectory, so the directory must stay: only our
    # own files come out, one by one, and the directory is never a removal target.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    listed = [*[f"binaries/{name}" for name in ALL_FILES], "binaries/other-2.0.0.tar.gz"]
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=listed))
    remove_files = mock.AsyncMock(return_value="r12 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data, subdir="binaries")
        writer = _admin_writer(data)

        await writer.unpublish_from_svn_execute(_args())

        rel_paths = remove_files.await_args.args[1]
        assert f"{DIST_DIR}/binaries" not in rel_paths
        assert f"{DIST_DIR}/binaries/other-2.0.0.tar.gz" not in rel_paths
        assert rel_paths == sorted(f"{DIST_DIR}/binaries/{name}" for name in ALL_FILES)


async def test_unpublish_skips_a_directory_that_lists_as_empty(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A directory that lists as empty must never become a removal target: svnmucc is
    # atomic, so an rm of a path that isn't there would fail the whole commit.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=[]))
    remove_files = mock.AsyncMock()
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data, subdir="binaries")
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.svn_revision is None
        remove_files.assert_not_awaited()


async def test_unpublish_removes_manifest_files_including_a_readme(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The attestable manifest lists every file the bundle published, so a README that is
    # not a recorded artifact is still removed. A foreign file keeps the directory, so the
    # removal is by file and the README is an explicit target.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    manifest = SimpleNamespace(paths={name: None for name in (*ALL_FILES, "README")})
    monkeypatch.setattr(release_writer.attestable, "latest_revision_number", mock.AsyncMock(return_value="00001"))
    monkeypatch.setattr(release_writer.attestable, "load", mock.AsyncMock(return_value=manifest))
    listed = [*ALL_FILES, "README", "other-2.0.0.tar.gz"]
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=listed))
    remove_files = mock.AsyncMock(return_value="r13 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        await writer.unpublish_from_svn_execute(_args())

        rel_paths = remove_files.await_args.args[1]
        assert f"{DIST_DIR}/README" in rel_paths
        assert f"{DIST_DIR}/other-2.0.0.tar.gz" not in rel_paths
        assert rel_paths == sorted(f"{DIST_DIR}/{name}" for name in (*ALL_FILES, "README"))


async def test_unpublish_at_the_committee_root_removes_a_nested_file(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A prefix-less publish puts the release straight in the committee root. One recursive
    # listing finds a file the release put in a subdirectory there, while the shared root -
    # which also holds KEYS and other releases - is never taken.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    manifest = SimpleNamespace(
        paths={"apache-project-1.0.0.tar.gz": None, "binaries/apache-project-1.0.0-bin.zip": None}
    )
    monkeypatch.setattr(release_writer.attestable, "latest_revision_number", mock.AsyncMock(return_value="00001"))
    monkeypatch.setattr(release_writer.attestable, "load", mock.AsyncMock(return_value=manifest))
    listed = ["apache-project-1.0.0.tar.gz", "binaries/apache-project-1.0.0-bin.zip", "KEYS", "9.9.9/other.tar.gz"]
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=listed))
    remove_files = mock.AsyncMock(return_value="r14 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        # An empty release suffix is the deliberate committee root; the artifact sits there too
        await _seed_release_with_artifacts(data, directory="project", release_suffix="")
        writer = _admin_writer(data)

        await writer.unpublish_from_svn_execute(_args())

        rel_paths = remove_files.await_args.args[1]
        assert "project/binaries/apache-project-1.0.0-bin.zip" in rel_paths
        assert "project" not in rel_paths
        assert "project/KEYS" not in rel_paths
        assert "project/9.9.9/other.tar.gz" not in rel_paths
        assert rel_paths == sorted(
            ("project/apache-project-1.0.0.tar.gz", "project/binaries/apache-project-1.0.0-bin.zip")
        )


async def test_unpublish_reports_leftover_files_for_a_fallback_removal(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A catalogued release has no manifest, so removal falls back to the recorded artifacts,
    # which can't account for a README. It is left behind and reported for manual cleanup.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(release_writer.attestable, "latest_revision_number", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=[*ALL_FILES, "README.md"]))
    remove_files = mock.AsyncMock(return_value="r15 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.leftover == [f"{DIST_DIR}/README.md"]
        assert remove_files.await_args.args[1] == sorted(f"{DIST_DIR}/{name}" for name in ALL_FILES)


async def test_unpublish_does_not_report_leftovers_for_a_manifest_removal(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A manifest removal is complete, so anything else in a shared directory belongs to another
    # release, not to us - it must not be reported as our leftover.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    manifest = SimpleNamespace(paths={name: None for name in ALL_FILES})
    monkeypatch.setattr(release_writer.attestable, "latest_revision_number", mock.AsyncMock(return_value="00001"))
    monkeypatch.setattr(release_writer.attestable, "load", mock.AsyncMock(return_value=manifest))
    monkeypatch.setattr(
        release_writer.svn, "list_files", mock.AsyncMock(return_value=[*ALL_FILES, "other-2.0.0.tar.gz"])
    )
    monkeypatch.setattr(
        release_writer.svn,
        "remove_files",
        mock.AsyncMock(return_value="r16 committed by alice at 2026-09-02T14:23:07Z"),
    )
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.leftover == []


async def test_unpublish_does_not_report_leftovers_at_the_committee_root(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A prefix-less release sits in the shared committee root, whose other entries (KEYS,
    # sibling releases) are not ours - a fallback removal must not report them as leftovers.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(release_writer.attestable, "latest_revision_number", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(
        release_writer.svn, "list_files", mock.AsyncMock(return_value=[*ALL_FILES, "KEYS", "other-9.9.9.tar.gz"])
    )
    monkeypatch.setattr(
        release_writer.svn,
        "remove_files",
        mock.AsyncMock(return_value="r17 committed by alice at 2026-09-02T14:23:07Z"),
    )
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data, directory="project")
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.leftover == []


async def test_unpublish_does_not_report_leftovers_in_a_shared_category_directory(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Some projects drop every release into one flat directory (plugins/, providers/) that carries
    # no version. Its other entries are sibling releases, not our leftovers, so a fallback removal
    # takes only our files and reports nothing left behind.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(release_writer.attestable, "latest_revision_number", mock.AsyncMock(return_value=None))
    monkeypatch.setattr(
        release_writer.svn, "list_files", mock.AsyncMock(return_value=[*ALL_FILES, "other-plugin-2.0.0.tar.gz"])
    )
    remove_files = mock.AsyncMock(return_value="r18 committed by alice at 2026-09-02T14:23:07Z")
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data, directory="project/plugins")
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.leftover == []
        assert remove_files.await_args.args[1] == sorted(f"project/plugins/{name}" for name in ALL_FILES)


async def test_unpublish_reports_absent_when_the_directory_is_gone(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    listing = mock.AsyncMock(side_effect=release_writer.svn.CommandExecutionError(1, MISSING_DIR_OUTPUT))
    monkeypatch.setattr(release_writer.svn, "list_files", listing)
    remove_files = mock.AsyncMock()
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.svn_revision is None
        remove_files.assert_not_awaited()


async def test_unpublish_fails_the_task_on_a_transient_svn_error(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A connection error is not proof the files are gone, so the task must fail
    # and retry rather than falsely report the removal done.
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    listing = mock.AsyncMock(
        side_effect=release_writer.svn.CommandExecutionError(1, "svn: E170013: Unable to connect to a repository")
    )
    monkeypatch.setattr(release_writer.svn, "list_files", listing)
    remove_files = mock.AsyncMock()
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        with pytest.raises(datatypes.FailedError):
            await writer.unpublish_from_svn_execute(_args())
        remove_files.assert_not_awaited()


async def test_unpublish_reports_when_no_files_are_recorded(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    list_files = mock.AsyncMock()
    monkeypatch.setattr(release_writer.svn, "list_files", list_files)
    remove_files = mock.AsyncMock()
    monkeypatch.setattr(release_writer.svn, "remove_files", remove_files)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data, with_artifact=False)
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.svn_revision is None
        list_files.assert_not_awaited()
        remove_files.assert_not_awaited()


async def test_unpublish_omits_the_revision_when_svn_reports_none(
    sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", INTERNAL_PUBLISH_URL, raising=False)
    monkeypatch.setattr(release_writer.svn, "list_files", mock.AsyncMock(return_value=ALL_FILES))
    monkeypatch.setattr(release_writer.svn, "remove_files", mock.AsyncMock(return_value="done, no revision line"))
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        result = await writer.unpublish_from_svn_execute(_args())

        assert result.svn_revision is None
        assert "rNone" not in result.message


async def test_unpublish_fails_when_svn_is_not_configured(sqlite_sessionmaker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.get(), "SVN_PUBLISH_URL", None, raising=False)
    async with sqlite_sessionmaker() as data:
        await _seed_release_with_artifacts(data)
        writer = _admin_writer(data)

        with pytest.raises(datatypes.FailedError):
            await writer.unpublish_from_svn_execute(_args())


def _admin_writer(data: db.Session) -> release_writer.FoundationAdmin:
    writer = object.__new__(release_writer.FoundationAdmin)
    writer._FoundationAdmin__asf_uid = "alice"
    writer._FoundationAdmin__data = data
    writer._FoundationAdmin__write_as = SimpleNamespace(append_to_audit_log=mock.Mock())
    writer._FoundationAdmin__write = mock.MagicMock()
    return writer


async def _seed_release_with_artifacts(
    data: db.Session,
    *,
    with_artifact: bool = True,
    subdir: str | None = None,
    directory: str = DIST_DIR,
    release_suffix: str = "1.0.0",
) -> None:
    committee = sql.Committee(
        key="project",
        name="Project",
        is_podling=False,
        committee_members=["alice"],
        committers=["alice"],
    )
    project = sql.Project(key="project", name="Project", committee=committee)
    release = sql.Release(
        key="project-1.0.0",
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        project_key=project.key,
        version="1.0.0",
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        is_archived=True,
        archived=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
        download_path_suffix=release_suffix,
    )
    rows: list[object] = [committee, project, release]
    if with_artifact:
        # A subdir models a release that published into a subdirectory of its own,
        # so the recorded file name carries that prefix while the directory stays the base.
        prefix = f"{subdir}/" if subdir else ""
        rows.append(
            sql.Artifact(
                project_key="project",
                version="1.0.0",
                artifact_path=f"{prefix}{ARTIFACT}",
                signature_path=f"{prefix}{SIGNATURE}",
                checksum_path=f"{prefix}{CHECKSUM}",
                sbom_path=f"{prefix}{SBOM}",
                release_key="project-1.0.0",
                managed=True,
                download_path_suffix=directory,
            )
        )
    data.add_all(rows)
    await data.commit()
