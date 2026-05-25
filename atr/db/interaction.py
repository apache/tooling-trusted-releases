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
import dataclasses
import datetime
import enum
from collections.abc import Sequence
from typing import Final

import asfquart.base as base
import packaging.version as version
import pydantic
import sqlalchemy
import sqlalchemy.orm as orm
import sqlmodel

import atr.attestable as attestable
import atr.config as config
import atr.cycles as cycles
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

PENDING_QUARANTINE_VOTE_BLOCK_MESSAGE: Final[str] = (
    "Archive validation is still in progress. Please wait for it to complete before starting a vote."
)

# Infra-provided service account with permission to run ATR workflows
# audit_guidance required actor for ATR distribution workflows; must not be used for project TP workflows.
_GITHUB_TRUSTED_ROLE_NID: Final[int] = 254436773
_NO_EXPECTED_VOTE_ROUND: Final[object] = object()


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


@dataclasses.dataclass
class TrustedVoteSummary:
    binding_votes_yes: int = 0
    binding_votes_no: int = 0
    binding_votes_abstain: int = 0
    non_binding_votes_yes: int = 0
    non_binding_votes_no: int = 0
    non_binding_votes_abstain: int = 0

    @property
    def binding_votes(self) -> int:
        return self.binding_votes_yes + self.binding_votes_no + self.binding_votes_abstain

    @property
    def non_binding_votes(self) -> int:
        return self.non_binding_votes_yes + self.non_binding_votes_no + self.non_binding_votes_abstain


@dataclasses.dataclass(frozen=True, kw_only=True)
class TrustedBallotDetail:
    cast_at: datetime.datetime
    choice: sql.VoteChoice
    comment: str
    is_binding: bool
    is_carried: bool = False
    receipt_message_id: str
    revision_number_at_cast: str
    status_label: str
    voter_asf_uid: str
    voter_fullname: str
    vote_round: int | None


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


async def ballot_receipt_message_ids(
    release_key: str,
    vote_seq: int,
    caller_data: db.Session | None = None,
) -> set[str]:
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.BallotPaper.receipt_message_id)
            .where(sql.BallotPaper.release_key == release_key)
            .where(sql.BallotPaper.vote_seq == vote_seq)
            .where(via(sql.BallotPaper.receipt_message_id).is_not(None))
            .where(sql.BallotPaper.receipt_message_id != "")
        )
        result = await data.execute(query)
        return {message_id for message_id in result.scalars().all() if message_id}


async def ballots_for_resolution(
    release_key: str,
    vote_seq: int,
    caller_data: db.Session | None = None,
) -> list[sql.BallotPaper]:
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        row_number = sqlalchemy.func.row_number().over(
            partition_by=(via(sql.BallotPaper.vote_round), via(sql.BallotPaper.voter_asf_uid)),
            order_by=via(sql.BallotPaper.id).desc(),
        )
        latest_ids = (
            sqlmodel.select(via(sql.BallotPaper.id).label("ballot_id"), row_number.label("row_number"))
            .where(sql.BallotPaper.release_key == release_key)
            .where(sql.BallotPaper.vote_seq == vote_seq)
            .subquery()
        )
        query = (
            sqlmodel.select(sql.BallotPaper)
            .join(latest_ids, via(sql.BallotPaper.id) == latest_ids.c.ballot_id)
            .where(latest_ids.c.row_number == 1)
            .order_by(via(sql.BallotPaper.vote_round).asc(), via(sql.BallotPaper.voter_asf_uid).asc())
        )
        result = await data.execute(query)
        return list(result.scalars().all())


async def candidate_drafts(project: sql.Project) -> list[sql.Release]:
    """Get the candidate drafts for the project."""
    return await releases_by_phase(project, sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT)


async def candidates(project: sql.Project) -> list[sql.Release]:
    """Get the candidate releases for the project."""
    return await releases_by_phase(project, sql.ReleasePhase.RELEASE_CANDIDATE)


