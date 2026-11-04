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
import itertools
import os
import re
from collections.abc import AsyncGenerator, Callable, Generator, Iterable, Sequence
from typing import Final, NamedTuple, TypeVar

import aiofiles.os
import sqlalchemy
import sqlalchemy.orm as orm
import sqlmodel

import atr.cycles as cycles
import atr.db as db
import atr.models.calver as calver
import atr.models.results as results
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.paths as paths
import atr.util as util


class Divergence(NamedTuple):
    expected: str
    actual: str


class AnnotatedDivergence(NamedTuple):
    components: list[str]
    validator: str
    source: str
    divergence: Divergence


class Consistency(NamedTuple):
    db_only: list[str]
    fs_only: list[str]
    paired: list[str]


type Divergences = Generator[Divergence]
type AnnotatedDivergences = Generator[AnnotatedDivergence]
type AsyncAnnotatedDivergences = AsyncGenerator[AnnotatedDivergence]
type CommitteeDivergences = Callable[[sql.Committee], Divergences]
type CommitteeAnnotatedDivergences = Callable[[sql.Committee], AnnotatedDivergences]
type ProjectDivergences = Callable[[sql.Project], Divergences]
type ProjectAnnotatedDivergences = Callable[[sql.Project], AnnotatedDivergences]
type ReleaseDivergences = Callable[[sql.Release], Divergences]
type ReleaseAnnotatedDivergences = Callable[[sql.Release], AnnotatedDivergences]

T = TypeVar("T")

# Same ASF UID character set as principal.py
_ASF_UID_PATTERN: Final = re.compile(r"^[-_a-z0-9]+$")

_FETCH_URLS: Final = (
    "policy_announce_release_template_url",
    "policy_finish_vote_template_url",
    "policy_rat_excludes_url",
    "policy_start_vote_template_url",
    "policy_vote_comment_template_url",
)

_IGNORE_GLOBS: Final = ("release_glob", "checker_glob", "primary_rel_path_glob", "member_rel_path_glob", "message_glob")

