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
import importlib.util
import pathlib
import sys

import atr.models.sql as sql
import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures

spec = importlib.util.spec_from_file_location(
    "keys_backfill", pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "keys_backfill.py"
)
assert spec is not None
assert spec.loader is not None
keys_backfill = importlib.util.module_from_spec(spec)
sys.modules["keys_backfill"] = keys_backfill
spec.loader.exec_module(keys_backfill)


def event(fingerprint: str, placements: frozenset[pgp.Placement], day: int, source: str) -> "keys_backfill.Event":
    return keys_backfill.Event(
        updated=datetime.datetime(2020, 1, day, tzinfo=datetime.UTC),
        kind=keys_backfill._KIND_SVN,
        path=source,
        revision=day,
        source=source,
        fingerprint=fingerprint,
        actor="committer",
        role=sql.KeyRole.SERVICE,
        placements=placements,
        raw=None,
        confidence=None,
    )


def test_backfill_rows_are_deltas_in_time_order() -> None:
    state = pgp.certificate_placements(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    attachment = next(p for p in state if p[1] is not None)
    subset = frozenset(state - {attachment})
    fingerprint = pgp.certificate_block_fingerprint(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    events = [
        event(fingerprint, state, 3, "svn:/release/a/KEYS@2"),
        event(fingerprint, subset, 1, "svn:/release/a/KEYS@1"),
        event(fingerprint, state, 5, "svn:/release/b/KEYS@3"),
    ]
    backfill = keys_backfill._backfill(events, frozenset({fingerprint}), [], [], keys_backfill.collections.Counter())
    rows = backfill.rows[fingerprint]
    assert [row.seq for row in rows] == [1, 2]
    assert [row.source for row in rows] == ["svn:/release/a/KEYS@1", "svn:/release/a/KEYS@2"]
    assert rows[0].previous == frozenset()
    assert rows[1].result == state
    assert backfill.final[fingerprint] == state
    assert backfill.problems == []
    folded = pgp.fold_deltas(pgp.delta_fragments(row.previous, row.result) for row in rows)
    assert folded == state


def test_backfill_reports_missing_chains_and_actors() -> None:
    state = pgp.certificate_placements(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    fingerprint = pgp.certificate_block_fingerprint(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    anonymous = keys_backfill.dataclasses.replace(event(fingerprint, state, 1, "svn:/release/a/KEYS@1"), actor=None)
    backfill = keys_backfill._backfill(
        [anonymous], frozenset({fingerprint, "0" * 40}), [], [], keys_backfill.collections.Counter()
    )
    assert any("without an actor" in problem for problem in backfill.problems)
    assert any("no chain" in problem for problem in backfill.problems)


def test_resolve_follows_the_rehoming_rule() -> None:
    committees = frozenset({"accumulo", "activemq", "oltu"})
    projects = {"accumulo": "accumulo", "activemq-apollo": "activemq", "amber": "oltu"}
    assert keys_backfill._resolve("/release/accumulo/KEYS", committees, projects) == ("accumulo", "committee")
    assert keys_backfill._resolve("/release/activemq/activemq-apollo/KEYS", committees, projects) == (
        "activemq",
        "subproject activemq-apollo",
    )
    assert keys_backfill._resolve("/release/incubator/amber/KEYS", committees, projects) == ("oltu", "project amber")
    assert keys_backfill._resolve("/release/any23/KEYS", committees, {}) == (None, "none")


def test_switchover_digest_separates_categories() -> None:
    unresolved = keys_backfill.Listed(set(), {}, ["/release/x/KEYS"], [])
    rejected = keys_backfill.Listed(set(), {}, [], ["/release/x/KEYS"])
    assert keys_backfill._switchover_digest({}, [], unresolved) != keys_backfill._switchover_digest({}, [], rejected)


def test_listed_rejects_bad_blocks_without_losing_good_ones(tmp_path: pathlib.Path) -> None:
    good = pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC
    garbage = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nnot base64 at all!!\n-----END PGP PUBLIC KEY BLOCK-----\n"
    unterminated = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQGNBGRr\n"
    (tmp_path / "blobs").mkdir()
    blobs = {"a": good + garbage, "b": good + unterminated}
    entries = []
    for name, text in sorted(blobs.items()):
        digest = keys_backfill.hashlib.sha256(text.encode()).hexdigest()
        (tmp_path / "blobs" / f"{digest}.KEYS").write_text(text)
        entries.append({"path": f"/release/{name}/KEYS", "rev": 1, "date": "2020-01-01T00:00:00", "sha256": digest})
    listing = {"revision": 5, "paths": entries}
    listed = keys_backfill._listed(
        listing, tmp_path, 5, frozenset({"a", "b"}), {}, {}, keys_backfill.collections.Counter()
    )
    assert sorted(listed.rejected) == ["/release/a/KEYS", "/release/b/KEYS"]
    assert set(listed.coverage) == {pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT}
    assert listed.links == {
        ("a", pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT),
        ("b", pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT),
    }