async def check_results_for_revision(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    *,
    checker: str | None = None,
    include_legacy_revision_results: bool = False,
    rel_path: str | None = None,
    caller_data: db.Session | None = None,
) -> list[sql.CheckResult]:
    file_path_checks = await attestable.load_checks(project_key, version_key, revision_number)
    check_hashes: list[str] = []
    if file_path_checks:
        if rel_path is not None:
            path_keys = ("", str(rel_path))
        else:
            path_keys = tuple(file_path_checks.keys())
        for path_key in path_keys:
            for checker_key, check_hash in file_path_checks.get(path_key, {}).items():
                if (checker is None) or (checker_key == checker):
                    check_hashes.append(check_hash)

    async with db.ensure_session(caller_data) as data:
        checker_arg = checker if checker is not None else db.NOT_SET
        if check_hashes:
            query = data.check_result(
                inputs_hash_in=check_hashes,
                checker=checker_arg,
                primary_rel_path=rel_path or db.NOT_SET,
            )
        elif include_legacy_revision_results:
            query = data.check_result(
                release_key=sql.release_key(str(project_key), str(version_key)),
                revision_number=str(revision_number),
                checker=checker_arg,
                primary_rel_path=rel_path or db.NOT_SET,
            )
        else:
            return []
        return list(
            await query.order_by(
                sql.validate_instrumented_attribute(sql.CheckResult.checker).asc(),
                sql.validate_instrumented_attribute(sql.CheckResult.created).desc(),
            ).all()
        )


async def checks_for(
    release: sql.Release,
    revision: safe.RevisionNumber | None = None,
    rel_path: str | None = None,
    caller_data: db.Session | None = None,
) -> list[sql.CheckResult]:
    """Get the check results for a release, optionally for a specific revision and/or file path."""
    if revision is None:
        revision = release.safe_latest_revision_number
    return await check_results_for_revision(
        release.safe_project_key,
        release.safe_version_key,
        revision,
        rel_path=rel_path,
        caller_data=caller_data,
    )


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


async def effective_latest_ballot_for_voter(
    release: sql.Release,
    vote_seq: int,
    voter_asf_uid: str,
    caller_data: db.Session | None = None,
) -> sql.BallotPaper | None:
    if caller_data is None:
        current_ballot = await latest_ballot_for_voter(release.key, vote_seq, voter_asf_uid)
    else:
        current_ballot = await latest_ballot_for_voter(release.key, vote_seq, voter_asf_uid, caller_data)
    if current_ballot is not None:
        return current_ballot
    if (release.committee is None) or (not release.committee.is_podling) or (trusted_vote_round(release) != 2):
        return None
    r1_seq = await previous_round_one_vote_seq(release.key, vote_seq, caller_data)
    if r1_seq is None:
        return None
    if caller_data is None:
        r1_ballot = await latest_ballot_for_voter(release.key, r1_seq, voter_asf_uid)
    else:
        r1_ballot = await latest_ballot_for_voter(release.key, r1_seq, voter_asf_uid, caller_data)
    if r1_ballot is None:
        return None
    is_binding, _binding_committee = await user.is_binding_for_release(
        release.committee,
        voter_asf_uid,
        2,
        caller_data=caller_data,
    )
    if not is_binding:
        return None
    return r1_ballot


async def effective_trusted_ballots(
    release: sql.Release,
    vote_seq: int,
    caller_data: db.Session | None = None,
) -> list[sql.BallotPaper]:
    if caller_data is None:
        current_ballots = await ballots_for_resolution(release.key, vote_seq)
    else:
        current_ballots = await ballots_for_resolution(release.key, vote_seq, caller_data)
    if (release.committee is None) or (not release.committee.is_podling) or (trusted_vote_round(release) != 2):
        return current_ballots
    r1_seq = await previous_round_one_vote_seq(release.key, vote_seq, caller_data)
    if r1_seq is None:
        return current_ballots
    if caller_data is None:
        r1_ballots = await ballots_for_resolution(release.key, r1_seq)
    else:
        r1_ballots = await ballots_for_resolution(release.key, r1_seq, caller_data)
    current_voted_uids = {b.voter_asf_uid for b in current_ballots}
    carried: list[sql.BallotPaper] = []
    for ballot in r1_ballots:
        if ballot.voter_asf_uid in current_voted_uids:
            continue
        is_binding, _binding_committee = await user.is_binding_for_release(
            release.committee,
            ballot.voter_asf_uid,
            2,
            caller_data=caller_data,
        )
        if is_binding:
            carried.append(ballot)
    return current_ballots + carried


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


