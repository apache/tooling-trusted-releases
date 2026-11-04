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
from typing import Any

import pytest
import sqlalchemy.ext.asyncio
import sqlalchemy.pool
import sqlmodel

import atr.db as db
import atr.models.calver as calver
import atr.models.results as results
import atr.models.sql as sql
import atr.validate as validate

CREATED = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
DRAFT = sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT
CANDIDATE = sql.ReleasePhase.RELEASE_CANDIDATE
PREVIEW = sql.ReleasePhase.RELEASE_PREVIEW
RELEASE = sql.ReleasePhase.RELEASE
EMAIL = sql.VoteMode.EMAIL
SHAPE = "vote or announce recipients with to, cc, and bcc"


def release(phase: sql.ReleasePhase, version: str = "1.0", project_key: str = "proj", **fields: Any) -> sql.Release:
    return sql.Release(project_key=project_key, version=version, phase=phase, created=CREATED, **fields)


@pytest.fixture
async def sqlite_sessionmaker() -> abc.AsyncIterator[sqlalchemy.ext.asyncio.async_sessionmaker[db.Session]]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    yield sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    await engine.dispose()


def task(
    task_id: int,
    task_type: sql.TaskType,
    status: sql.TaskStatus,
    version: str | None = None,
    revision: str | None = None,
    task_args: Any = None,
    **fields: Any,
) -> sql.Task:
    return sql.Task(
        id=task_id,
        task_type=task_type,
        status=status,
        task_args=task_args or {},
        asf_uid="alice",
        completed=CREATED if (status != sql.TaskStatus.QUEUED) else None,
        project_key="proj",
        version_key=version,
        revision_number=revision,
        **fields,
    )


@pytest.mark.parametrize(
    ("suffix", "count"),
    [(None, 0), ("", 0), ("{{PROJECT_KEY}}/{{VERSION}}", 0), ("/absolute", 1), ("a/../b", 1)],
)
def test_download_path_suffix(suffix: str | None, count: int) -> None:
    policy = sql.ReleasePolicy(download_path_suffix=suffix or "")
    project = sql.Project(key="proj", release_policy=policy)

    assert len(list(validate.release_download_path_suffix(release(DRAFT, download_path_suffix=suffix)))) == count
    assert len(list(validate.project_download_path_suffix(project))) == count


