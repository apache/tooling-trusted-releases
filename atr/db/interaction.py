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
import asyncio
import datetime
import enum
from collections.abc import Sequence
from typing import Final

import packaging.version as version
import sqlalchemy
import sqlalchemy.orm as orm
import sqlmodel

import atr.attestable as attestable
import atr.config as config
import atr.db as db
import atr.jwtoken as jwtoken
import atr.ldap as ldap
import atr.log as log
import atr.models.github as github
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.user as user
import atr.util as util
import atr.web as web

# Infra-provided service account with permission to run ATR workflows
# audit_guidance required actor for ATR distribution workflows; must not be used for project TP workflows.
_GITHUB_TRUSTED_ROLE_NID: Final[int] = 254436773


class ApacheUserMissingError(RuntimeError):
    def __init__(self, message: str, fingerprint: str | None, primary_uid: str | None) -> None:
        super().__init__(message)
        self.fingerprint = fingerprint
        self.primary_uid = primary_uid


class InteractionError(RuntimeError):
    pass


class PublicKeyError(RuntimeError):
    pass


class ReleasePolicyNotFoundError(RuntimeError):
    pass


class TrustedProjectPhase(enum.Enum):
    COMPOSE = "compose"
    VOTE = "vote"
    FINISH = "finish"


async def all_releases(project: sql.Project) -> list[sql.Release]:
    """Get all releases for the project, sorted by version."""
    query = sqlmodel.select(sql.Release).where(sql.Release.project_key == project.key)

    results = []
    async with db.session() as data:
        for result in (await data.execute(query)).all():
            release = result[0]
            results.append(release)

    for release in results:
        release.project = project

    try:
        # This rejects any non PEP 440 versions
        results.sort(key=lambda r: version.Version(r.version), reverse=True)
    except Exception as e:
        # Usually packaging.version.InvalidVersion
        if not isinstance(e, version.InvalidVersion):
            log.warning(f"Error sorting releases: {type(e)}: {e!s}")

        def sort_key(release: sql.Release) -> tuple[tuple[int, int | str], ...]:
            parts = []
            v = release.version.replace("+", ".").replace("-", ".")
            for part in v.split("."):
                try:
                    # Numeric parts: (0, number) to sort before strings
                    parts.append((0, int(part)))
                except ValueError:
                    # String parts: (1, string) to sort after numbers
                    parts.append((1, part))
            return tuple(parts)

        results.sort(key=sort_key, reverse=True)
    return results


async def automated_release_signing_committees(caller_data: db.Session | None = None) -> frozenset[str]:
    """Get all automated release signing committees."""
    committees = []
    async with db.ensure_session(caller_data) as data:
        via = sql.validate_instrumented_attribute
        query = (
            sqlmodel.select(sql.PublicSigningKey)
            .options(orm.selectinload(via(sql.PublicSigningKey.committees)))
            .where(
                sqlalchemy.and_(
                    sqlalchemy.or_(
                        via(sql.PublicSigningKey.primary_declared_uid).like("%Automated Release Signing%"),
                        via(sql.PublicSigningKey.primary_declared_uid).like("%Services RM%"),
                    ),
                    via(sql.PublicSigningKey.primary_declared_uid).like("%private@%.apache.org%"),
                )
            )
        )
        result = await data.execute(query)
        keys = result.scalars().all()

        for key in keys:
            for committee in key.committees:
                committees.append(committee.key)

    # Committees allowed to make automated releases for testing
    committees.append("test")
    committees.append("tooling")

    return frozenset(committees)


async def automated_release_signing_keys(caller_data: db.Session | None = None) -> Sequence[sql.PublicSigningKey]:
    """Get all automated release signing keys."""
    async with db.ensure_session(caller_data) as data:
        via = sql.validate_instrumented_attribute
        query = sqlmodel.select(sql.PublicSigningKey).where(
            sqlalchemy.and_(
                sqlalchemy.or_(
                    via(sql.PublicSigningKey.primary_declared_uid).like("%Automated Release Signing%"),
                    via(sql.PublicSigningKey.primary_declared_uid).like("%Services RM%"),
                ),
                via(sql.PublicSigningKey.primary_declared_uid).like("%private@%.apache.org%"),
            ),
        )
        result = await data.execute(query)
        return result.scalars().all()


