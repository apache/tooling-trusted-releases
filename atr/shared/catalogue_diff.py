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

import dataclasses
import enum
import hashlib
import json

import pydantic

import atr.shared.catalogue_rows as catalogue_rows


class Mode(enum.StrEnum):
    # REPLACE clears the committee and re-adds from the files, so every row is an add. ADDITIVE
    # matches each row against what is already there, so a row can also repoint a release or an
    # artifact, or say nothing new. The two share their types and their parsing, but not a code path
    REPLACE = "replace"
    ADDITIVE = "additive"


@dataclasses.dataclass(frozen=True)
class DbSnapshot:
    project_committee: dict[str, str]
    release_project: dict[str, str]
    artifact_by_dist: dict[tuple[str, str], tuple[str, str, str]]
    managed_project_keys: frozenset[str]
    # Every release key foundation-wide, unlike the workspace-scoped release_project
    release_keys: frozenset[str] = dataclasses.field(default_factory=frozenset)
    # The workspace committee's artifact primary keys, (project_key, version, artifact_path)
    artifact_pks: frozenset[tuple[str, str, str]] = dataclasses.field(default_factory=frozenset)


@dataclasses.dataclass(frozen=True)
class Conflict:
    table: str
    key: str
    reason: str


@dataclasses.dataclass(frozen=True)
class ArtifactRepoint:
    dist: tuple[str, str]
    from_pk: tuple[str, str, str]
    to_row: catalogue_rows.ArtifactRow


@dataclasses.dataclass(frozen=True)
class ReleaseRepoint:
    key: str
    from_project: str
    to_project: str
    row: catalogue_rows.ReleaseRow


@dataclasses.dataclass
class CatalogueDiff:
    adds: list[catalogue_rows.Row] = dataclasses.field(default_factory=list)
    artifact_repoints: list[ArtifactRepoint] = dataclasses.field(default_factory=list)
    release_repoints: list[ReleaseRepoint] = dataclasses.field(default_factory=list)
    conflicts: list[Conflict] = dataclasses.field(default_factory=list)
    project_deletions: list[str] = dataclasses.field(default_factory=list)
    release_deletions: list[str] = dataclasses.field(default_factory=list)
    artifact_deletions: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    unchanged: int = 0
    # A replace applies as a whole or not at all, so a conflict leaves the committee untouched
    refused: bool = False

    @property
    def counts(self) -> dict[str, int]:
        return {
            "add": len(self.adds),
            "artifact_repoint": len(self.artifact_repoints),
            "release_repoint": len(self.release_repoints),
            "delete": len(self.project_deletions) + len(self.release_deletions) + len(self.artifact_deletions),
            "conflict": len(self.conflicts),
            "unchanged": self.unchanged,
        }


def classify(
    rows_by_table: dict[str, list[dict[str, str]]], snapshot: DbSnapshot, committee_key: str, mode: Mode
) -> CatalogueDiff:
    match mode:
        case Mode.REPLACE:
            return classify_replace(rows_by_table, snapshot, committee_key)
        case Mode.ADDITIVE:
            return classify_additive(rows_by_table, snapshot, committee_key)


def classify_additive(
    rows_by_table: dict[str, list[dict[str, str]]], snapshot: DbSnapshot, committee_key: str
) -> CatalogueDiff:
    # Rows are matched against what the committee already holds. A row that matches nothing is an
    # add, one that matches somewhere else repoints it, and one that matches where it says it is is
    # unchanged. Nothing is deleted, so a bad row is skipped and the rest still apply
    diff = CatalogueDiff()
    projects, releases, artifacts = _parse_all(rows_by_table, diff)
    _classify_projects(projects, snapshot, diff, committee_key)
    _classify_releases(releases, snapshot, diff, committee_key)
    _classify_artifacts(artifacts, snapshot, diff, committee_key)
    return diff