async def test_everything_audits_cross_record_consistency(sqlite_sessionmaker) -> None:
    async with sqlite_sessionmaker() as data:
        data.add(sql.Committee(key="proj", name="Proj"))
        policy = sql.ReleasePolicy(
            download_path_suffix="/absolute", recipient_defaults={"vote": "dev@proj.apache.org"}, min_hours=24
        )
        data.add(sql.Project(key="proj", name="Apache Proj", committee_key="proj", release_policy=policy))
        data.add(sql.Project(key="gone", name="Apache Gone", committee_key="proj", status=sql.ProjectStatus.RETIRED))
        voting = {"vote_started": CREATED, "vote_mode": EMAIL, "voted_revision_number": "00001"}
        data.add(release(CANDIDATE, "1.0", current_vote_seq=1, **voting))
        data.add(release(PREVIEW, "2.0", current_vote_seq=3, vote_resolved=CREATED, **voting))
        data.add(release(DRAFT, "1.0", "gone"))
        data.add(release(RELEASE, "0.6", archive_source=sql.ArchiveSource.MANUAL))
        data.add(release(RELEASE, "0.7", cycle_key="proj-other"))
        data.add(release(RELEASE, "0.8", is_archived=True))
        for version in ("0.5", "4.0", "5.0"):
            phase = RELEASE if (version == "0.5") else DRAFT
            data.add(release(phase, version, current_vote_seq=2, vote_started=CREATED))
        await data.commit()
        for release_key in ("proj-1.0", "proj-1.0", "proj-2.0"):
            data.add(sql.Revision(release_key=release_key, asfuid="alice", phase=DRAFT))
        data.add(sql.RevisionCounter(release_key="proj-0.9", last_allocated_number=7))
        for release_key, last in (("proj-1.0", 4), ("proj-4.0", 2), ("proj-5.0", 2)):
            data.add(sql.VoteCounter(release_key=release_key, last_allocated_number=last))
        data.add(
            sql.BallotPaper(
                release_key="proj-4.0",
                vote_seq=9,
                voter_asf_uid="alice",
                voter_fullname="Alice",
                choice=sql.VoteChoice.YES,
                is_binding_at_cast=True,
                revision_number_at_cast="00001",
                receipt_message_id="receipt",
            )
        )
        await data.commit()
        counter = await data.get(sql.RevisionCounter, "proj-2.0")
        assert counter is not None
        counter.last_allocated_number = 0

        published = results.SvnPublish(kind="svn_publish", svn_revision=10, message="Published")
        unusable = results.ReleaseFinalise(kind="release_finalise", audit_events=0, message="Wrong kind")
        finalise = sql.TaskType.RELEASE_FINALISE
        publish = sql.TaskType.SVN_PUBLISH
        validation = sql.TaskType.QUARANTINE_VALIDATE
        queued = sql.TaskStatus.QUEUED
        completed = sql.TaskStatus.COMPLETED
        failed = sql.TaskStatus.FAILED
        data.add(task(1, finalise, queued, "3.1", "00002"))
        data.add(task(2, publish, completed, "3.1", "00001", result=published))
        data.add(task(3, finalise, queued, "3.2", "00001"))
        data.add(task(4, publish, completed, "3.2", "00001", result=published))
        data.add(task(5, finalise, queued, "3.3", "00001"))
        data.add(task(6, publish, completed, "3.3", "00001", result=unusable))
        data.add(task(7, publish, completed, "3.3", "00001", result=published))
        data.add(task(8, validation, queued, "4.0", task_args={"quarantined_id": 1}))
        data.add(task(9, validation, failed, "4.0", task_args={"quarantined_id": 2}, error="Failed"))
        data.add(task(10, sql.TaskType.VOTE_INITIATE, failed, "5.0", task_args={"vote_seq": 6}, error="Failed"))
        data.add(task(11, sql.TaskType.VOTE_INITIATE, failed, "1.0", task_args={"vote_seq": "legacy"}, error="Failed"))

        for quarantined_id, release_key, status, completed_at, prior in [
            (1, "proj-4.0", sql.QuarantineStatus.PENDING, None, "proj-4.0 00001"),
            (2, "proj-4.0", sql.QuarantineStatus.PENDING, None, None),
            (3, "proj-4.0", sql.QuarantineStatus.PENDING, None, None),
            (4, "proj-4.0", sql.QuarantineStatus.FAILED, None, None),
            (5, "proj-1.0", sql.QuarantineStatus.STAGING, None, None),
            (6, "proj-4.0", sql.QuarantineStatus.ACKNOWLEDGED, CREATED, "proj-1.0 00002"),
        ]:
            data.add(
                sql.Quarantined(
                    id=quarantined_id,
                    release_key=release_key,
                    asf_uid="alice",
                    status=status,
                    token=f"token{quarantined_id}",
                    completed=completed_at,
                    prior_revision_key=prior,
                )
            )
        for ignore_id, checker_glob, message_glob in [(1, "atr.tasks.checks.*", None), (2, None, "^(unclosed")]:
            data.add(
                sql.CheckResultIgnore(
                    id=ignore_id,
                    asf_uid="alice",
                    created=CREATED,
                    project_key="proj",
                    checker_glob=checker_glob,
                    message_glob=message_glob,
                )
            )

        for token_id, asfuid, created_by, is_system in [
            (1, "alice", "alice", False),
            (2, None, "admin", True),
            (3, "bob", "admin", False),
            (4, "admin", "admin", True),
        ]:
            data.add(
                sql.PersonalAccessToken(
                    id=token_id,
                    asfuid=asfuid,
                    created_by=created_by,
                    is_system=is_system,
                    token_hash=f"hash{token_id}",
                    expires=CREATED,
                )
            )

        data.add(sql.Artifact(project_key="proj", version="0.1", artifact_path="old.tar.gz"))
        data.add(
            sql.Artifact(
                project_key="proj",
                version="1.0",
                artifact_path="a.tar.gz",
                release_key="proj-1.0",
                signature_path="a.tar.gz.asc",
                signature_sha3_256="0" * 64,
            )
        )
        data.add(sql.Artifact(project_key="proj", version="1.0", artifact_path="b.tar.gz", release_key="proj-2.0"))
        data.add(sql.Artifact(project_key="proj", version="1.0", artifact_path="c.tar.gz", signature_sha3_256="A" * 64))
        await data.commit()
        malformed = {"kind": "svn_publish", "svn_revision": 123}
        await data.execute(sqlalchemy.update(sql.Task).where(sql.Task.id == 4).values(result=malformed))
        await data.commit()

    async with sqlite_sessionmaker() as data:
        found = sorted([(d.validator, d.source) async for d in validate.everything(data)])

    assert [divergence for divergence in found if divergence[0] != "release_on_disk"] == [
        ("allocation_counters", "proj-2.0"),
        ("allocation_counters", "proj-2.0"),
        ("allocation_counters", "proj-4.0"),
        ("allocation_counters", "proj-5.0"),
        ("artifact_release_key", "proj 1.0 b.tar.gz"),
        ("artifact_signature", "proj 1.0 c.tar.gz"),
        ("artifact_signature", "proj 1.0 c.tar.gz"),
        ("check_result_ignores", "proj ignore 2"),
        ("personal_access_token_owner", "token 3"),
        ("personal_access_token_owner", "token 4"),
        ("project_download_path_suffix", "proj"),
        ("project_min_hours", "proj"),
        ("project_recipient_defaults", "proj"),
        ("quarantine_completed", "proj-4.0 quarantine 4"),
        ("quarantine_pending", "proj-4.0 quarantine 2"),
        ("quarantine_pending", "proj-4.0 quarantine 3"),
        ("quarantine_release", "proj-1.0 quarantine 5"),
        ("quarantine_revision", "proj-4.0 quarantine 6"),
        ("release_archived", "proj-0.6"),
        ("release_cycle", "proj-0.7"),
        ("release_finalise_publication", "proj 3.1 task 1"),
        ("release_finalise_publication", "proj 3.2 task 3"),
        ("release_project_active", "gone-1.0"),
        ("release_voted_revision", "proj-1.0"),
    ]