async def candidate_drafts(project: sql.Project) -> list[sql.Release]:
    """Get the candidate drafts for the project."""
    return await releases_by_phase(project, sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)


async def candidates(project: sql.Project) -> list[sql.Release]:
    """Get the candidate releases for the project."""
    return await releases_by_phase(project, sql.ReleasePhase.RELEASE_CANDIDATE)


async def checks_for(
    release: sql.Release,
    revision: safe.RevisionNumber | None = None,
    rel_path: str | None = None,
    caller_data: db.Session | None = None,
) -> list[sql.CheckResult]:
    """Get the check results for a release, optionally for a specific revision and/or file path."""
    if revision is None:
        revision = release.safe_latest_revision_number
    file_path_checks = await attestable.load_checks(release.safe_project_key, release.safe_version_key, revision)
    if file_path_checks:
        if rel_path is not None:
            hashes = [
                h for key in ("", str(rel_path)) if key in file_path_checks for h in file_path_checks[key].values()
            ]
        else:
            hashes = [h for inner in file_path_checks.values() for h in inner.values()]
        async with db.ensure_session(caller_data) as data:
            check_results = (
                await data.check_result(inputs_hash_in=hashes, primary_rel_path=rel_path or db.NOT_SET)
                .order_by(
                    sql.validate_instrumented_attribute(sql.CheckResult.checker).asc(),
                    sql.validate_instrumented_attribute(sql.CheckResult.created).desc(),
                )
                .all()
            )
    else:
        check_results = []
    return list(check_results)


async def count_checks_for_revision_by_status(
    status: sql.CheckResultStatus,
    release: sql.Release,
    revision_number: safe.RevisionNumber,
    caller_data: db.Session | None = None,
):
    file_path_checks = await attestable.load_checks(release.safe_project_key, release.safe_version_key, revision_number)
    check_hashes = [h for inner in file_path_checks.values() for h in inner.values()]
    if len(check_hashes) == 0:
        return 0
    async with db.ensure_session(caller_data) as data:
        via = sql.validate_instrumented_attribute
        query = (
            sqlmodel.select(sqlalchemy.func.count())
            .select_from(sql.CheckResult)
            .where(
                via(sql.CheckResult.inputs_hash).in_(check_hashes),
                sql.CheckResult.status == status,
            )
        )
        result = await data.execute(query)
        return result.scalar_one()


async def full_releases(project: sql.Project) -> list[sql.Release]:
    """Get the full releases for the project."""
    return await releases_by_phase(project, sql.ReleasePhase.RELEASE)


async def has_blocker_checks(
    release: sql.Release, revision_number: safe.RevisionNumber, caller_data: db.Session | None = None
) -> bool:
    count = await count_checks_for_revision_by_status(
        sql.CheckResultStatus.BLOCKER, release, revision_number, caller_data
    )
    return count > 0


async def latest_ballot_for_voter(
    release_key: str,
    vote_seq: int,
    voter_asf_uid: str,
    caller_data: db.Session | None = None,
) -> sql.BallotPaper | None:
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.BallotPaper)
            .where(sql.BallotPaper.release_key == release_key)
            .where(sql.BallotPaper.vote_seq == vote_seq)
            .where(sql.BallotPaper.voter_asf_uid == voter_asf_uid)
            .order_by(via(sql.BallotPaper.id).desc())
            .limit(1)
        )
        return (await data.execute(query)).scalar_one_or_none()


