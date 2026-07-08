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

import atr.shared.catalogue_diff as catalogue_diff
import atr.shared.catalogue_rows as catalogue_rows


def _project(key: str, committee: str = "alpha") -> dict[str, str]:
    return {"key": key, "committee_key": committee, "status": "active", "version_method": "simple"}


def _release(key: str, project: str, version: str) -> dict[str, str]:
    return {"key": key, "project_key": project, "version": version, "phase": "release", "created": "2020-01-01"}


def _artifact(project: str, version: str, path: str, suffix: str) -> dict[str, str]:
    return {"project_key": project, "version": version, "artifact_path": path, "download_path_suffix": suffix}


def _snapshot(**kw) -> catalogue_diff.DbSnapshot:
    base = dict(
        project_committee={"alpha-one": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one"},
        artifact_by_dist={},
        managed_project_keys=frozenset(),
    )
    base.update(kw)
    return catalogue_diff.DbSnapshot(**base)


def test_diff_counts_an_add() -> None:
    snap = _snapshot()
    assert "alpha-one" in snap.project_committee
    d = catalogue_diff.classify_additive({"projects": [_project("alpha-two")]}, snap, "alpha")
    assert d.counts["add"] == 1
    assert d.counts["conflict"] == 0


def test_classify_projects_adds_new_conflicts_missing_committee_and_managed() -> None:
    snap = _snapshot(managed_project_keys=frozenset({"alpha-managed"}))
    rows = [
        _project("alpha-two"),  # add
        _project("alpha-one"),  # present -> unchanged
        _project("beta-one", committee="beta"),  # missing committee -> conflict
        _project("alpha-managed"),  # managed -> conflict
    ]
    d = catalogue_diff.classify_additive({"projects": rows}, snap, "alpha")
    assert [str(a.key) for a in d.adds] == ["alpha-two"]
    assert d.unchanged == 1
    reasons = {c.key: c.reason for c in d.conflicts}
    assert "beta-one" in reasons and "committee" in reasons["beta-one"].lower()
    assert "alpha-managed" in reasons


def test_classify_releases_repoints_within_the_committee_and_refuses_to_leave_it() -> None:
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha", "beta-one": "beta"},
        release_project={
            "alpha-one-1.0.0": "alpha-one",
            "alpha-one-2.0.0": "alpha-one",
            "alpha-one-4.0.0": "alpha-one",
        },
    )
    rows = [
        _release("alpha-one-3.0.0", "alpha-one", "3.0.0"),  # add
        _release("alpha-one-1.0.0", "alpha-one", "1.0.0"),  # unchanged
        _release("alpha-one-2.0.0", "alpha-two", "2.0.0"),  # repoint within committee
        _release("alpha-one-4.0.0", "beta-one", "4.0.0"),  # another committee -> conflict
        _release("ghost-5.0.0", "ghost", "5.0.0"),  # target missing
    ]
    d = catalogue_diff.classify_additive({"releases": rows}, snap, "alpha")
    assert [str(a.key) for a in d.adds] == ["alpha-one-3.0.0"]
    assert d.unchanged == 1
    assert [m.key for m in d.release_repoints] == ["alpha-one-2.0.0"]
    reasons = {c.key: c.reason for c in d.conflicts}
    assert reasons["alpha-one-4.0.0"] == "project 'beta-one' is not in this committee"
    assert "does not exist" in reasons["ghost-5.0.0"]


def test_classify_releases_accepts_a_release_under_a_project_added_in_the_same_diff() -> None:
    # Combined upload: projects.csv adds alpha-new, releases.csv adds a release under it
    snap = _snapshot(project_committee={"alpha-one": "alpha"}, release_project={})
    d = catalogue_diff.classify_additive(
        {"projects": [_project("alpha-new")], "releases": [_release("alpha-new-1.0.0", "alpha-new", "1.0.0")]},
        snap,
        "alpha",
    )
    release_adds = [a for a in d.adds if isinstance(a, catalogue_rows.ReleaseRow)]
    assert [str(a.key) for a in release_adds] == ["alpha-new-1.0.0"]
    assert not d.conflicts


def test_classify_releases_flags_a_repoint_onto_an_existing_target_release() -> None:
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one"},
        release_keys=frozenset({"alpha-one-1.0.0", "alpha-two-1.0.0"}),
    )
    d = catalogue_diff.classify_additive(
        {"releases": [_release("alpha-one-1.0.0", "alpha-two", "1.0.0")]}, snap, "alpha"
    )
    assert not d.release_repoints
    assert [c.reason for c in d.conflicts] == ["target already has release 'alpha-two-1.0.0'"]