async def pending_quarantine_count(release_key: str, caller_data: db.Session | None = None) -> int:
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sqlalchemy.func.count())
            .select_from(sql.Quarantined)
            .where(sql.Quarantined.release_key == release_key)
            .where(via(sql.Quarantined.status).in_([sql.QuarantineStatus.STAGING, sql.QuarantineStatus.PENDING]))
        )
        return (await data.execute(query)).scalar_one()


async def previews(project: sql.Project) -> list[sql.Release]:
    """Get the preview releases for the project."""
    return await releases_by_phase(project, sql.ReleasePhase.RELEASE_PREVIEW)


async def previous_round_one_recipient(
    release: sql.Release,
    current_vote_seq: int,
    caller_data: db.Session | None = None,
) -> str | None:
    if (release.committee is None) or (not release.committee.is_podling):
        return None
    if trusted_vote_round(release) != 2:
        return None
    r1_seq = await previous_round_one_vote_seq(release.key, current_vote_seq, caller_data)
    if r1_seq is None:
        return None
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.Task)
            .where(sql.Task.project_key == release.project_key)
            .where(sql.Task.version_key == release.version)
            .where(sql.Task.task_type == sql.TaskType.VOTE_INITIATE)
            .where(sqlalchemy.func.json_extract(sql.Task.task_args, "$.vote_seq") == r1_seq)
            .order_by(via(sql.Task.added).desc())
            .limit(1)
        )
        task = (await data.execute(query)).scalar_one_or_none()
    if task is None:
        return None
    email_to = task.task_args.get("email_to")
    if isinstance(email_to, str) and email_to:
        return email_to
    return None


async def previous_round_one_vote_seq(
    release_key: str,
    current_vote_seq: int,
    caller_data: db.Session | None = None,
) -> int | None:
    # This works because there must be a round one vote to get to round two
    # We get the latest round one before the current round for the same release
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sqlalchemy.func.max(sql.BallotPaper.vote_seq))
            .where(sql.BallotPaper.release_key == release_key)
            .where(sql.BallotPaper.vote_round == 1)
            .where(sql.BallotPaper.vote_seq < current_vote_seq)
        )
        return (await data.execute(query)).scalar_one_or_none()


async def prior_release_for_archive(project: sql.Project, version: str) -> sql.Release | None:
    """Find the prior release in the same cycle still eligible for archival."""
    via = sql.validate_instrumented_attribute
    query = sqlmodel.select(sql.Release).where(
        sql.Release.project_key == project.key,
        sql.Release.phase == sql.ReleasePhase.RELEASE,
        via(sql.Release.archived).is_(None),
    )
    async with db.session() as data:
        result = await data.execute(query)
        candidates = list(result.scalars().all())
    return cycles.prior_release_in_cycle(project, version, candidates)


async def release_completed_svn_publish_task_for_revision(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    caller_data: db.Session | None = None,
) -> sql.Task | None:
    """Return the most recently completed usable SVN_PUBLISH task for the revision."""
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.Task)
            .where(sql.Task.project_key == str(project_key))
            .where(sql.Task.version_key == str(version_key))
            .where(sql.Task.revision_number == str(revision_number))
            .where(sql.Task.task_type == sql.TaskType.SVN_PUBLISH)
            .where(sql.Task.status == sql.TaskStatus.COMPLETED)
            .order_by(via(sql.Task.added).desc())
        )
        for task in (await data.execute(query)).scalars().all():
            if _svn_publish_result(task) is not None:
                return task
        return None


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