def classify_replace(
    rows_by_table: dict[str, list[dict[str, str]]], snapshot: DbSnapshot, committee_key: str
) -> CatalogueDiff:
    # The files *are* the committee's catalogue. The deepest table uploaded decides what is deleted,
    # which cascades downwards. Against a cleared committee nothing matches, so the same
    # classification leaves every uploaded row an add, and a surviving row is a managed one
    diff = CatalogueDiff()
    snapshot = _clear(rows_by_table, snapshot, diff, committee_key)
    projects, releases, artifacts = _parse_all(rows_by_table, diff)
    _classify_projects(projects, snapshot, diff, committee_key)
    _classify_releases(releases, snapshot, diff, committee_key)
    _classify_artifacts(artifacts, snapshot, diff, committee_key)
    if diff.conflicts:
        # A row that cannot be applied would be deleted and not restored, so the files only describe
        # a complete catalogue if every row in them is good
        return _refused(diff)
    return diff


def fingerprint(diff: CatalogueDiff) -> str:
    # The outcome the preview showed. The files decide the rows, but the catalogue underneath them
    # decides what those rows do, so two classifications of one upload only agree while it holds
    # still. An apply compares this against the preview rather than trusting the files alone
    outcome = {
        "adds": sorted(_row_identity(add) for add in diff.adds),
        "artifact_deletions": sorted(diff.artifact_deletions),
        "artifact_repoints": sorted(
            [list(repoint.dist), list(repoint.from_pk), _row_identity(repoint.to_row)]
            for repoint in diff.artifact_repoints
        ),
        "conflicts": sorted([conflict.table, conflict.key, conflict.reason] for conflict in diff.conflicts),
        "project_deletions": sorted(diff.project_deletions),
        "refused": diff.refused,
        "release_deletions": sorted(diff.release_deletions),
        "release_repoints": sorted(
            [repoint.key, repoint.from_project, repoint.to_project] for repoint in diff.release_repoints
        ),
        "unchanged": diff.unchanged,
    }
    return hashlib.sha256(json.dumps(outcome, sort_keys=True).encode()).hexdigest()


def _classify_artifacts(
    rows: list[catalogue_rows.ArtifactRow], snapshot: DbSnapshot, diff: CatalogueDiff, committee_key: str
) -> None:
    # An artifact stays within its own committee. The releases are classified first, so a repointed
    # release has already taken its artifacts' primary keys to the target project, and a row can
    # name the artifact at either end of that: where the database still has it, or where it lands
    repointed = {
        (repoint.from_project, str(repoint.row.version)): repoint.to_project for repoint in diff.release_repoints
    }
    snapshot = _after_release_repoints(snapshot, diff)
    known_projects = _known_projects(snapshot, diff, committee_key)
    known_releases = set(snapshot.release_project) | {
        str(add.key) for add in diff.adds if isinstance(add, catalogue_rows.ReleaseRow)
    }
    # Keys the database holds now, plus the keys this diff adds to them. A repoint away from a key
    # never frees it, because the writer inserts the adds before it repoints anything
    claimed = set(snapshot.artifact_pks) | {_repointed_pk(pk, repointed) for pk in snapshot.artifact_pks}
    seen_dists: set[tuple[str, str]] = set()
    seen_pks: set[tuple[str, str, str]] = set()
    for row in rows:
        path = str(row.artifact_path)
        pk = (str(row.project_key), str(row.version), path)
        reference_conflict = _artifact_reference_conflict(row, snapshot, known_projects, known_releases)
        if reference_conflict is not None:
            diff.conflicts.append(reference_conflict)
            continue
        suffix = str(row.download_path_suffix) if row.download_path_suffix else ""
        if ((suffix, path) in seen_dists) or (pk in seen_pks):
            diff.conflicts.append(Conflict(table="artifacts", key=path, reason="listed more than once in this file"))
            continue
        seen_dists.add((suffix, path))
        seen_pks.add(pk)
        # The database still holds the artifact where it was, which is where the writer has to find it
        matched = snapshot.artifact_by_dist.get((suffix, path)) if suffix else None
        if (matched is not None) and (pk in (matched, _repointed_pk(matched, repointed))):
            # The row names the artifact where it is, or where its release is about to take it
            diff.unchanged += 1
            continue
        if matched is not None:
            if matched[0] in snapshot.managed_project_keys:
                diff.conflicts.append(
                    Conflict(table="artifacts", key=path, reason="source project has live workflow data")
                )
                continue
            if pk in claimed:
                diff.conflicts.append(Conflict(table="artifacts", key=path, reason="target already exists"))
                continue
            claimed.add(pk)
            diff.artifact_repoints.append(ArtifactRepoint(dist=(suffix, path), from_pk=matched, to_row=row))
            continue
        # Without a dist path there is nothing to match on, so the row can only describe a new
        # artifact. The dist path is the artifact's identity, so an edited one does too
        if pk in claimed:
            diff.conflicts.append(
                Conflict(table="artifacts", key=path, reason="artifact already exists at that project and version")
            )
            continue
        claimed.add(pk)
        diff.adds.append(row)