def test_classify_releases_refuses_a_row_whose_key_disagrees_with_its_version() -> None:
    # A release key is derived from its project and version, so a row that breaks the derivation
    # is refused rather than silently re-keying the release
    snap = _snapshot(project_committee={"alpha-one": "alpha", "alpha-two": "alpha"})
    d = catalogue_diff.classify_additive(
        {"releases": [_release("alpha-one-1.0.0", "alpha-two", "2.0.0")]}, snap, "alpha"
    )
    assert not d.release_repoints
    assert [c.reason for c in d.conflicts] == ["key does not match version '2.0.0'"]


def test_classify_releases_refuses_an_add_whose_key_already_exists() -> None:
    # The release belongs to another committee, so it is absent from the workspace-scoped map
    snap = _snapshot(
        project_committee={"alpha-one": "alpha"},
        release_project={},
        release_keys=frozenset({"alpha-one-1.0.0"}),
    )
    d = catalogue_diff.classify_additive(
        {"releases": [_release("alpha-one-1.0.0", "alpha-one", "1.0.0")]}, snap, "alpha"
    )
    assert not d.adds
    assert [c.reason for c in d.conflicts] == ["release 'alpha-one-1.0.0' already exists"]


def test_classify_artifacts_refuses_an_add_that_would_duplicate_an_existing_artifact() -> None:
    # Editing the dist suffix describes a new artifact, but its primary key is already taken
    snap = _snapshot(
        artifact_by_dist={("alpha/1.0.0", "a.tar.gz"): ("alpha-one", "1.0.0", "a.tar.gz")},
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tar.gz")}),
    )
    rows = [_artifact("alpha-one", "1.0.0", "a.tar.gz", "alpha/edited")]
    d = catalogue_diff.classify_additive({"artifacts": rows}, snap, "alpha")
    assert not d.adds
    assert not d.artifact_repoints
    assert [c.reason for c in d.conflicts] == ["artifact already exists at that project and version"]


def test_classify_artifacts_refuses_a_repoint_out_of_a_managed_project() -> None:
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-live": "alpha"},
        artifact_by_dist={("alpha/1.0.0", "a.tar.gz"): ("alpha-live", "1.0.0", "a.tar.gz")},
        managed_project_keys=frozenset({"alpha-live"}),
    )
    rows = [_artifact("alpha-one", "1.0.0", "a.tar.gz", "alpha/1.0.0")]
    d = catalogue_diff.classify_additive({"artifacts": rows}, snap, "alpha")
    assert not d.artifact_repoints
    assert [c.reason for c in d.conflicts] == ["source project has live workflow data"]


def test_classify_artifacts_refuses_a_project_outside_the_committee() -> None:
    snap = _snapshot(project_committee={"alpha-one": "alpha", "beta-one": "beta"})
    rows = [_artifact("beta-one", "1.0.0", "a.tar.gz", "beta/1.0.0")]
    d = catalogue_diff.classify_additive({"artifacts": rows}, snap, "alpha")
    assert [c.reason for c in d.conflicts] == ["project 'beta-one' is not in this committee"]


def test_replace_prunes_only_the_tables_that_were_uploaded() -> None:
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha"},
        artifact_by_dist={("alpha/1.0.0", "a.tar.gz"): ("alpha-one", "1.0.0", "a.tar.gz")},
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tar.gz")}),
    )
    d = catalogue_diff.classify_replace({"artifacts": []}, snap, "alpha")
    assert d.artifact_deletions == [("alpha-one", "1.0.0", "a.tar.gz")]
    assert not d.project_deletions
    assert not d.release_deletions


def test_replace_never_deletes_anything_under_a_managed_project() -> None:
    snap = _snapshot(
        project_committee={"alpha-live": "alpha"},
        release_project={"alpha-live-1.0.0": "alpha-live"},
        artifact_by_dist={("alpha/1.0.0", "a.tar.gz"): ("alpha-live", "1.0.0", "a.tar.gz")},
        managed_project_keys=frozenset({"alpha-live"}),
    )
    d = catalogue_diff.classify_replace({"projects": [], "releases": [], "artifacts": []}, snap, "alpha")
    assert d.counts["delete"] == 0