async def release_in_flight_svn_publish_task(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    target_url: str | None = None,
    caller_data: db.Session | None = None,
) -> sql.Task | None:
    """Return the most recent queued or active SVN_PUBLISH task."""
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.Task)
            .where(sql.Task.project_key == str(project_key))
            .where(sql.Task.version_key == str(version_key))
            .where(sql.Task.revision_number == str(revision_number))
            .where(sql.Task.task_type == sql.TaskType.SVN_PUBLISH)
            .where(via(sql.Task.status).in_([sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE]))
            .order_by(via(sql.Task.added).desc())
            .limit(1)
        )
        if target_url is not None:
            query = query.where(sqlalchemy.func.json_extract(sql.Task.task_args, "$.target_url") == target_url)
        return (await data.execute(query)).scalar_one_or_none()


async def release_latest_failed_svn_publish_task(
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_number: safe.RevisionNumber,
    target_url: str | None = None,
    caller_data: db.Session | None = None,
) -> sql.Task | None:
    """Return the most recently failed SVN_PUBLISH task."""
    via = sql.validate_instrumented_attribute
    async with db.ensure_session(caller_data) as data:
        query = (
            sqlmodel.select(sql.Task)
            .where(sql.Task.project_key == str(project_key))
            .where(sql.Task.version_key == str(version_key))
            .where(sql.Task.revision_number == str(revision_number))
            .where(sql.Task.task_type == sql.TaskType.SVN_PUBLISH)
            .where(sql.Task.status == sql.TaskStatus.FAILED)
            .order_by(via(sql.Task.added).desc())
            .limit(1)
        )
        if target_url is not None:
            query = query.where(sqlalchemy.func.json_extract(sql.Task.task_args, "$.target_url") == target_url)
        return (await data.execute(query)).scalar_one_or_none()


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


