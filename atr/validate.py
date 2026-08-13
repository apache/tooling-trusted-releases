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
import re
from collections.abc import AsyncGenerator, Callable, Generator, Iterable, Sequence
from typing import Final, NamedTuple, TypeVar

import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.paths as paths


class Divergence(NamedTuple):
    expected: str
    actual: str


class AnnotatedDivergence(NamedTuple):
    components: list[str]
    validator: str
    source: str
    divergence: Divergence


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
    projects_sorted = await data.project(_distribution_channels=True).order_by(sql.Project.key).all()
    releases_sorted = await data.release().order_by(sql.Release.key).all()
    finalising_tasks = await data.task(
        status_in=[sql.TaskStatus.QUEUED, sql.TaskStatus.ACTIVE],
        task_type=sql.TaskType.RELEASE_FINALISE,
    ).all()
    finalising_releases = {
        (task.project_key, task.version_key)
        for task in finalising_tasks
        if (task.project_key is not None) and (task.version_key is not None)
    }

    for c in await asyncio.to_thread(committees, committees_sorted):
        yield c

    for p in await asyncio.to_thread(projects, projects_sorted):
        yield p

    for r in await asyncio.to_thread(releases, releases_sorted, finalising_releases):
        yield r


def project(p: sql.Project) -> AnnotatedDivergences:
    """Check that a project is valid."""

    yield from project_categories(p)
    yield from project_committee(p)
    yield from project_created(p)
    yield from project_created_by(p)
    yield from project_cycle_match(p)
    yield from project_full_name(p)
    yield from project_key(p)
    yield from project_programming_languages(p)
    yield from project_release_policy(p)
    yield from project_version_pattern(p)


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


@project_components("Project.release_policy")
def project_release_policy(p: sql.Project) -> Divergences:
    """Ensure that release_policy is None."""

    expected = None
    actual = p.release_policy_id
    yield from divergences(expected, actual)


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


def release(r: sql.Release, finalising: bool = False) -> AnnotatedDivergences:
    """Check that a release is valid."""
    yield from release_created(r)
    yield from release_key(r)
    if (r.phase != sql.ReleasePhase.RELEASE) or (not finalising):
        yield from release_on_disk(r)
    yield from release_package_managers(r)
    yield from release_released(r)
    yield from release_sboms(r)
    yield from release_vote_logic(r)


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


@release_components("Release.created")
def release_created(r: sql.Release) -> Divergences:
    """Check that the release created date is in the past."""
    now = datetime.datetime.now(datetime.UTC)

    def predicate(dt: datetime.datetime) -> bool:
        return dt < now

    expected = "value to be in the past"
    yield from divergences_predicate(predicate, expected, r.created)


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


def releases(
    rs: Iterable[sql.Release], finalising_releases: set[tuple[str, str]] | None = None
) -> AnnotatedDivergences:
    """Check that the releases are valid."""
    finalising_releases = finalising_releases or set()
    for r in rs:
        yield from release(r, (r.project_key, r.version) in finalising_releases)