def test_replace_clears_downwards_from_the_deepest_table_uploaded() -> None:
    # releases.csv clears releases and, by cascade, artifacts; projects are left alone
    snap = _snapshot(
        project_committee={"alpha-one": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one", "alpha-one-2.0.0": "alpha-one"},
        artifact_by_dist={("alpha/1.0.0", "a.tar.gz"): ("alpha-one", "1.0.0", "a.tar.gz")},
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tar.gz")}),
    )
    rows = {"releases": [_release("alpha-one-1.0.0", "alpha-one", "1.0.0")]}
    d = catalogue_diff.classify_replace(rows, snap, "alpha")
    assert not d.project_deletions
    assert d.release_deletions == ["alpha-one-1.0.0", "alpha-one-2.0.0"]
    assert d.artifact_deletions == [("alpha-one", "1.0.0", "a.tar.gz")]
    # the cleared committee means the uploaded release is simply re-added
    assert [str(a.key) for a in d.adds] == ["alpha-one-1.0.0"]
    assert not d.conflicts
    assert any("artifacts.csv was not uploaded" in w for w in d.warnings)


def test_replace_warns_when_a_lower_table_is_missing() -> None:
    snap = _snapshot(
        project_committee={"alpha-one": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one"},
        artifact_by_dist={("alpha/1.0.0", "a.tar.gz"): ("alpha-one", "1.0.0", "a.tar.gz")},
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tar.gz")}),
    )
    d = catalogue_diff.classify_replace({"projects": [_project("alpha-one")]}, snap, "alpha")
    # projects.csv alone clears everything beneath it, and says so
    assert d.release_deletions == ["alpha-one-1.0.0"]
    assert d.artifact_deletions == [("alpha-one", "1.0.0", "a.tar.gz")]
    assert any("releases.csv was not uploaded" in w for w in d.warnings)


def test_replace_lets_an_edited_dist_suffix_replace_the_artifact() -> None:
    # An artifact's identity is its dist path, so editing it frees the primary key for the new row
    snap = _snapshot(
        artifact_by_dist={("alpha/old", "a.tar.gz"): ("alpha-one", "1.0.0", "a.tar.gz")},
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tar.gz")}),
    )
    rows = {"artifacts": [_artifact("alpha-one", "1.0.0", "a.tar.gz", "alpha/new")]}
    overwritten = catalogue_diff.classify_replace(rows, snap, "alpha")
    assert overwritten.artifact_deletions == [("alpha-one", "1.0.0", "a.tar.gz")]
    assert [str(add.download_path_suffix) for add in overwritten.adds] == ["alpha/new"]
    assert not overwritten.conflicts

    additive = catalogue_diff.classify_additive(rows, snap, "alpha")
    assert [c.reason for c in additive.conflicts] == ["artifact already exists at that project and version"]


def test_classify_refuses_a_row_listed_twice_in_the_same_file() -> None:
    # A row is checked against the rows already accepted from its own file, not just the database,
    # so a duplicate is a conflict rather than a failed insert
    snap = _snapshot(project_committee={"alpha-one": "alpha"}, release_project={})
    rows = {
        "projects": [_project("alpha-two"), _project("alpha-two")],
        "releases": [_release("alpha-one-1.0.0", "alpha-one", "1.0.0")] * 2,
        "artifacts": [_artifact("alpha-one", "1.0.0", "a.tar.gz", "alpha/1.0.0")] * 2,
    }
    d = catalogue_diff.classify_additive(rows, snap, "alpha")
    assert d.counts["add"] == 3
    assert [c.reason for c in d.conflicts] == ["listed more than once in this file"] * 3


def test_replace_is_refused_outright_when_any_row_conflicts() -> None:
    # A replace deletes what the files do not list, so a row it cannot apply would be deleted and
    # never restored. One bad row means the files are not a complete set, and nothing is changed
    snap = _snapshot(
        project_committee={"alpha-one": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one", "alpha-one-2.0.0": "alpha-one"},
    )
    rows = {
        "releases": [
            _release("alpha-one-1.0.0", "alpha-one", "1.0.0"),
            _release("alpha-one-2.0.0", "alpha-one", "9.9.9"),  # key disagrees with version
        ]
    }
    d = catalogue_diff.classify_replace(rows, snap, "alpha")
    assert d.counts["delete"] == 0
    assert d.counts["add"] == 0
    assert d.conflicts
    assert any("Nothing was changed" in w for w in d.warnings)


def test_classify_releases_refuses_two_rows_converging_on_one_release() -> None:
    # The check must cover the keys the diff itself will produce, not only the ones already in the db
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha", "alpha-three": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one", "alpha-two-1.0.0": "alpha-two"},
    )
    rows = {
        "releases": [
            _release("alpha-one-1.0.0", "alpha-three", "1.0.0"),
            _release("alpha-two-1.0.0", "alpha-three", "1.0.0"),
        ]
    }
    d = catalogue_diff.classify_additive(rows, snap, "alpha")
    assert len(d.release_repoints) == 1
    assert [c.reason for c in d.conflicts] == ["target already has release 'alpha-three-1.0.0'"]


def test_classify_artifacts_repoints_adds_warns_and_flags_dangling_references() -> None:
    snap = _snapshot(
        project_committee={"alpha-one": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one", "alpha-one-2.0.0": "alpha-one"},
        artifact_by_dist={
            ("incubator/alpha/1.0.0", "a.tar.gz"): ("alpha-old", "1.0.0", "a.tar.gz"),
        },
    )
    rows = [
        # dist matches existing under a different project -> repoint
        _artifact("alpha-one", "1.0.0", "a.tar.gz", "incubator/alpha/1.0.0"),
        # valid refs, has suffix, no dist match -> add
        _artifact("alpha-one", "2.0.0", "b.tar.gz", "alpha/2.0.0"),
        # no suffix, so nothing to match on, and its key is free -> add
        _artifact("alpha-one", "1.0.0", "c.tar.gz", ""),
        # derived release key alpha-one-3.0.0 does not exist -> conflict, not a dangling add
        _artifact("alpha-one", "3.0.0", "d.tar.gz", "alpha/3.0.0"),
    ]
    d = catalogue_diff.classify_additive({"artifacts": rows}, snap, "alpha")
    assert [str(r.to_row.project_key) for r in d.artifact_repoints] == ["alpha-one"]
    assert [str(a.artifact_path) for a in d.adds] == ["b.tar.gz", "c.tar.gz"]
    reasons = [c.reason for c in d.conflicts]
    assert any("release" in r.lower() and "does not exist" in r for r in reasons)


def test_classify_artifacts_keeps_an_artifact_without_a_dist_path() -> None:
    # The dist path is nullable, so an exported row can carry an empty one. A replace re-adds every
    # row it is given, and a row with nothing to match on is simply a new artifact
    snap = _snapshot(
        project_committee={"alpha-one": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one"},
        release_keys=frozenset({"alpha-one-1.0.0"}),
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tgz")}),
    )
    d = catalogue_diff.classify_replace({"artifacts": [_artifact("alpha-one", "1.0.0", "a.tgz", "")]}, snap, "alpha")
    assert [str(a.artifact_path) for a in d.adds] == ["a.tgz"]
    assert not d.conflicts
    assert d.refused is False


def test_classify_artifacts_refuses_an_add_onto_a_key_a_repoint_has_not_yet_freed() -> None:
    # The writer inserts the adds before it repoints anything, so a key an artifact is leaving is
    # still occupied when the add lands on it
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one", "alpha-two-1.0.0": "alpha-two"},
        release_keys=frozenset({"alpha-one-1.0.0", "alpha-two-1.0.0"}),
        artifact_by_dist={("alpha/old", "a.tgz"): ("alpha-one", "1.0.0", "a.tgz")},
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tgz")}),
    )
    rows = {
        "artifacts": [
            _artifact("alpha-two", "1.0.0", "a.tgz", "alpha/old"),
            _artifact("alpha-one", "1.0.0", "a.tgz", "alpha/new"),
        ]
    }
    d = catalogue_diff.classify_additive(rows, snap, "alpha")
    assert len(d.artifact_repoints) == 1
    assert not d.adds
    assert [c.reason for c in d.conflicts] == ["artifact already exists at that project and version"]


def test_classify_artifacts_left_alone_follow_the_release_that_is_repointed() -> None:
    # Only releases.csv is edited, so every artifact row still names the project the release came
    # from. Those rows describe artifacts the repoint carries, not dangling references
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one"},
        release_keys=frozenset({"alpha-one-1.0.0"}),
        artifact_by_dist={("alpha/1.0.0", "a.tgz"): ("alpha-one", "1.0.0", "a.tgz")},
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tgz")}),
    )
    rows = {
        "releases": [_release("alpha-one-1.0.0", "alpha-two", "1.0.0")],
        "artifacts": [_artifact("alpha-one", "1.0.0", "a.tgz", "alpha/1.0.0")],
    }
    d = catalogue_diff.classify_additive(rows, snap, "alpha")
    assert [r.key for r in d.release_repoints] == ["alpha-one-1.0.0"]
    assert not d.conflicts
    assert not d.adds
    assert not d.artifact_repoints
    assert d.unchanged == 1


def test_classify_rejects_malformed_keys_and_paths() -> None:
    snap = _snapshot()
    rows = {
        "projects": [{"key": "Bad-Cap", "committee_key": "alpha", "status": "active", "version_method": "simple"}],
        "artifacts": [_artifact("alpha-one", "1.0.0", "../escape.tgz", "alpha/1.0.0")],
    }
    d = catalogue_diff.classify_additive(rows, snap, "alpha")
    reasons = " ".join(c.reason for c in d.conflicts)
    assert "invalid key" in reasons
    assert "invalid artifact_path" in reasons


def test_classify_projects_refuses_a_key_another_committee_already_holds() -> None:
    # A project key is unique foundation-wide, so a row claiming one held elsewhere would take it
    snap = _snapshot(project_committee={"beta-one": "beta"})
    rows = {"projects": [_project("beta-one")]}
    for d in (
        catalogue_diff.classify_replace(rows, snap, "alpha"),
        catalogue_diff.classify_additive(rows, snap, "alpha"),
    ):
        assert not d.adds
        assert [c.reason for c in d.conflicts] == ["project 'beta-one' belongs to committee 'beta'"]


def test_classify_releases_refuses_a_repoint_whose_artifacts_would_collide() -> None:
    # A move rewrites its artifacts' project key, which is part of their primary key. An artifact
    # need not have a release, so the target can hold one without holding the release it repoints onto
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one"},
        release_keys=frozenset({"alpha-one-1.0.0"}),
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tar.gz"), ("alpha-two", "1.0.0", "a.tar.gz")}),
    )
    d = catalogue_diff.classify_additive(
        {"releases": [_release("alpha-one-1.0.0", "alpha-two", "1.0.0")]}, snap, "alpha"
    )
    assert not d.release_repoints
    assert [c.reason for c in d.conflicts] == ["target already has artifact 'a.tar.gz' at version 1.0.0"]