async def latest_info(
    project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> tuple[safe.RevisionNumber, str, datetime.datetime] | None:
    """Get the name, editor, and timestamp of the latest revision."""
    release_key = sql.release_key(project_key, version_key)
    async with db.session() as data:
        # TODO: No need to get release here
        # Just use maximum seq from revisions
        release = await data.release(key=str(release_key), _project=True).demand(
            RuntimeError(f"Release {release_key} does not exist")
        )
        if release.latest_revision_number is None:
            return None
        revision = await data.revision(release_key=str(release_key), number=release.latest_revision_number).get()
        if not revision:
            return None
    return revision.safe_number, revision.asfuid, revision.created


async def latest_revision(release: sql.Release, caller_data: db.Session | None = None) -> sql.Revision | None:
    if release.latest_revision_number is None:
        return None
    async with db.ensure_session(caller_data) as data:
        return await data.revision(release_key=release.key, number=release.latest_revision_number).get()


async def previews(project: sql.Project) -> list[sql.Release]:
    """Get the preview releases for the project."""
    return await releases_by_phase(project, sql.ReleasePhase.RELEASE_PREVIEW)


async def release_current_vote_task(release: sql.Release, caller_data: db.Session | None = None) -> sql.Task | None:
    current_vote_seq = getattr(release, "current_vote_seq", None)
    if current_vote_seq is None:
        return await release_latest_vote_task(release, caller_data)
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.Task)
            .where(sql.Task.project_key == release.project_key)
            .where(sql.Task.version_key == release.version)
            .where(sql.Task.task_type == sql.TaskType.VOTE_INITIATE)
            .where(sqlalchemy.func.json_extract(sql.Task.task_args, "$.vote_seq") == current_vote_seq)
            .order_by(via(sql.Task.added).desc())
            .limit(1)
        )
        task = (await data.execute(query)).scalar_one_or_none()
        return task


async def release_latest_vote_task(release: sql.Release, caller_data: db.Session | None = None) -> sql.Task | None:
    """Find the most recent VOTE_INITIATE task for this release."""
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.Task)
            .where(sql.Task.project_key == release.project_key)
            .where(sql.Task.version_key == release.version)
            .where(sql.Task.task_type == sql.TaskType.VOTE_INITIATE)
            .order_by(via(sql.Task.added).desc())
            .limit(1)
        )
        task = (await data.execute(query)).scalar_one_or_none()
        return task


async def release_ready_for_vote(
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision: safe.RevisionNumber,
    data: db.Session,
    allowed_vote_modes: frozenset[sql.VoteMode],
) -> tuple[sql.Release, sql.Committee] | str:
    release = await session.release(
        project_key,
        version_key,
        data=data,
        with_project=True,
        with_committee=True,
        with_project_release_policy=True,
    )

    selected_revision_number = release.latest_revision_number
    if selected_revision_number is None:
        return "No revision found for this release"

    if release.safe_latest_revision_number != revision:
        return "This revision does not match the revision you are voting on"

    committee = release.committee
    if committee is None:
        return "The committee for this release was not found"

    if release.effective_vote_mode not in allowed_vote_modes:
        return "This release's vote mode does not allow that action"

    if await has_blocker_checks(release, revision, caller_data=data):
        return "This release candidate draft has blockers. Please fix the blockers before starting a vote."

    if not (user.is_committee_member(committee, session.uid) or session.is_admin):
        return "You must be on the PMC of this project to start a vote"

    has_files = await util.has_files(release)
    if not has_files:
        return "This release candidate draft has no files yet. Please add some files before starting a vote."

    return release, committee


async def releases_by_phase(project: sql.Project, phase: sql.ReleasePhase) -> list[sql.Release]:
    """Get the releases for the project by phase."""

    query = (
        sqlmodel.select(sql.Release)
        .where(
            sql.Release.project_key == project.key,
            sql.Release.phase == phase,
        )
        .order_by(sql.validate_instrumented_attribute(sql.Release.created).desc())
    )

    results = []
    async with db.session() as data:
        for result in (await data.execute(query)).all():
            release = result[0]
            results.append(release)

    for release in results:
        # Don't need to eager load and lose it when the session closes
        release.project = project
    return results