def _classify_projects(
    rows: list[catalogue_rows.ProjectRow], snapshot: DbSnapshot, diff: CatalogueDiff, committee_key: str
) -> None:
    seen: set[str] = set()
    for row in rows:
        key = str(row.key)
        if key in seen:
            diff.conflicts.append(Conflict(table="projects", key=key, reason="listed more than once in this file"))
            continue
        seen.add(key)
        conflict = _project_reference_conflict(row, snapshot, committee_key)
        if conflict is not None:
            diff.conflicts.append(conflict)
            continue
        if key in snapshot.project_committee:
            diff.unchanged += 1
            continue
        diff.adds.append(row)


def _classify_releases(
    rows: list[catalogue_rows.ReleaseRow], snapshot: DbSnapshot, diff: CatalogueDiff, committee_key: str
) -> None:
    known_projects = _known_projects(snapshot, diff, committee_key)
    seen: set[str] = set()
    # Keys this diff will end up holding, so two rows cannot converge on one release
    claimed: set[str] = set()
    for row in rows:
        key = str(row.key)
        if key in seen:
            diff.conflicts.append(Conflict(table="releases", key=key, reason="listed more than once in this file"))
            continue
        seen.add(key)
        target_project = str(row.project_key)
        current_project = snapshot.release_project.get(key)
        reference_conflict = _release_reference_conflict(row, snapshot, known_projects, current_project)
        if reference_conflict is not None:
            diff.conflicts.append(reference_conflict)
            continue
        if current_project is None:
            if (key in snapshot.release_keys) or (key in claimed):
                diff.conflicts.append(Conflict(table="releases", key=key, reason=f"release '{key}' already exists"))
                continue
            claimed.add(key)
            diff.adds.append(row)
            continue
        if current_project == target_project:
            diff.unchanged += 1
            continue
        version = str(row.version)
        target_release_key = f"{target_project}-{version}"
        if (target_release_key in snapshot.release_keys) or (target_release_key in claimed):
            diff.conflicts.append(
                Conflict(table="releases", key=key, reason=f"target already has release '{target_release_key}'")
            )
            continue
        collision = _repoint_artifact_collision(snapshot, current_project, target_project, version)
        if collision is not None:
            diff.conflicts.append(Conflict(table="releases", key=key, reason=collision))
            continue
        claimed.add(target_release_key)
        diff.release_repoints.append(
            ReleaseRepoint(key=key, from_project=current_project, to_project=target_project, row=row)
        )


def _after_release_repoints(snapshot: DbSnapshot, diff: CatalogueDiff) -> DbSnapshot:
    if not diff.release_repoints:
        return snapshot
    # A release key is "{owning project}-{version}", so a repoint rekeys the release. Both keys have
    # to resolve: an artifact row edited to follow the release names the new one, and an untouched
    # row from the same download still names the old one
    new_keys = {
        f"{release_repoint.to_project}-{release_repoint.row.version}": release_repoint.to_project
        for release_repoint in diff.release_repoints
    }
    return dataclasses.replace(
        snapshot,
        release_project=snapshot.release_project | new_keys,
        release_keys=snapshot.release_keys | frozenset(new_keys),
    )