_PHASE_VOTE_FIELDS: Final[dict[sql.ReleasePhase, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT: (
        (),
        ("vote_mode", "vote_resolved", "podling_thread_id", "voted_revision_number", "vote_thread_url"),
    ),
    sql.ReleasePhase.RELEASE_CANDIDATE: (("vote_started", "vote_mode"), ("vote_resolved", "vote_thread_url")),
    sql.ReleasePhase.RELEASE_PREVIEW: (("vote_started", "vote_resolved", "vote_mode"), ()),
}

_QUARANTINE_STAGING_MINUTES: Final = 5

_RECIPIENT_ACTIONS: Final = frozenset(action.value for action in sql.RecipientAction)

_SHA3_256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")

_VOTE_FIELD_PREREQUISITES: Final = {
    "current_vote_seq": ("vote_started",),
    "voted_revision_number": ("current_vote_seq", "vote_started", "vote_mode"),
}

_VOTING_PHASES: Final = frozenset({sql.ReleasePhase.RELEASE_CANDIDATE, sql.ReleasePhase.RELEASE_PREVIEW})

if True:

    def committee_components(
        *components: str,
    ) -> Callable[[CommitteeDivergences], CommitteeAnnotatedDivergences]:
        """Wrap a Committee divergence generator to yield annotated divergences."""

        def wrap(original: CommitteeDivergences) -> CommitteeAnnotatedDivergences:
            def replacement(c: sql.Committee) -> AnnotatedDivergences:
                yield from divergences_with_annotations(
                    components,
                    original.__name__,
                    c.key,
                    original(c),
                )

            return replacement

        return wrap

    def project_components(
        *components: str,
    ) -> Callable[[ProjectDivergences], ProjectAnnotatedDivergences]:
        """Wrap a Project divergence generator to yield annotated divergences."""

        def wrap(original: ProjectDivergences) -> ProjectAnnotatedDivergences:
            def replacement(p: sql.Project) -> AnnotatedDivergences:
                yield from divergences_with_annotations(
                    components,
                    original.__name__,
                    str(p.key),
                    original(p),
                )

            return replacement

        return wrap


async def allocation_counters(data: db.Session) -> AsyncAnnotatedDivergences:
    statements = (("RevisionCounter", _revision_allocations()), ("VoteCounter", _vote_allocations()))
    for counter, statement in statements:
        for release_key, allocated, last in (await data.execute(statement)).all():
            if (last is None) or (last < allocated):
                yield AnnotatedDivergence(
                    [f"{counter}.last_allocated_number"],
                    "allocation_counters",
                    release_key,
                    Divergence(f"at least {allocated}", repr(last)),
                )


async def artifacts(data: db.Session) -> AsyncAnnotatedDivergences:
    via = sql.validate_instrumented_attribute
    statement = sqlalchemy.select(
        via(sql.Artifact.project_key),
        via(sql.Artifact.version),
        via(sql.Artifact.artifact_path),
        via(sql.Artifact.release_key),
        via(sql.Artifact.key_fingerprint),
        via(sql.Artifact.signature_path),
        via(sql.Artifact.signature_sha3_256),
    )
    for project_key, version, path, release_key, fingerprint, signature_path, digest in (
        await data.execute(statement)
    ).all():
        source = f"{project_key} {version} {path}"
        expected_key = sql.release_key(project_key, version)
        if (release_key is not None) and (release_key != expected_key):
            divergence = Divergence(repr(expected_key), repr(release_key))
            yield AnnotatedDivergence(["Artifact.release_key"], "artifact_release_key", source, divergence)
        if ((fingerprint is not None) or (digest is not None)) and (signature_path is None):
            divergence = Divergence("signature_path to be set with a signer or digest", "None")
            yield AnnotatedDivergence(["Artifact.signature_path"], "artifact_signature", source, divergence)
        if (digest is not None) and (not _SHA3_256_PATTERN.fullmatch(digest)):
            divergence = Divergence("64 lowercase hexadecimal digits", repr(digest))
            yield AnnotatedDivergence(["Artifact.signature_sha3_256"], "artifact_signature", source, divergence)


async def check_result_ignores(data: db.Session) -> AsyncAnnotatedDivergences:
    via = sql.validate_instrumented_attribute
    columns = [via(getattr(sql.CheckResultIgnore, name)) for name in _IGNORE_GLOBS]
    statement = sqlalchemy.select(
        via(sql.CheckResultIgnore.id), via(sql.CheckResultIgnore.project_key), *columns
    ).order_by(via(sql.CheckResultIgnore.id))
    for ignore_id, project_key, *patterns in (await data.execute(statement)).all():
        for name, pattern in zip(_IGNORE_GLOBS, patterns):
            if pattern is None:
                continue
            try:
                validation.validate_ignore_pattern(pattern)
            except ValueError:
                yield AnnotatedDivergence(
                    [f"CheckResultIgnore.{name}"],
                    "check_result_ignores",
                    f"{project_key} ignore {ignore_id}",
                    Divergence("a valid ignore pattern", repr(pattern)),
                )


def committee(c: sql.Committee) -> AnnotatedDivergences:
    """Check that a committee is valid."""

    yield from committee_child_committees(c)
    yield from committee_committers(c)
    yield from committee_full_name(c)
    yield from committee_key(c)
    yield from committee_members(c)
    yield from committee_release_managers(c)


@committee_components("Committee.child_committees")
def committee_child_committees(c: sql.Committee) -> Divergences:
    """Check that a committee has no child_committees."""

    expected: list[object] = []
    actual = c.child_committees
    yield from divergences(expected, actual)


@committee_components("Committee.committers")
def committee_committers(c: sql.Committee) -> Divergences:
    def okay(uid: str) -> bool:
        return bool(_ASF_UID_PATTERN.match(uid))

    for uid in c.committers:
        yield from divergences_predicate(okay, "value to be an ASF UID", uid)


@committee_components("Committee.name")
def committee_full_name(c: sql.Committee) -> Divergences:
    """Validate the Committee.name value."""

    full_name = c.name

    def present(fn: str | None) -> bool:
        return bool(fn)

    yield from divergences_predicate(
        present,
        "value to be set",
        full_name,
    )

    def trimmed(fn: str | None) -> bool:
        return False if (fn is None) else (fn == fn.strip())

    yield from divergences_predicate(
        trimmed,
        "value not to have surrounding whitespace",
        full_name,
    )

    def not_prefixed(fn: str | None) -> bool:
        return False if (fn is None) else (not fn.startswith("Apache "))

    yield from divergences_predicate(
        not_prefixed,
        "value not to start with 'Apache '",
        full_name,
    )


@committee_components("Committee.key")
def committee_key(c: sql.Committee) -> Divergences:
    def okay(key: str) -> bool:
        try:
            safe.CommitteeKey(key)
        except ValueError:
            return False
        return True

    yield from divergences_predicate(okay, "value to be a valid committee key", c.key)


@committee_components("Committee.committee_members")
def committee_members(c: sql.Committee) -> Divergences:
    """Check that every committee_members entry looks like an ASF UID."""

    def okay(uid: str) -> bool:
        return bool(_ASF_UID_PATTERN.match(uid))

    for uid in c.committee_members:
        yield from divergences_predicate(okay, "value to be an ASF UID", uid)


@committee_components("Committee.release_managers")
def committee_release_managers(c: sql.Committee) -> Divergences:
    """Check that every release_managers entry looks like an ASF UID and is a non-member committer."""

    def okay(uid: str) -> bool:
        return bool(_ASF_UID_PATTERN.match(uid))

    def committer(uid: str) -> bool:
        return uid in c.committers

    def non_member(uid: str) -> bool:
        return uid not in c.committee_members

    for uid in c.release_managers:
        yield from divergences_predicate(okay, "value to be an ASF UID", uid)
        yield from divergences_predicate(committer, "release manager to be a committer", uid)
        yield from divergences_predicate(non_member, "release manager to not be a PMC member", uid)


def committees(cs: Iterable[sql.Committee]) -> AnnotatedDivergences:
    """Validate multiple committees."""
    for c in cs:
        yield from committee(c)


async def consistency(data: db.Session) -> Consistency:
    # Get all releases from the database
    release_records = await data.release().all()
    finalising_releases = await _finalising_releases(data)
    database_dirs = await _consistency_database_dirs(release_records)
    if len(set(database_dirs)) != len(database_dirs):
        raise ValueError("Duplicate release directories in database")

    # Get all releases from the filesystem
    filesystem_dirs = set(await _get_filesystem_dirs())
    for record in release_records:
        if (record.phase != sql.ReleasePhase.RELEASE) or (
            (record.project_key, record.version) not in finalising_releases
        ):
            continue
        filesystem_dirs.discard(str(paths.get_unfinished_dir() / record.project_key / record.version))
        filesystem_dirs.discard(
            str(paths.get_unfinished_tombstone_for(record.safe_project_key, record.safe_version_key))
        )

    # Pair them up where possible
    database_paths = set(database_dirs)
    return Consistency(
        sorted(database_paths - filesystem_dirs),
        sorted(filesystem_dirs - database_paths),
        sorted(database_paths & filesystem_dirs),
    )


def divergences[T](expected: T, actual: T) -> Divergences:
    """Compare two values and yield the divergence if they differ."""
    if expected != actual:
        yield Divergence(repr(expected), repr(actual))


def divergences_predicate[T](okay: Callable[[T], bool], expected: str, actual: T) -> Divergences:
    """Apply a predicate to a value and yield the divergence if false."""
    if not okay(actual):
        yield Divergence(expected, repr(actual))


def divergences_with_annotations(
    components: Sequence[str],
    validator: str,
    source: str,
    ds: Divergences,
) -> AnnotatedDivergences:
    """Wrap divergences with components, validator, and source."""
    for d in ds:
        yield AnnotatedDivergence(list(components), validator, source, d)


async def everything(data: db.Session) -> AsyncAnnotatedDivergences:
    """Yield divergences for all projects and releases in the DB."""
    committees_sorted = await data.committee(_child_committees=True).order_by(sql.Committee.key).all()
    projects_sorted = await data.project(_release_policy=True).order_by(sql.Project.key).all()
    releases_sorted = await data.release().order_by(sql.Release.key).all()
    finalising_releases = await _finalising_releases(data)

    for c in await asyncio.to_thread(committees, committees_sorted):
        yield c

    for p in await asyncio.to_thread(projects, projects_sorted):
        yield p

    for r in await asyncio.to_thread(releases, releases_sorted, finalising_releases):
        yield r

    for checks in (
        allocation_counters,
        artifacts,
        check_result_ignores,
        personal_access_tokens,
        quarantine_pending,
        quarantine_staging,
        quarantines,
        release_finalise_publication,
    ):
        async for divergence in checks(data):
            yield divergence


async def personal_access_tokens(data: db.Session) -> AsyncAnnotatedDivergences:
    via = sql.validate_instrumented_attribute
    statement = sqlmodel.select(
        via(sql.PersonalAccessToken.id),
        via(sql.PersonalAccessToken.is_system),
        via(sql.PersonalAccessToken.asfuid),
        via(sql.PersonalAccessToken.created_by),
    ).order_by(via(sql.PersonalAccessToken.id))
    for token_id, is_system, asfuid, created_by in (await data.execute(statement)).all():
        expected = None if is_system else created_by
        if asfuid != expected:
            yield AnnotatedDivergence(
                ["PersonalAccessToken.asfuid", "PersonalAccessToken.is_system"],
                "personal_access_token_owner",
                f"token {token_id}",
                Divergence(repr(expected), repr(asfuid)),
            )


def project(p: sql.Project) -> AnnotatedDivergences:
    """Check that a project is valid."""

    yield from project_calver_format(p)
    yield from project_categories(p)
    yield from project_committee(p)
    yield from project_created(p)
    yield from project_created_by(p)
    yield from project_cycle_match(p)
    yield from project_download_path_suffix(p)
    yield from project_fetch_urls(p)
    yield from project_full_name(p)
    yield from project_key(p)
    yield from project_min_hours(p)
    yield from project_programming_languages(p)
    yield from project_recipient_defaults(p)
    yield from project_security_contact(p)
    yield from project_trusted_publishing(p)
    yield from project_version_pattern(p)


@project_components("Project.calver_format", "Project.cycle_match")
def project_calver_format(p: sql.Project) -> Divergences:
    if p.calver_format is None:
        return
    if p.version_method != sql.VersionMethod.CALVER:
        yield Divergence("calver_format to be unset when version_method is not calver", repr(p.calver_format))
        return
    try:
        calver.validate(p.calver_format)
    except ValueError:
        yield Divergence("a valid calver format", repr(p.calver_format))
        return
    yield from divergences(calver.cycle_regex(p.calver_format), p.cycle_match)


@project_components("Project.categories")
def project_categories(p: sql.Project) -> Divergences:
    """Check that the categories string uses 'label, label' syntax without colons."""

    def okay(cat: str | None) -> bool:
        if not cat:
            return True
        tokens = [t.strip() for t in cat.split(",")]
        if any((not t) or (":" in t) for t in tokens):
            return False
        return True

    expected = "comma separated labels without colon"
    yield from divergences_predicate(okay, expected, p.categories)


@project_components("Project.committee_key")
def project_committee(p: sql.Project) -> Divergences:
    """Check that the project is linked to a committee."""

    def okay(cn: str | None) -> bool:
        return cn is not None

    expected = "committee_key to be set"
    yield from divergences_predicate(okay, expected, p.committee_key)


@project_components("Project.created")
def project_created(p: sql.Project) -> Divergences:
    """Check that the project created timestamp is in the past."""
    now = datetime.datetime.now(datetime.UTC)

    def predicate(dt: datetime.datetime) -> bool:
        return dt < now

    expected = "value to be in the past"
    yield from divergences_predicate(predicate, expected, p.created)


@project_components("Project.created_by")
def project_created_by(p: sql.Project) -> Divergences:
    """Check that created_by, if set, looks like an ASF UID."""

    def okay(uid: str | None) -> bool:
        if not uid:
            return True
        return bool(_ASF_UID_PATTERN.match(uid))

    yield from divergences_predicate(okay, "value to be an ASF UID or unset", p.created_by)


@project_components("Project.cycle_match")
def project_cycle_match(p: sql.Project) -> Divergences:
    """Check that cycle_match, if set, is a regex with at least one capture group."""

    def okay(pattern: str | None) -> bool:
        if not pattern:
            return True
        try:
            compiled = validation.compile_project_pattern(pattern, captures=True)
        except ValueError:
            return False
        # Capture group 1 carries the cycle name, so we need at least one
        return compiled.groups >= 1

    yield from divergences_predicate(okay, "value to be a regex with a capture group, or unset", p.cycle_match)


@project_components("ReleasePolicy.download_path_suffix")
def project_download_path_suffix(p: sql.Project) -> Divergences:
    if p.release_policy is not None:
        yield from _download_path_suffix(p.release_policy.download_path_suffix)


@project_components("ReleasePolicy")
def project_fetch_urls(p: sql.Project) -> Divergences:
    for name in _FETCH_URLS:
        url = getattr(p, name)
        try:
            validation.validate_fetch_url(url)
        except ValueError:
            yield Divergence(f"{name} to be an allowed https URL", repr(url))


@project_components("Project.name")
def project_full_name(p: sql.Project) -> Divergences:
    """Check that the project full_name is present and starts with 'Apache '."""

    def okay(fn: str | None) -> bool:
        return (fn is not None) and fn.startswith("Apache ")

    expected = "full_name to be set and start with 'Apache '"
    yield from divergences_predicate(okay, expected, p.name)


@project_components("Project.key")
def project_key(p: sql.Project) -> Divergences:
    """Check that the project key is well-formed."""

    def okay(key: str) -> bool:
        try:
            safe.ProjectKey(key)
        except ValueError:
            return False
        return True

    yield from divergences_predicate(okay, "value to be a valid project key", p.key)


@project_components("ReleasePolicy.min_hours")
def project_min_hours(p: sql.Project) -> Divergences:
    try:
        validation.validate_policy_min_hours(p.policy_min_hours)
    except ValueError:
        yield Divergence("0 or 72 to 168 hours", repr(p.policy_min_hours))


@project_components("Project.programming_languages")
def project_programming_languages(p: sql.Project) -> Divergences:
    """Check that programming_languages uses 'label, label' syntax without colons."""

    def okay(pl: str | None) -> bool:
        if not pl:
            return True
        tokens = [t.strip() for t in pl.split(",")]
        if any((not t) or (":" in t) for t in tokens):
            return False
        return True

    expected = "comma separated labels without colon"
    yield from divergences_predicate(okay, expected, p.programming_languages)


@project_components("ReleasePolicy.recipient_defaults")
def project_recipient_defaults(p: sql.Project) -> Divergences:
    if p.release_policy is None:
        return
    defaults = p.release_policy.recipient_defaults
    if not isinstance(defaults, dict):
        yield Divergence("a mapping of actions to recipients", repr(defaults))
        return
    for action, entry in defaults.items():
        if (action not in _RECIPIENT_ACTIONS) or (not _recipients_shaped(entry)):
            yield Divergence("vote or announce recipients with to, cc, and bcc", repr({action: entry}))
            continue
        addresses = [address for address in (entry["to"], *entry["cc"], *entry["bcc"]) if address]
        try:
            validation.validate_announce_recipients(addresses)
        except ValueError:
            yield Divergence(f"{action} recipients on apache.org or a subdomain", repr(addresses))


@project_components("Project.security_contact")
def project_security_contact(p: sql.Project) -> Divergences:
    try:
        validation.validate_security_contact(p.committee_key or "", p.security_contact)
    except ValueError:
        yield Divergence("security@apache.org or the committee security address", repr(p.security_contact))


@project_components("ReleasePolicy.github_repository_name", "ReleasePolicy.github_repository_branch")
def project_trusted_publishing(p: sql.Project) -> Divergences:
    name = p.policy_github_repository_name
    branch = p.policy_github_repository_branch
    workflow_paths = [
        *p.policy_github_compose_workflow_path,
        *p.policy_github_vote_workflow_path,
        *p.policy_github_finish_workflow_path,
    ]
    try:
        util.validate_trusted_publishing_constraints(name, branch, workflow_paths)
    except ValueError as error:
        yield Divergence(str(error), repr((name, branch, workflow_paths)))


@project_components("Project.version_pattern")
def project_version_pattern(p: sql.Project) -> Divergences:
    """Check that version_pattern, if set, is a compilable regex."""

    def okay(pattern: str | None) -> bool:
        if not pattern:
            return True
        try:
            validation.compile_project_pattern(pattern)
        except ValueError:
            return False
        return True

    yield from divergences_predicate(okay, "value to be a valid regex or unset", p.version_pattern)


def projects(ps: Iterable[sql.Project]) -> AnnotatedDivergences:
    """Validate multiple projects."""
    for p in ps:
        yield from project(p)


async def quarantine_pending(data: db.Session) -> AsyncAnnotatedDivergences:
    via = sql.validate_instrumented_attribute
    validation_task = sqlmodel.select(via(sql.Task.id)).where(
        via(sql.Task.task_type) == sql.TaskType.QUARANTINE_VALIDATE,
        via(sql.Task.status).in_([sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE]),
        sqlalchemy.func.json_extract(via(sql.Task.task_args), "$.quarantined_id") == via(sql.Quarantined.id),
    )
    query = data.quarantined(status=sql.QuarantineStatus.PENDING).where(~validation_task.exists())
    for q in await query.order_by(via(sql.Quarantined.id)).all():
        yield AnnotatedDivergence(
            ["Quarantined.status"],
            "quarantine_pending",
            f"{q.release_key} quarantine {q.id}",
            Divergence("PENDING with a queued or active validation task", "no such task"),
        )


async def quarantine_staging(data: db.Session) -> AsyncAnnotatedDivergences:
    via = sql.validate_instrumented_attribute
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=_QUARANTINE_STAGING_MINUTES)
    query = data.quarantined(status=sql.QuarantineStatus.STAGING).where(via(sql.Quarantined.created) < cutoff)
    for q in await query.order_by(sql.Quarantined.id).all():
        yield AnnotatedDivergence(
            ["Quarantined.status"],
            "quarantine_staging",
            f"{q.release_key} quarantine {q.id}",
            Divergence(
                f"STAGING for at most {_QUARANTINE_STAGING_MINUTES} minutes",
                f"STAGING since {q.created.isoformat()}",
            ),
        )