async def releases_in_progress(project: sql.Project) -> list[sql.Release]:
    """Get the releases in progress for the project."""
    drafts = await candidate_drafts(project)
    cands = await candidates(project)
    prevs = await previews(project)
    return drafts + cands + prevs


def task_mid_get(latest_vote_task: sql.Task) -> str | None:
    # if util.is_dev_environment():
    #     import atr.db.interaction as interaction

    #     return interaction.TEST_MID
    # # TODO: Improve this

    result = latest_vote_task.result
    if not isinstance(result, results.VoteInitiate):
        return None
    return result.mid


def task_recipient_get(latest_vote_task: sql.Task) -> str | None:
    result = latest_vote_task.result
    if not isinstance(result, results.VoteInitiate):
        return None
    if not result.email_to:
        return None
    return result.email_to


async def tasks_ongoing(
    project_key: safe.ProjectKey, version_key: safe.VersionKey, revision_number: safe.RevisionNumber | None = None
) -> int:
    tasks = sqlmodel.select(sqlalchemy.func.count()).select_from(sql.Task)
    async with db.session() as data:
        query = tasks.where(
            sql.Task.project_key == str(project_key),
            sql.Task.version_key == str(version_key),
            sql.Task.revision_number
            == (sql.RELEASE_LATEST_REVISION_NUMBER if (revision_number is None) else str(revision_number)),
            sql.validate_instrumented_attribute(sql.Task.status).in_([sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE]),
        )
        result = await data.execute(query)
        return result.scalar_one()


async def tasks_ongoing_revision(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber | None = None,
) -> tuple[int, str | None]:
    via = sql.validate_instrumented_attribute
    subquery = (
        sqlalchemy.select(via(sql.Revision.number))
        .where(
            via(sql.Revision.release_key) == sql.release_key(str(project_key), str(version_key)),
        )
        .order_by(via(sql.Revision.seq).desc())
        .limit(1)
        .scalar_subquery()
        .label("latest_revision")
    )

    query = (
        sqlmodel.select(
            sqlalchemy.func.count().label("task_count"),
            subquery,
        )
        .select_from(sql.Task)
        .where(
            sql.Task.project_key == str(project_key),
            sql.Task.version_key == str(version_key),
            sql.Task.revision_number == (subquery if (revision_number is None) else str(revision_number)),
            sql.validate_instrumented_attribute(sql.Task.status).in_(
                [sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE],
            ),
        )
    )

    async with db.session() as session:
        task_count, latest_revision = (await session.execute(query)).one()
        return task_count, latest_revision


async def trusted_jwt(
    publisher: str, jwt: str, phase: TrustedProjectPhase
) -> tuple[github.TrustedPublisherPayload, str, sql.Project]:
    payload, asf_uid = await validate_trusted_jwt(publisher, jwt)
    # JWT could be for an ASF user or the trusted role, but we need a user here.
    if asf_uid is None:
        raise InteractionError("ASF user not found")
    project = await _trusted_project(payload.repository, payload.workflow_ref, phase)
    return payload, asf_uid, project