def test_classify_artifacts_refuses_an_add_a_repointed_release_already_takes() -> None:
    # The move takes the artifact to alpha-two, so an artifact row landing on the same primary key
    # would be a second row for it rather than an add
    snap = _snapshot(
        project_committee={"alpha-one": "alpha", "alpha-two": "alpha"},
        release_project={"alpha-one-1.0.0": "alpha-one"},
        release_keys=frozenset({"alpha-one-1.0.0"}),
        artifact_pks=frozenset({("alpha-one", "1.0.0", "a.tgz")}),
    )
    rows = {
        "releases": [_release("alpha-one-1.0.0", "alpha-two", "1.0.0")],
        "artifacts": [_artifact("alpha-two", "1.0.0", "a.tgz", "alpha/new")],
    }
    d = catalogue_diff.classify_additive(rows, snap, "alpha")
    assert [m.key for m in d.release_repoints] == ["alpha-one-1.0.0"]
    assert not d.adds
    assert d.counts["conflict"] == 1


def test_replace_refuses_a_dist_path_a_surviving_artifact_still_holds() -> None:
    # Managed projects are never cleared, so their artifacts are still catalogued afterwards. A row
    # claiming one of their dist paths would catalogue the same file in svn a second time
    snap = _snapshot(
        project_committee={"alpha-managed": "alpha", "alpha-two": "alpha"},
        release_project={"alpha-two-1.0.0": "alpha-two"},
        release_keys=frozenset({"alpha-two-1.0.0"}),
        managed_project_keys=frozenset({"alpha-managed"}),
        artifact_by_dist={("alpha/dist", "a.tgz"): ("alpha-managed", "1.0.0", "a.tgz")},
        artifact_pks=frozenset({("alpha-managed", "1.0.0", "a.tgz")}),
    )
    rows = {"artifacts": [_artifact("alpha-two", "1.0.0", "a.tgz", "alpha/dist")]}
    d = catalogue_diff.classify_replace(rows, snap, "alpha")
    assert not d.adds
    assert d.refused is True
    assert [c.reason for c in d.conflicts] == ["source project has live workflow data"]
