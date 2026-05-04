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

"""ECMA-428 Common Lifecycle Enumeration document generator.

Reads from `LifecycleEvent` rows and renders to ECMA-428 wire format.
Internal event names map to spec event types via `_SPEC_TYPE`:

    release  -> released
    archive  -> endOfDistribution
    eod      -> endOfDevelopment
    eos      -> endOfSupport
    eol      -> endOfLife
    withdraw -> withdrawn

Per ECMA-428 section 7.9, a `withdrawn` event retracts the *publication*
of a previously-emitted event - typically a correction. The withdrawn
event itself and the targeted event both remain in the document; the
withdrawal references its target via the `eventId` field. ATR mostly uses it
for corrections to previously-published cycle dates (see
policy.edit_cycle_dates).

We don't emit endOfMarketing, supersededBy, or componentRenamed.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any, Final

import atr.models.sql as sql

if TYPE_CHECKING:
    from collections.abc import Iterable

CLE_SCHEMA_URL: Final[str] = "https://ecma-tc54.github.io/ECMA-428/cle.v1.0.0.schema.json"

_SPEC_TYPE: Final[dict[sql.LifecycleEventType, str]] = {
    sql.LifecycleEventType.RELEASE: "released",
    sql.LifecycleEventType.ARCHIVE: "endOfDistribution",
    sql.LifecycleEventType.EOD: "endOfDevelopment",
    sql.LifecycleEventType.EOS: "endOfSupport",
    sql.LifecycleEventType.EOL: "endOfLife",
    sql.LifecycleEventType.WITHDRAW: "withdrawn",
}


def project_document(
    project: sql.Project,
    events: Iterable[sql.LifecycleEvent],
    releases: Iterable[sql.Release],
    *,
    now: datetime.datetime,
) -> dict[str, Any]:
    """Generate a CLE document covering every event for a project.

    This is the canonical form per ECMA-428: one document per component.
    `releases` are needed to resolve version strings and cycle release sets
    at render time.
    """
    return _document(project, list(events), list(releases), now=now)


def release_document(
    project: sql.Project,
    release: sql.Release,
    events: Iterable[sql.LifecycleEvent],
    *,
    now: datetime.datetime,
) -> dict[str, Any]:
    """Generate a CLE document filtered to a single release.

    `events` should already be filtered by the caller to those touching the
    release: events with `version_key == release.key`, plus the cycle's
    eod/eos/eol events. The document still has the component-level shape
    ECMA-428 prescribes; the filtering is a derived view.
    """
    return _document(project, list(events), [release], now=now)


def _document(
    project: sql.Project,
    events: list[sql.LifecycleEvent],
    releases: list[sql.Release],
    *,
    now: datetime.datetime,
) -> dict[str, Any]:
    rendered_events = _events(project, events, releases)
    # Document is "as of" the latest event's publication. Falls back to `now`
    # when no events are present so the field is always populated.
    updated_at = max((e.published for e in events if e.id is not None), default=now)
    doc: dict[str, Any] = {
        "$schema": CLE_SCHEMA_URL,
        "identifier": _identifier(project),
        "updatedAt": _iso(updated_at),
        "events": rendered_events,
    }
    # Patch in the support information - currently static
    if any(e["type"] in {"endOfDevelopment", "endOfSupport"} for e in rendered_events):
        doc["definitions"] = {
            "support": [
                {"id": "default", "description": "Apache project community support"},
            ]
        }
    return doc


def _events(
    project: sql.Project,
    events: list[sql.LifecycleEvent],
    releases: list[sql.Release],
) -> list[dict[str, Any]]:
    """Render lifecycle event rows to ECMA-428 events.

    Spec id is the db id. Output is sorted descending by id per § 6.2.
    """
    releases_by_key = {r.key: r for r in releases}
    releases_by_cycle = _releases_by_cycle(releases)

    emit = [event for event in events if event.id is not None]
    emit.sort(key=lambda e: e.id or 0, reverse=True)

    rendered: list[dict[str, Any]] = []
    for event in emit:
        rendered_event = _render_event(project, event, releases_by_key, releases_by_cycle)
        rendered_event["id"] = event.id
        rendered.append(rendered_event)
    return rendered


def _identifier(project: sql.Project) -> str:
    """Render the project as a Package-URL.

    `pkg:apache/<project_key>` is the simplest form. Per-distribution PURLs
    (`pkg:maven/...`, `pkg:pypi/...`) belong on the artifact catalog (#911),
    not on the lifecycle doc. This may change with outcome of https://github.com/package-url/purl-spec/issues/516
    """
    return f"pkg:apache/{project.key}"


def _iso(value: datetime.datetime) -> str:
    """Render a UTC datetime as ISO 8601 with a Z suffix."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    else:
        value = value.astimezone(datetime.UTC)
    return value.isoformat().replace("+00:00", "Z")


def _release_for(event: sql.LifecycleEvent, releases_by_key: dict[str, sql.Release]) -> sql.Release:
    """Resolve the release a version-scoped event refers to.

    Raises if the event has no version_key set, or if its version_key
    isn't in `releases_by_key`.
    """
    if event.version_key is None:
        raise ValueError(f"{event.event} event requires version_key")
    release = releases_by_key.get(event.version_key)
    if release is None:
        raise ValueError(f"{event.event} event references unknown release {event.version_key}")
    return release


def _releases_by_cycle(releases: list[sql.Release]) -> dict[str, list[sql.Release]]:
    grouped: dict[str, list[sql.Release]] = {}
    for release in releases:
        grouped.setdefault(release.cycle_key, []).append(release)
    return grouped


def _render_event(
    project: sql.Project,
    event: sql.LifecycleEvent,
    releases_by_key: dict[str, sql.Release],
    releases_by_cycle: dict[str, list[sql.Release]],
) -> dict[str, Any]:
    """Render one LifecycleEvent to its CLE spec dict shape. id is set by the caller."""
    rendered: dict[str, Any] = {
        "type": _SPEC_TYPE[event.event],
        "effective": _iso(event.effective),
        "published": _iso(event.published),
    }

    if event.event is sql.LifecycleEventType.RELEASE:
        release = _release_for(event, releases_by_key)
        rendered["version"] = release.version
        rendered["license"] = "Apache-2.0"
    elif event.event is sql.LifecycleEventType.ARCHIVE:
        release = _release_for(event, releases_by_key)
        rendered["versions"] = [{"range": _vers_literal(project, release.version)}]
    elif event.event in (
        sql.LifecycleEventType.EOD,
        sql.LifecycleEventType.EOS,
        sql.LifecycleEventType.EOL,
    ):
        if event.cycle_key is None:
            raise ValueError(f"{event.event} event requires cycle_key")
        cycle_name = event.cycle_key.removeprefix(f"{project.key}-")
        cycle_releases = releases_by_cycle.get(event.cycle_key, [])
        rendered["versions"] = [{"range": _vers_for_cycle(project, cycle_name, cycle_releases)}]
        if event.event in (sql.LifecycleEventType.EOD, sql.LifecycleEventType.EOS):
            rendered["supportId"] = "default"
    elif event.event is sql.LifecycleEventType.WITHDRAW:
        if event.target_event_id is None:
            raise ValueError("withdraw event requires target_event_id")
        rendered["eventId"] = event.target_event_id

    if event.reference_urls:
        rendered["references"] = list(event.reference_urls)

    return rendered


def _semver_bounds_for_cycle_name(name: str) -> tuple[str, str] | None:
    """Derive (floor, ceiling) semver bounds from a cycle name prefix.

    Accepts numeric prefixes with optional trailing wildcards: "2", "2.1",
    "2.x", "2.1.x" all resolve. Full version strings ("1.2.3"), non-numeric
    names ("default"), and unusually shaped names ("2.x.0") return None.
    """
    parts = name.split(".")
    while parts and parts[-1].lower() == "x":
        parts.pop()
    if not parts or len(parts) >= 3:
        return None
    nums: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        nums.append(int(part))
    floor_parts = list(nums)
    while len(floor_parts) < 3:
        floor_parts.append(0)
    floor = ".".join(str(n) for n in floor_parts)
    ceiling_parts = list(nums)
    ceiling_parts[-1] += 1
    while len(ceiling_parts) < 3:
        ceiling_parts.append(0)
    ceiling = ".".join(str(n) for n in ceiling_parts)
    return floor, ceiling


def _vers_for_cycle(project: sql.Project, cycle_name: str, cycle_releases: list[sql.Release]) -> str:
    """Render a VERS range covering a cycle.

    SEMVER projects emit a forward-looking semver range derived from the
    cycle name (e.g. cycle "2.x" becomes `>=2.0.0|<3.0.0`), so the range
    covers future versions added to the cycle as well as current ones.

    SIMPLE and CALVER projects fall back to a literal list of every version
    currently in the cycle - or a wildcard for empty cycles - because we
    don't have range semantics for those schemes.
    """
    scheme = _vers_scheme(project)
    if project.version_method is sql.VersionMethod.SEMVER:
        bounds = _semver_bounds_for_cycle_name(cycle_name)
        if bounds is not None:
            floor, ceiling = bounds
            return f"vers:{scheme}/>={floor}|<{ceiling}"
    if not cycle_releases:
        return f"vers:{scheme}/*"
    constraints = "|".join(r.version for r in cycle_releases)
    return f"vers:{scheme}/{constraints}"


def _vers_literal(project: sql.Project, version: str) -> str:
    """Render a VERS range matching exactly one version."""
    return f"vers:{_vers_scheme(project)}/{version}"


def _vers_scheme(project: sql.Project) -> str:
    """Pick a VERS versioning scheme for a project.

    SEMVER projects emit `semver`. SIMPLE and CALVER fall back to `generic`;
    calver support is deferred.
    """
    if project.version_method is sql.VersionMethod.SEMVER:
        return "semver"
    return "generic"