async def release_ready_to_start_vote(
    session: web.Committer,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
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

    latest_revision_number = release.latest_revision_number
    if latest_revision_number is None:
        return "No revision found for this release"

    committee = release.committee
    if committee is None:
        return "The committee for this release was not found"

    if release.effective_vote_mode not in allowed_vote_modes:
        return "This release's vote mode does not allow that action"

    if await has_blocker_checks(release, release.safe_latest_revision_number, caller_data=data):
        return "This release candidate draft has blockers. Please fix the blockers before starting a vote."

    if await pending_quarantine_count(release.key, caller_data=data) > 0:
        return PENDING_QUARANTINE_VOTE_BLOCK_MESSAGE

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


async def trusted_ballot_details(
    release: sql.Release,
    vote_seq: int,
    expected_vote_round: int | None,
    caller_data: db.Session | None = None,
) -> tuple[list[TrustedBallotDetail], TrustedVoteSummary]:
    if caller_data is None:
        ballots = await ballots_for_resolution(release.key, vote_seq)
    else:
        ballots = await ballots_for_resolution(release.key, vote_seq, caller_data)
    return await trusted_ballot_details_from_ballots(
        release,
        ballots,
        expected_vote_round,
        caller_data=caller_data,
    )


async def trusted_ballot_details_from_ballots(
    release: sql.Release,
    ballots: Sequence[sql.BallotPaper],
    expected_vote_round: int | None,
    caller_data: db.Session | None = None,
) -> tuple[list[TrustedBallotDetail], TrustedVoteSummary]:
    return await _trusted_ballot_details_from_ballots(
        release,
        ballots,
        expected_vote_round=expected_vote_round,
        caller_data=caller_data,
    )


async def trusted_ballot_summary(
    release: sql.Release,
    ballots: Sequence[sql.BallotPaper],
    caller_data: db.Session | None = None,
) -> TrustedVoteSummary:
    _details, summary = await _trusted_ballot_details_from_ballots(
        release,
        ballots,
        caller_data=caller_data,
    )
    return summary


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
    project, release = await _trusted_dist_lookup(asf_uid, phase, project_key, version_key)
    return payload, asf_uid, project, release


async def trusted_project_for_payload(
    payload: github.TrustedPublisherPayload,
    asf_uid: str | None,
    phase: TrustedProjectPhase,
) -> tuple[str, sql.Project]:
    """Look up the project for an already-verified Trusted Publisher payload."""
    if asf_uid is None:
        raise InteractionError("ASF user not found")
    project = await _trusted_project(payload.repository, payload.workflow_ref, phase)
    return asf_uid, project


async def trusted_release_for_payload(
    payload_asf_uid: str | None,
    asserted_asf_uid: str,
    phase: TrustedProjectPhase,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> tuple[sql.Project, sql.Release]:
    """Look up project+release for an already-verified body_oidc workflow token."""
    if payload_asf_uid is not None:
        raise InteractionError("Must use Trusted Publishing when specifying ASF UID")
    return await _trusted_dist_lookup(asserted_asf_uid, phase, project_key, version_key)


def trusted_vote_round(release: sql.Release) -> int | None:
    if (release.committee is not None) and release.committee.is_podling:
        return 1 if (release.podling_thread_id is None) else 2
    return None


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
        try:
            asf_uid = await ldap.github_to_apache(payload.actor_id)
        except ldap.LookupError as e:
            message = (
                f"GitHub account {payload.actor} (ID {payload.actor_id}) is not yet linked to an ASF user "
                "in gitbox.apache.org/boxer"
            )
            raise base.ASFQuartException(message, errorcode=403) from e
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


def _svn_publish_result(task: sql.Task) -> results.SvnPublish | None:
    result = task.result
    if isinstance(result, results.SvnPublish):
        return result
    if isinstance(result, dict):
        try:
            parsed = results.ResultsAdapter.validate_python(result)
        except pydantic.ValidationError:
            return None
        if isinstance(parsed, results.SvnPublish):
            return parsed
    return None


async def _trusted_ballot_details_from_ballots(
    release: sql.Release,
    ballots: Sequence[sql.BallotPaper],
    *,
    expected_vote_round: int | None | object = _NO_EXPECTED_VOTE_ROUND,
    caller_data: db.Session | None = None,
) -> tuple[list[TrustedBallotDetail], TrustedVoteSummary]:
    if release.committee is None:
        raise ValueError("Release has no committee")
    active_round = trusted_vote_round(release)
    details: list[TrustedBallotDetail] = []
    summary = TrustedVoteSummary()
    for ballot in ballots:
        is_carried = (ballot.vote_round == 1) and (active_round == 2)
        round_mismatch = (
            (expected_vote_round is not _NO_EXPECTED_VOTE_ROUND)
            and (ballot.vote_round != expected_vote_round)
            and (not is_carried)
        )
        if round_mismatch:
            raise ValueError("Trusted ballot vote round does not match the active vote round")
        if caller_data is None:
            is_binding, _binding_committee = await user.is_binding_for_release(
                release.committee,
                ballot.voter_asf_uid,
                active_round,
            )
        else:
            is_binding, _binding_committee = await user.is_binding_for_release(
                release.committee,
                ballot.voter_asf_uid,
                active_round,
                caller_data=caller_data,
            )
        _trusted_summary_add(summary, ballot.choice, is_binding)
        binding_label, non_binding_label = user.binding_terminology(active_round)
        details.append(
            TrustedBallotDetail(
                cast_at=ballot.created,
                choice=ballot.choice,
                comment=ballot.comment,
                is_binding=is_binding,
                is_carried=is_carried,
                receipt_message_id=ballot.receipt_message_id,
                revision_number_at_cast=ballot.revision_number_at_cast,
                status_label=binding_label if is_binding else non_binding_label,
                voter_asf_uid=ballot.voter_asf_uid,
                voter_fullname=ballot.voter_fullname,
                vote_round=ballot.vote_round,
            )
        )
    return details, summary


async def _trusted_dist_lookup(
    asf_uid: str,
    phase: TrustedProjectPhase,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> tuple[sql.Project, sql.Release]:
    """Shared project + release + phase lookup for dist-style trusted calls."""
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
    return project, release


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


def _trusted_summary_add(summary: TrustedVoteSummary, choice: sql.VoteChoice, is_binding: bool) -> None:
    match choice:
        case sql.VoteChoice.YES:
            if is_binding:
                summary.binding_votes_yes += 1
            else:
                summary.non_binding_votes_yes += 1
        case sql.VoteChoice.ABSTAIN:
            if is_binding:
                summary.binding_votes_abstain += 1
            else:
                summary.non_binding_votes_abstain += 1
        case sql.VoteChoice.NO:
            if is_binding:
                summary.binding_votes_no += 1
            else:
                summary.non_binding_votes_no += 1