async def quarantines(data: db.Session) -> AsyncAnnotatedDivergences:
    via = sql.validate_instrumented_attribute
    statement = (
        sqlalchemy.select(
            via(sql.Quarantined.id),
            via(sql.Quarantined.release_key),
            via(sql.Quarantined.status),
            via(sql.Quarantined.completed),
            via(sql.Quarantined.prior_revision_key),
            via(sql.Release.phase),
        )
        .outerjoin(sql.Release, via(sql.Release.key) == via(sql.Quarantined.release_key))
        .order_by(via(sql.Quarantined.id))
    )
    for quarantined_id, release_key, status, completed, prior, phase in (await data.execute(statement)).all():
        source = f"{release_key} quarantine {quarantined_id}"
        unfinished = status in (sql.QuarantineStatus.STAGING, sql.QuarantineStatus.PENDING)
        if (completed is not None) == unfinished:
            divergence = Divergence(f"completed to match status {status.name}", repr(completed))
            yield AnnotatedDivergence(["Quarantined.completed"], "quarantine_completed", source, divergence)
        if unfinished and (phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT):
            divergence = Divergence(f"a draft release when {status.name}", repr(phase))
            yield AnnotatedDivergence(["Quarantined.status"], "quarantine_release", source, divergence)
        if (prior is not None) and (not prior.startswith(f"{release_key} ")):
            divergence = Divergence(f"a revision key of {release_key}", repr(prior))
            yield AnnotatedDivergence(["Quarantined.prior_revision_key"], "quarantine_revision", source, divergence)