@pytest.mark.parametrize(
    ("method", "calver_format", "cycle_match", "expected"),
    [
        pytest.param(sql.VersionMethod.CALVER, None, None, [], id="unset"),
        pytest.param(sql.VersionMethod.CALVER, "(YY.MM).N", calver.cycle_regex("(YY.MM).N"), [], id="generated"),
        pytest.param(sql.VersionMethod.CALVER, "YYYY.MM.DD", None, [], id="no-cycle-span"),
        pytest.param(
            sql.VersionMethod.CALVER, "(YY.MM).N", None, [repr(calver.cycle_regex("(YY.MM).N"))], id="stale-matcher"
        ),
        pytest.param(sql.VersionMethod.CALVER, "(YY.MM", None, ["a valid calver format"], id="invalid"),
        pytest.param(
            sql.VersionMethod.SEMVER,
            "YYYY",
            None,
            ["calver_format to be unset when version_method is not calver"],
            id="not-calver",
        ),
    ],
)
def test_project_calver_format(
    method: sql.VersionMethod, calver_format: str | None, cycle_match: str | None, expected: list[str]
) -> None:
    project = sql.Project(key="proj", version_method=method, calver_format=calver_format, cycle_match=cycle_match)

    assert [d.divergence.expected for d in validate.project_calver_format(project)] == expected


@pytest.mark.parametrize(
    ("defaults", "expected"),
    [
        pytest.param({}, [], id="empty"),
        pytest.param({"vote": {"to": "", "cc": [], "bcc": []}}, [], id="empty-block"),
        pytest.param(
            {"vote": {"to": "dev@proj.apache.org", "cc": [], "bcc": ["private@proj.apache.org"]}}, [], id="committee"
        ),
        pytest.param(
            {"vote": {"to": "user-tests@tooling.apache.org", "cc": ["alice@apache.org"], "bcc": []}}, [], id="alpha"
        ),
        pytest.param({"announce": {"to": "", "cc": ["announce@apache.org"], "bcc": []}}, [], id="cc-only"),
        pytest.param(
            {"announce": {"to": "announce@example.org", "cc": [], "bcc": []}},
            ["announce recipients on apache.org or a subdomain"],
            id="external",
        ),
        pytest.param({"vote": "dev@proj.apache.org"}, [SHAPE], id="string-entry"),
        pytest.param({"release": {"to": "dev@proj.apache.org", "cc": [], "bcc": []}}, [SHAPE], id="unknown-action"),
        pytest.param({"vote": {"to": "dev@proj.apache.org", "cc": "a@apache.org", "bcc": []}}, [SHAPE], id="string-cc"),
        pytest.param(["dev@proj.apache.org"], ["a mapping of actions to recipients"], id="list"),
    ],
)
def test_project_recipient_defaults(defaults: Any, expected: list[str]) -> None:
    project = sql.Project(key="proj", release_policy=sql.ReleasePolicy(recipient_defaults=defaults))

    assert [d.divergence.expected for d in validate.project_recipient_defaults(project)] == expected