def _artifact_reference_conflict(
    row: catalogue_rows.ArtifactRow, snapshot: DbSnapshot, known_projects: set[str], known_releases: set[str]
) -> Conflict | None:
    path = str(row.artifact_path)
    project_key = str(row.project_key)
    if project_key in snapshot.managed_project_keys:
        return Conflict(table="artifacts", key=path, reason="target project has live workflow data")
    if project_key not in known_projects:
        return Conflict(table="artifacts", key=path, reason=f"project '{project_key}' is not in this committee")
    if row.release_key not in known_releases:
        return Conflict(table="artifacts", key=path, reason=f"release '{row.release_key}' does not exist")
    return None


def _clear(
    rows_by_table: dict[str, list[dict[str, str]]], snapshot: DbSnapshot, diff: CatalogueDiff, committee_key: str
) -> DbSnapshot:
    # Projects with live workflow data stay in the snapshot, and are refused as usual
    catalogued = {
        key
        for key, committee in snapshot.project_committee.items()
        if (committee == committee_key) and (key not in snapshot.managed_project_keys)
    }
    clear_projects = "projects" in rows_by_table
    clear_releases = clear_projects or ("releases" in rows_by_table)
    clear_artifacts = clear_releases or ("artifacts" in rows_by_table)

    releases = {key for key, project in snapshot.release_project.items() if project in catalogued}
    artifacts = {dist for dist, pk in snapshot.artifact_by_dist.items() if pk[0] in catalogued}
    # Keyed on the primary keys, so an artifact with no dist path is cleared too
    artifact_pks = {pk for pk in snapshot.artifact_pks if pk[0] in catalogued}

    if clear_projects:
        diff.project_deletions.extend(sorted(catalogued))
    if clear_releases:
        diff.release_deletions.extend(sorted(releases))
    if clear_artifacts:
        diff.artifact_deletions.extend(sorted(artifact_pks))
    _warn_about_empty_levels(rows_by_table, diff, len(releases), len(artifact_pks))

    return dataclasses.replace(
        snapshot,
        project_committee={
            key: committee
            for key, committee in snapshot.project_committee.items()
            if not (clear_projects and (key in catalogued))
        },
        release_project={
            key: project
            for key, project in snapshot.release_project.items()
            if not (clear_releases and (key in releases))
        },
        release_keys=snapshot.release_keys - (releases if clear_releases else set()),
        artifact_by_dist={
            dist: pk for dist, pk in snapshot.artifact_by_dist.items() if not (clear_artifacts and (dist in artifacts))
        },
        artifact_pks=snapshot.artifact_pks - (artifact_pks if clear_artifacts else set()),
    )


def _first_error(error: pydantic.ValidationError) -> str:
    detail = error.errors()[0]
    field = ".".join(str(part) for part in detail["loc"])
    message = detail["msg"].removeprefix("Value error, ")
    return f"invalid {field}: {message}"


def _known_projects(snapshot: DbSnapshot, diff: CatalogueDiff, committee_key: str) -> set[str]:
    # A project added earlier in this diff counts as present
    return {key for key, committee in snapshot.project_committee.items() if committee == committee_key} | {
        str(add.key) for add in diff.adds if isinstance(add, catalogue_rows.ProjectRow)
    }


def _repoint_artifact_collision(snapshot: DbSnapshot, from_project: str, to_project: str, version: str) -> str | None:
    # A repointed release rewrites its artifacts' project key, which is part of their primary key
    for _, _, path in (pk for pk in snapshot.artifact_pks if (pk[0] == from_project) and (pk[1] == version)):
        if (to_project, version, path) in snapshot.artifact_pks:
            return f"target already has artifact '{path}' at version {version}"
    return None


def _row_identity(row: catalogue_rows.Row) -> list[str]:
    match row:
        case catalogue_rows.ArtifactRow():
            return ["artifacts", str(row.project_key), str(row.version), str(row.artifact_path)]
        case catalogue_rows.ProjectRow():
            return ["projects", str(row.key)]
        case catalogue_rows.ReleaseRow():
            return ["releases", str(row.key)]


def _repointed_pk(pk: tuple[str, str, str], repointed: dict[tuple[str, str], str]) -> tuple[str, str, str]:
    # Where an artifact's primary key ends up once the release repoints have applied
    project = repointed.get((pk[0], pk[1]))
    if project is None:
        return pk
    return (project, pk[1], pk[2])