def release(r: sql.Release, finalising: bool = False) -> AnnotatedDivergences:
    """Check that a release is valid."""
    yield from release_archived(r)
    yield from release_created(r)
    yield from release_cycle(r)
    yield from release_download_path_suffix(r)
    yield from release_key(r)
    if (r.phase != sql.ReleasePhase.RELEASE) or (not finalising):
        yield from release_on_disk(r)
    yield from release_package_managers(r)
    yield from release_project_active(r)
    yield from release_released(r)
    yield from release_sboms(r)
    yield from release_vote_fields(r)
    yield from release_vote_logic(r)
    yield from release_vote_prerequisites(r)
    yield from release_voted_revision(r)


def release_components(
    *components: str,
) -> Callable[[ReleaseDivergences], ReleaseAnnotatedDivergences]:
    """Wrap a function that yields divergences to yield annotated divergences."""

    def wrap(original: ReleaseDivergences) -> ReleaseAnnotatedDivergences:
        def replacement(r: sql.Release) -> AnnotatedDivergences:
            yield from divergences_with_annotations(
                components,
                original.__name__,
                str(r.key),
                original(r),
            )

        return replacement

    return wrap


@release_components("Release.archived", "Release.archive_source", "Release.is_archived")
def release_archived(r: sql.Release) -> Divergences:
    if ((r.archived is not None) or (r.archive_source is not None)) and (not r.is_archived):
        yield Divergence("is_archived to be set with an archive date or source", repr(r.is_archived))