@pytest.mark.parametrize(
    ("podling", "fields", "settings", "expected"),
    [
        pytest.param(False, {}, {}, [], id="defaults"),
        pytest.param(False, {"security_contact": "security@proj.apache.org"}, {}, [], id="committee-contact"),
        pytest.param(
            False, {"security_contact": "security@example.org"}, {}, ["project_security_contact"], id="contact"
        ),
        pytest.param(False, {}, {"min_hours": 0}, [], id="zero-hours"),
        pytest.param(False, {}, {"min_hours": 24}, ["project_min_hours"], id="short-hours"),
        pytest.param(False, {}, {"rat_excludes_url": "https://example.org/x"}, ["project_fetch_urls"], id="rat-host"),
        pytest.param(False, {}, {"start_vote_template_url": "http://apache.org/t"}, ["project_fetch_urls"], id="http"),
        pytest.param(False, {}, {"github_vote_workflow_path": None}, [], id="null-paths"),
        # Workflow paths and a branch stand on their own now - the repository name is optional
        pytest.param(False, {}, {"github_finish_workflow_path": [".github/workflows/finish.yml"]}, [], id="paths"),
        pytest.param(False, {}, {"github_repository_branch": "main"}, [], id="branch"),
        pytest.param(False, {}, {"github_repository_name": "apache/x"}, ["project_trusted_publishing"], id="slash"),
        pytest.param(
            False,
            {},
            {"github_repository_name": "x", "github_compose_workflow_path": ["release.yml"]},
            ["project_trusted_publishing"],
            id="path-prefix",
        ),
    ],
)
def test_project_settings(podling: bool, fields: dict[str, Any], settings: dict[str, Any], expected: list[str]) -> None:
    committee = sql.Committee(key="proj", is_podling=podling)
    policy = sql.ReleasePolicy(**settings)
    project = sql.Project(
        key="proj", name="Apache Proj", committee_key="proj", committee=committee, release_policy=policy, **fields
    )

    assert [d.validator for d in validate.project(project)] == expected


@pytest.mark.parametrize(
    ("cycle_match", "version", "cycle_key", "count"),
    [
        pytest.param(None, "1.0", "proj-default", 0, id="no-matcher"),
        pytest.param(r"^(\d+)\.\d+$", "2.5", "proj-2", 0, id="matched"),
        pytest.param(r"^(\d+)\.\d+$", "legacy", "proj-default", 0, id="unmatched-default"),
        pytest.param(r"^(\d+)\.\d+$", "2.5", "proj-default", 1, id="stale-cycle"),
    ],
)
def test_release_cycle(cycle_match: str | None, version: str, cycle_key: str, count: int) -> None:
    candidate = release(RELEASE, version, cycle_key=cycle_key)
    candidate.project = sql.Project(key="proj", cycle_match=cycle_match)

    assert len(list(validate.release_cycle(candidate))) == count


def test_release_project_active_without_project() -> None:
    divergences = list(validate.release_project_active(release(DRAFT, project_key="missing")))

    assert [(d.divergence.expected, d.divergence.actual) for d in divergences] == [("'active'", "None")]


@pytest.mark.parametrize(
    ("phase", "fields", "expected"),
    [
        pytest.param(DRAFT, {"vote_started": CREATED, "current_vote_seq": 2}, [], id="failed-vote-draft"),
        pytest.param(
            DRAFT,
            {"vote_mode": EMAIL, "podling_thread_id": "thread"},
            [f"{name} to be unset in release_candidate_draft" for name in ("vote_mode", "podling_thread_id")],
            id="draft-with-vote-state",
        ),
        pytest.param(CANDIDATE, {"vote_started": CREATED, "vote_mode": EMAIL}, [], id="legacy-candidate"),
        pytest.param(
            CANDIDATE, {"vote_started": CREATED}, ["vote_mode to be set in release_candidate"], id="candidate-no-mode"
        ),
        pytest.param(
            CANDIDATE,
            {"vote_started": CREATED, "vote_mode": EMAIL, "voted_revision_number": "00001"},
            ["current_vote_seq to be set with voted_revision_number"],
            id="voted-without-serial",
        ),
        pytest.param(PREVIEW, {"vote_started": CREATED, "vote_mode": EMAIL}, [], id="legacy-preview"),
        pytest.param(
            PREVIEW,
            {},
            [f"{name} to be set in release_preview" for name in ("vote_started", "vote_mode")],
            id="legacy-preview-unstarted",
        ),
        pytest.param(
            PREVIEW,
            {"vote_started": CREATED, "vote_mode": EMAIL, "current_vote_seq": 0},
            ["vote_resolved to be set in release_preview"],
            id="numbered-preview-unresolved",
        ),
        pytest.param(RELEASE, {}, [], id="catalogued-release"),
        pytest.param(
            RELEASE, {"current_vote_seq": 1}, ["vote_started to be set with current_vote_seq"], id="serial-not-started"
        ),
    ],
)
def test_release_vote_state(phase: sql.ReleasePhase, fields: dict[str, Any], expected: list[str]) -> None:
    candidate = release(phase, **fields)

    divergences = [*validate.release_vote_fields(candidate), *validate.release_vote_prerequisites(candidate)]

    assert [d.divergence.expected for d in divergences] == expected