async def trusted_jwt_for_dist(
    publisher: str,
    jwt: str,
    asf_uid: str,
    phase: TrustedProjectPhase,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> tuple[github.TrustedPublisherPayload, str, sql.Project, sql.Release]:
    payload, asf_uid_from_jwt = await validate_trusted_jwt(publisher, jwt)
    if asf_uid_from_jwt is not None:
        raise InteractionError("Must use Trusted Publishing when specifying ASF UID")
    # payload, asf_uid, project = await trusted_jwt(publisher, jwt, phase)
    async with db.session() as db_data:
        project = await db_data.project(key=str(project_key), _committee=True).demand(
            InteractionError(f"Project {project_key} does not exist")
        )
        release = await db_data.release(project_key=str(project_key), version=str(version_key)).get()
        if not release:
            raise InteractionError(f"Release {version_key} does not exist in project {project_key}")
        if (phase == TrustedProjectPhase.COMPOSE) and (release.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT):
            raise InteractionError(f"Release {version_key} is not in compose phase")
        if (phase == TrustedProjectPhase.VOTE) and (release.phase != sql.ReleasePhase.RELEASE_CANDIDATE):
            raise InteractionError(f"Release {version_key} is not in vote phase")
        if (phase == TrustedProjectPhase.FINISH) and (release.phase != sql.ReleasePhase.RELEASE_PREVIEW):
            raise InteractionError(f"Release {version_key} is not in finish phase")

    return payload, asf_uid, project, release


async def unfinished_releases(asfuid: str) -> list[tuple[str, str, list[sql.Release]]]:
    releases: list[tuple[str, str, list[sql.Release]]] = []
    async with db.session() as data:
        user_projects = await user.projects(asfuid)
        user_projects.sort(key=lambda p: p.display_name)

        active_phases = [
            sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
            sql.ReleasePhase.RELEASE_CANDIDATE,
            sql.ReleasePhase.RELEASE_PREVIEW,
        ]
        for project in user_projects:
            stmt = (
                sqlmodel.select(sql.Release)
                .where(
                    sql.Release.project_key == project.key,
                    sql.validate_instrumented_attribute(sql.Release.phase).in_(active_phases),
                )
                .options(db.select_in_load(sql.Release.project))
                .order_by(sql.validate_instrumented_attribute(sql.Release.created).desc())
            )
            result = await data.execute(stmt)
            active_releases = list(result.scalars().all())
            if active_releases:
                active_releases.sort(key=lambda r: r.created, reverse=True)
                releases.append((project.short_display_name, project.key, active_releases))

    return releases


async def user_committees(asf_uid: str) -> list[tuple[str, str]]:
    results = []
    for committee in await user_committees_participant(asf_uid):
        results.append((committee.key, committee.name))
    return results


# This function cannot go in user.py because it causes a circular import
async def user_committees_committer(asf_uid: str, caller_data: db.Session | None = None) -> Sequence[sql.Committee]:
    async with db.ensure_session(caller_data) as data:
        return await data.committee(has_committer=asf_uid).all()


# This function cannot go in user.py because it causes a circular import
async def user_committees_member(asf_uid: str, caller_data: db.Session | None = None) -> Sequence[sql.Committee]:
    async with db.ensure_session(caller_data) as data:
        return await data.committee(has_member=asf_uid).all()


# This function cannot go in user.py because it causes a circular import
async def user_committees_participant(asf_uid: str, caller_data: db.Session | None = None) -> Sequence[sql.Committee]:
    async with db.ensure_session(caller_data) as data:
        return await data.committee(has_participant=asf_uid).all()


async def user_projects(asf_uid: str, caller_data: db.Session | None = None) -> list[tuple[str, str]]:
    projects = await user.projects(asf_uid)
    return [(p.key, p.display_name) for p in projects]


async def validate_trusted_jwt(publisher: str, jwt: str) -> tuple[github.TrustedPublisherPayload, str | None]:
    if publisher != "github":
        raise InteractionError(f"Publisher {publisher} not supported")
    payload = await jwtoken.verify_github_oidc(jwt)
    if payload.actor_id != _GITHUB_TRUSTED_ROLE_NID:
        asf_uid = await ldap.github_to_apache(payload.actor_id)
    else:
        asf_uid = None
    return payload, asf_uid


def vote_duration_bypass() -> bool:
    return not config.is_production_mode()


def vote_end_get(latest_vote_task: sql.Task | None) -> datetime.datetime | None:
    if latest_vote_task is None:
        return None
    result = latest_vote_task.result
    if not isinstance(result, results.VoteInitiate):
        return None
    try:
        naive = datetime.datetime.strptime(result.vote_end, "%Y-%m-%d %H:%M:%S UTC")
        return naive.replace(tzinfo=datetime.UTC)
    except (ValueError, AttributeError):
        return None


def vote_pass_fail_allowed(latest_vote_task: sql.Task | None) -> bool:
    vote_end = vote_end_get(latest_vote_task)
    if vote_end is None:
        return False
    return datetime.datetime.now(datetime.UTC) >= vote_end


async def wait_for_task(
    task: sql.Task,
    caller_data: db.Session | None = None,
    desired_status: sql.TaskStatus = sql.TaskStatus.COMPLETED,
    timeout_s: int = 10,
) -> bool:
    # We must wait until the sbom_task is complete before we can queue checks
    # Maximum wait time is 60 * 100ms = 6000ms
    log.info(f"Waiting for task {task.id} to complete")
    async with db.ensure_session(caller_data) as data:
        t = await data.task(id=task.id).get()
        if t is None:
            return False
        for _attempt in range(timeout_s * 10):
            await data.refresh(t)
            if t.status == sql.TaskStatus.FAILED:
                raise InteractionError(f"Task {task.id} failed with error {t.error}")
            if t.status == desired_status:
                return True
            # Wait 100ms before checking again
            await asyncio.sleep(0.1)
    return False


async def _trusted_project(repository: str, workflow_ref: str, phase: TrustedProjectPhase) -> sql.Project:
    # Debugging
    log.info(f"GitHub OIDC JWT payload: {repository} {workflow_ref}")
    repository_name, workflow_path = _trusted_project_checks(repository, workflow_ref)

    rpnf_error = ReleasePolicyNotFoundError(
        f"Release policy for repository {repository_name} and {phase.value} workflow path {workflow_path} not found"
    )
    # TODO: If a policy is reused between projects, we can't get the project
    async with db.session() as db_data:
        match phase:
            case TrustedProjectPhase.COMPOSE:
                # Searches in github_*compose*_workflow_path
                policy = await db_data.release_policy(
                    github_repository_name=repository_name,
                    github_compose_workflow_path_has=workflow_path,
                ).demand(rpnf_error)
            case TrustedProjectPhase.VOTE:
                # Searches in github_*vote*_workflow_path
                policy = await db_data.release_policy(
                    github_repository_name=repository_name,
                    github_vote_workflow_path_has=workflow_path,
                ).demand(rpnf_error)
            case TrustedProjectPhase.FINISH:
                # Searches in github_*finish*_workflow_path
                policy = await db_data.release_policy(
                    github_repository_name=repository_name,
                    github_finish_workflow_path_has=workflow_path,
                ).demand(rpnf_error)
        project = await db_data.project(release_policy_id=policy.id).demand(
            InteractionError(f"Project for release policy {policy.id} not found")
        )
    if project.committee is None:
        raise InteractionError(f"Project {project.key} has no committee")
    github_automated_release_committees = await automated_release_signing_committees()
    if project.committee.key not in github_automated_release_committees:
        raise InteractionError(f"Project {project.key} is not in a committee that can make automated releases")
    return project


def _trusted_project_checks(repository: str, workflow_ref: str) -> tuple[str, str]:
    if not repository.startswith("apache/"):
        raise InteractionError("Repository must start with 'apache/'")
    repository_name = repository.removeprefix("apache/")
    if not workflow_ref.startswith(repository + "/"):
        raise InteractionError(f"Workflow ref must start with repository, got {workflow_ref}")
    workflow_path_at = workflow_ref.removeprefix(repository + "/")
    if "@" not in workflow_path_at:
        raise InteractionError(f"Workflow path must contain '@', got {workflow_path_at}")
    workflow_path = workflow_path_at.rsplit("@", 1)[0]
    if not workflow_path.startswith(".github/workflows/"):
        raise InteractionError(f"Workflow path must start with '.github/workflows/', got {workflow_path}")
    return repository_name, workflow_path