@release_components("Release.created")
def release_created(r: sql.Release) -> Divergences:
    """Check that the release created date is in the past."""
    now = datetime.datetime.now(datetime.UTC)

    def predicate(dt: datetime.datetime) -> bool:
        return dt < now

    expected = "value to be in the past"
    yield from divergences_predicate(predicate, expected, r.created)


@release_components("Release.cycle_key")
def release_cycle(r: sql.Release) -> Divergences:
    if not r.project:
        return
    try:
        cycle = cycles.cycle_name_for_version(r.project, r.version)
    except ValueError:
        cycle = cycles.DEFAULT_CYCLE
    yield from divergences(f"{r.project_key}-{cycle}", r.cycle_key)


@release_components("Release.download_path_suffix")
def release_download_path_suffix(r: sql.Release) -> Divergences:
    yield from _download_path_suffix(r.download_path_suffix)


async def release_finalise_publication(data: db.Session) -> AsyncAnnotatedDivergences:
    via = sql.validate_instrumented_attribute
    publish = orm.aliased(sql.Task)
    publication = sqlalchemy.and_(
        via(publish.task_type) == sql.TaskType.SVN_PUBLISH,
        via(publish.status) == sql.TaskStatus.COMPLETED,
        via(publish.project_key) == via(sql.Task.project_key),
        via(publish.version_key) == via(sql.Task.version_key),
        via(publish.revision_number) == via(sql.Task.revision_number),
    )
    statement = (
        sqlalchemy.select(
            via(sql.Task.id),
            via(sql.Task.project_key),
            via(sql.Task.version_key),
            via(sql.Task.revision_number),
            via(publish.result),
        )
        .outerjoin(publish, publication)
        .where(via(sql.Task.task_type) == sql.TaskType.RELEASE_FINALISE)
        .order_by(via(sql.Task.id))
    )
    rows = (await data.execute(statement)).all()
    for (task_id, project_key, version_key, revision), group in itertools.groupby(rows, key=lambda row: row[:4]):
        if not any(isinstance(row[4], results.SvnPublish) for row in group):
            yield AnnotatedDivergence(
                ["Task.task_type"],
                "release_finalise_publication",
                f"{project_key} {version_key} task {task_id}",
                Divergence(f"a usable completed SVN publication of revision {revision}", "none recorded"),
            )