def _parse[R: pydantic.BaseModel](
    diff: CatalogueDiff, table: str, identity: str, model_cls: type[R], rows: list[dict[str, str]]
) -> list[R]:
    parsed: list[R] = []
    for raw in rows:
        try:
            parsed.append(model_cls.model_validate(raw))
        except pydantic.ValidationError as error:
            diff.conflicts.append(Conflict(table=table, key=raw.get(identity, "?"), reason=_first_error(error)))
    return parsed


def _parse_all(
    rows_by_table: dict[str, list[dict[str, str]]], diff: CatalogueDiff
) -> tuple[list[catalogue_rows.ProjectRow], list[catalogue_rows.ReleaseRow], list[catalogue_rows.ArtifactRow]]:
    return (
        _parse(diff, "projects", "key", catalogue_rows.ProjectRow, rows_by_table.get("projects", [])),
        _parse(diff, "releases", "key", catalogue_rows.ReleaseRow, rows_by_table.get("releases", [])),
        _parse(diff, "artifacts", "artifact_path", catalogue_rows.ArtifactRow, rows_by_table.get("artifacts", [])),
    )


def _project_reference_conflict(
    row: catalogue_rows.ProjectRow, snapshot: DbSnapshot, committee_key: str
) -> Conflict | None:
    key = str(row.key)
    if key in snapshot.managed_project_keys:
        return Conflict(table="projects", key=key, reason="project has live workflow data")
    committee = str(row.committee_key) if row.committee_key else ""
    if committee != committee_key:
        return Conflict(table="projects", key=key, reason=f"committee '{committee}' is not this committee")
    # A project key is unique foundation-wide, so one already held elsewhere cannot be taken
    holder = snapshot.project_committee.get(key)
    if (holder is not None) and (holder != committee_key):
        return Conflict(table="projects", key=key, reason=f"project '{key}' belongs to committee '{holder}'")
    return None


def _refused(diff: CatalogueDiff) -> CatalogueDiff:
    return CatalogueDiff(
        conflicts=diff.conflicts,
        refused=True,
        warnings=[
            "Nothing was changed. Your files replace this committee's catalogue, so a row that cannot"
            " be applied would be deleted and not restored. Fix the conflicts below and upload again."
        ],
    )


def _release_reference_conflict(
    row: catalogue_rows.ReleaseRow,
    snapshot: DbSnapshot,
    known_projects: set[str],
    current_project: str | None,
) -> Conflict | None:
    key = str(row.key)
    target_project = str(row.project_key)
    # A release key is "{owning project}-{version}", so the three columns have one degree of freedom
    # between them; an edit that breaks the derivation is refused rather than applied
    owner = current_project if (current_project is not None) else target_project
    if key != f"{owner}-{row.version}":
        return Conflict(table="releases", key=key, reason=f"key does not match version '{row.version}'")
    if target_project in snapshot.managed_project_keys:
        return Conflict(table="releases", key=key, reason="target project has live workflow data")
    if target_project not in known_projects:
        if target_project in snapshot.project_committee:
            return Conflict(table="releases", key=key, reason=f"project '{target_project}' is not in this committee")
        return Conflict(table="releases", key=key, reason=f"target project '{target_project}' does not exist")
    if (current_project is not None) and (current_project in snapshot.managed_project_keys):
        return Conflict(table="releases", key=key, reason="source project has live workflow data")
    return None


def _warn_about_empty_levels(
    rows_by_table: dict[str, list[dict[str, str]]], diff: CatalogueDiff, releases: int, artifacts: int
) -> None:
    if ("projects" in rows_by_table) and ("releases" not in rows_by_table):
        diff.warnings.append(
            f"releases.csv was not uploaded, so {releases} releases and {artifacts} artifacts will be"
            " deleted and not restored, leaving the projects empty. Import releases.csv next to restore them."
        )
    if ("releases" in rows_by_table) and ("artifacts" not in rows_by_table):
        diff.warnings.append(
            f"artifacts.csv was not uploaded, so {artifacts} artifacts will be deleted and not restored,"
            " leaving the releases with no artifacts. Import artifacts.csv next to restore them."
        )