@release_components("Release.key")
def release_key(r: sql.Release) -> Divergences:
    """Check that the release name is valid."""
    expected = sql.release_key(r.project_key, r.version)
    actual = r.key
    yield from divergences(expected, actual)


@release_components("Release")
def release_on_disk(r: sql.Release) -> Divergences:
    """Check that the release is on disk."""
    if r.phase == sql.ReleasePhase.RELEASE:
        unfinished = paths.get_unfinished_dir() / r.project_key / r.version
        expected = "no unfinished directory to remain after release"
        yield from divergences_predicate(lambda p: not p.path.exists(), expected, unfinished)
        return
    path = paths.release_directory(r)
    if r.phase == sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
        expected = "directory to exist"
        yield from divergences_predicate(lambda p: p.path.is_dir(), expected, path)
        return

    def okay(p: safe.StatePath) -> bool:
        # The release directory must exist and contain at least one entry
        return p.path.exists() and any(p.path.iterdir())

    expected = "directory to exist and contain files"
    yield from divergences_predicate(okay, expected, path)


@release_components("Release.package_managers")
def release_package_managers(r: sql.Release) -> Divergences:
    """Check that the release package managers are empty."""
    expected = []
    actual = r.package_managers
    yield from divergences(expected, actual)


@release_components("Release.phase", "Project.status")
def release_project_active(r: sql.Release) -> Divergences:
    if r.phase != sql.ReleasePhase.RELEASE:
        yield from divergences(sql.ProjectStatus.ACTIVE.value, r.project.status.value if r.project else None)


@release_components("Release.released")
def release_released(r: sql.Release) -> Divergences:
    """Check that the release released date is in the past or None."""
    now = datetime.datetime.now(datetime.UTC)

    def okay(dt: datetime.datetime | None) -> bool:
        if dt is None:
            return True
        return dt < now

    expected = "value to be in the past or None"
    yield from divergences_predicate(okay, expected, r.released)


@release_components("Release.sboms")
def release_sboms(r: sql.Release) -> Divergences:
    """Check that the release sboms are empty."""
    expected = []
    actual = r.sboms
    yield from divergences(expected, actual)


@release_components("Release.phase")
def release_vote_fields(r: sql.Release) -> Divergences:
    required, cleared = _PHASE_VOTE_FIELDS.get(r.phase, ((), ()))
    for name in required:
        if (name == "vote_resolved") and (r.current_vote_seq is None):
            continue
        if getattr(r, name) is None:
            yield Divergence(f"{name} to be set in {r.phase.value}", "None")
    for name in cleared:
        if (value := getattr(r, name)) is not None:
            yield Divergence(f"{name} to be unset in {r.phase.value}", repr(value))


@release_components("Release.vote_started", "Release.vote_resolved")
def release_vote_logic(r: sql.Release) -> Divergences:
    """Check that the release vote logic is valid."""

    def okay(sr: tuple[datetime.datetime | None, datetime.datetime | None]) -> bool:
        # The vote_resolved property must not be set unless vote_started is set
        match sr:
            case (None, None) | (_, None):
                return True
            case (None, _):
                # Resolved without being started
                return False
            case (_, _):
                return True

    expected = "vote_started to be set when vote_resolved is set"
    actual = (r.vote_started, r.vote_resolved)
    yield from divergences_predicate(okay, expected, actual)


@release_components("Release.current_vote_seq", "Release.voted_revision_number")
def release_vote_prerequisites(r: sql.Release) -> Divergences:
    for name, prerequisites in _VOTE_FIELD_PREREQUISITES.items():
        if getattr(r, name) is None:
            continue
        for prerequisite in prerequisites:
            if getattr(r, prerequisite) is None:
                yield Divergence(f"{prerequisite} to be set with {name}", "None")


@release_components("Release.voted_revision_number")
def release_voted_revision(r: sql.Release) -> Divergences:
    if (r.phase in _VOTING_PHASES) and (r.voted_revision_number is not None):
        yield from divergences(r.latest_revision_number, r.voted_revision_number)


def releases(
    rs: Iterable[sql.Release], finalising_releases: set[tuple[str, str]] | None = None
) -> AnnotatedDivergences:
    """Check that the releases are valid."""
    finalising_releases = finalising_releases or set()
    for r in rs:
        yield from release(r, (r.project_key, r.version) in finalising_releases)


async def _consistency_database_dirs(releases: Sequence[sql.Release]) -> list[str]:
    database_dirs: list[str] = []
    for release in releases:
        path = paths.release_directory_version(release)
        if (release.phase == sql.ReleasePhase.RELEASE) and (not await aiofiles.os.path.isdir(path)):
            continue
        database_dirs.append(str(path))
    return database_dirs


def _download_path_suffix(suffix: str | None) -> Divergences:
    if suffix is None:
        return
    try:
        validation.validate_download_path_suffix(suffix)
    except ValueError:
        yield Divergence("a valid download path suffix", repr(suffix))


async def _finalising_releases(data: db.Session) -> set[tuple[str, str]]:
    finalising_tasks = await data.task(
        status_in=[sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE],
        task_type=sql.TaskType.RELEASE_FINALISE,
    ).all()
    return {
        (task.project_key, task.version_key)
        for task in finalising_tasks
        if (task.project_key is not None) and (task.version_key is not None)
    }


async def _get_filesystem_dirs() -> list[str]:
    filesystem_dirs = []
    await _get_filesystem_dirs_finished(filesystem_dirs)
    await _get_filesystem_dirs_embargoed(filesystem_dirs)
    await _get_filesystem_dirs_unfinished(filesystem_dirs)
    return filesystem_dirs


async def _get_filesystem_dirs_embargoed(filesystem_dirs: list[str]) -> None:
    embargoed_dir = paths.get_embargoed_dir()
    embargoed_dir_contents = await aiofiles.os.listdir(embargoed_dir)
    for project_dir in embargoed_dir_contents:
        project_dir_path = os.path.join(embargoed_dir, project_dir)
        if await aiofiles.os.path.isdir(project_dir_path):
            for version_dir in await aiofiles.os.listdir(project_dir_path):
                if await aiofiles.os.path.isdir(os.path.join(project_dir_path, version_dir)):
                    version_dir_path = os.path.join(project_dir_path, version_dir)
                    if await aiofiles.os.path.isdir(version_dir_path):
                        filesystem_dirs.append(version_dir_path)


async def _get_filesystem_dirs_finished(filesystem_dirs: list[str]) -> None:
    finished_dir = paths.get_finished_dir()
    finished_dir_contents = await aiofiles.os.listdir(finished_dir)
    for project_dir in finished_dir_contents:
        project_dir_path = os.path.join(finished_dir, project_dir)
        if await aiofiles.os.path.isdir(project_dir_path):
            for version_dir in await aiofiles.os.listdir(project_dir_path):
                if await aiofiles.os.path.isdir(os.path.join(project_dir_path, version_dir)):
                    version_dir_path = os.path.join(project_dir_path, version_dir)
                    if await aiofiles.os.path.isdir(version_dir_path):
                        filesystem_dirs.append(version_dir_path)


async def _get_filesystem_dirs_unfinished(filesystem_dirs: list[str]) -> None:
    unfinished_dir = paths.get_unfinished_dir()
    unfinished_dir_contents = await aiofiles.os.listdir(unfinished_dir)
    for project_dir in unfinished_dir_contents:
        project_dir_path = os.path.join(unfinished_dir, project_dir)
        if await aiofiles.os.path.isdir(project_dir_path):
            for version_dir in await aiofiles.os.listdir(project_dir_path):
                if await aiofiles.os.path.isdir(os.path.join(project_dir_path, version_dir)):
                    version_dir_path = os.path.join(project_dir_path, version_dir)
                    if await aiofiles.os.path.isdir(version_dir_path):
                        filesystem_dirs.append(version_dir_path)


def _recipients_shaped(entry: object) -> bool:
    if (not isinstance(entry, dict)) or (set(entry) != {"bcc", "cc", "to"}):
        return False
    lists = (entry["cc"], entry["bcc"])
    if (not isinstance(entry["to"], str)) or (not all(isinstance(addresses, list) for addresses in lists)):
        return False
    return all(isinstance(address, str) for addresses in lists for address in addresses)


def _revision_allocations() -> sqlalchemy.Select:
    via = sql.validate_instrumented_attribute
    return (
        sqlalchemy.select(
            via(sql.Revision.release_key),
            sqlalchemy.func.max(via(sql.Revision.seq)),
            via(sql.RevisionCounter.last_allocated_number),
        )
        .outerjoin(sql.RevisionCounter, via(sql.RevisionCounter.release_key) == via(sql.Revision.release_key))
        .group_by(via(sql.Revision.release_key), via(sql.RevisionCounter.last_allocated_number))
    )


def _vote_allocations() -> sqlalchemy.Select:
    via = sql.validate_instrumented_attribute
    serials = sqlalchemy.union_all(
        sqlalchemy.select(via(sql.Release.key).label("release_key"), via(sql.Release.current_vote_seq).label("serial")),
        sqlalchemy.select(via(sql.BallotPaper.release_key), via(sql.BallotPaper.vote_seq)),
        sqlalchemy.select(
            via(sql.Task.project_key) + "-" + via(sql.Task.version_key),
            sqlalchemy.func.json_extract(via(sql.Task.task_args), "$.vote_seq"),
        ).where(
            via(sql.Task.task_type) == sql.TaskType.VOTE_INITIATE,
            sqlalchemy.func.json_type(via(sql.Task.task_args), "$.vote_seq") == "integer",
        ),
    ).subquery()
    return (
        sqlalchemy.select(
            serials.c.release_key,
            sqlalchemy.func.max(serials.c.serial),
            via(sql.VoteCounter.last_allocated_number),
        )
        .select_from(serials)
        .join(sql.Release, via(sql.Release.key) == serials.c.release_key)
        .outerjoin(sql.VoteCounter, via(sql.VoteCounter.release_key) == serials.c.release_key)
        .where(serials.c.serial.is_not(None), via(sql.Release.phase) != sql.ReleasePhase.RELEASE)
        .group_by(serials.c.release_key, via(sql.VoteCounter.last_allocated_number))
    )
