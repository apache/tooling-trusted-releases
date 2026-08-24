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


import argparse
import asyncio
import collections
import dataclasses
import datetime
import hashlib
import json
import logging.handlers
import pathlib
import sys
import tarfile
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import openpgp.composed
import sqlalchemy
import sqlmodel

import atr.cache as cache
import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.db.interaction as interaction
import atr.loggers as loggers
import atr.metadata as metadata
import atr.models.sql as sql
import atr.pgp as pgp
import atr.principal as principal
import atr.registry as registry
import atr.storage as storage
import atr.storage.writers.keys as keys_writer
import atr.svn.dist as dist
import atr.util as util

_ACTOR_OVERRIDES: Final[dict[tuple[str, str], str]] = {
    ("8fd9d52d7e1170a1abd7396f2abfa58b5e443290", "2025-09-23T09:21:03"): "sparsick",
}
_ARCHIVE_CAPTURE_DATE: Final = "2026-06-24"
_ARCHIVE_CAPTURE_SHA256: Final = "31d9ed4517539f16a05facc32e74700fe947d56accde4494459ed9fbff4d94ea"
_ARMOR_BEGIN: Final = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
_ARMOR_END: Final = "-----END PGP PUBLIC KEY BLOCK-----"
_DELETED_FINGERPRINTS: Final[tuple[str, str]] = (
    "68ff2e20f02b070d73d416188de8cc167fe2663a",
    "0e6cd3dc60509aeabd66797fe8b8d83357d95dc4",
)
_EXPECTED_UNREADABLE_HEADS: Final[dict[str, str]] = {
    "771625b192aeb258383a711f8b504ed095522e76": "ecaf3d327a12c9061016fa53dac2cfd7e85da224612175f0b7bacde25ff77f89",
}
_KIND_SVN: Final = 0
_KIND_ARCHIVE: Final = 1
_KIND_ATR: Final = 2
_KIND_NAMES: Final[dict[int, str]] = {_KIND_SVN: "svn", _KIND_ARCHIVE: "archive", _KIND_ATR: "atr"}

type Placements = frozenset[pgp.Placement]
type Blocks = dict[str, tuple[list[tuple[str, Placements, bytes]], int]]


@dataclasses.dataclass(frozen=True)
class Event:
    updated: datetime.datetime
    kind: int
    path: str
    revision: int
    source: str
    fingerprint: str
    actor: str | None
    role: sql.KeyRole
    placements: Placements
    raw: bytes | None
    confidence: str | None


@dataclasses.dataclass(frozen=True)
class Head:
    fingerprint: str
    block: str
    uids: tuple[str, ...]
    latest_self_signature: datetime.datetime | None
    placements: int
    parse_error: str | None


@dataclasses.dataclass(frozen=True)
class Listed:
    links: set[tuple[str, str]]
    coverage: dict[str, Placements]
    unresolved: list[str]
    rejected: list[str]


@dataclasses.dataclass(frozen=True)
class PlannedRow:
    fingerprint: str
    seq: int
    source: str
    raw: bytes | None
    previous: Placements
    result: Placements
    updated: datetime.datetime
    actor: str
    role: sql.KeyRole
    kind: int
    confidence: str | None


@dataclasses.dataclass(frozen=True)
class Timeless:
    fingerprint: str
    channel: str
    event: str
    placements: Placements


@dataclasses.dataclass(frozen=True)
class UidChange:
    fingerprint: str
    previous: str | None
    value: str | None
    branch: str


@dataclasses.dataclass(frozen=True)
class Backfill:
    rows: dict[str, list[PlannedRow]]
    final: dict[str, Placements]
    delta_bytes: int
    events_considered: collections.Counter
    problems: list[str]
    unreadable_heads: list[str]
    block_errors: collections.Counter


def main() -> None:
    asyncio.run(_run(_parse_args()))


def _archive_events(inputs: pathlib.Path, blocks: Blocks, errors: collections.Counter) -> list[Event]:
    tar_path = inputs / "keys-files.tar.gz"
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    if digest != _ARCHIVE_CAPTURE_SHA256:
        raise ValueError(f"{tar_path} has digest {digest}, not the pinned {_ARCHIVE_CAPTURE_SHA256}")
    events = []
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar:
            if not (member.isfile() and member.name.endswith("KEYS")):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            text = handle.read().decode("utf-8", "replace")
            path = "/" + member.name.removeprefix("./").lstrip("/")
            updated = datetime.datetime.fromtimestamp(member.mtime, tz=datetime.UTC)
            for fingerprint, placements in _file_certificates(text, blocks, errors).items():
                events.append(
                    Event(
                        updated=updated,
                        kind=_KIND_ARCHIVE,
                        path=path,
                        revision=0,
                        source=f"archive:{_ARCHIVE_CAPTURE_DATE}:{path}",
                        fingerprint=fingerprint,
                        actor=constants.SYSTEM_SERVICE_UID,
                        role=sql.KeyRole.SERVICE,
                        placements=placements,
                        raw=None,
                        confidence=None,
                    )
                )
    return events


def _armored_blocks(text: str) -> list[str]:
    found = []
    current: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _ARMOR_BEGIN:
            current = [line]
        elif current is not None:
            current.append(line)
            if stripped == _ARMOR_END:
                found.append("\n".join(current) + "\n")
                current = None
    return found


def _automated_uid(uid: str) -> bool:
    if not any(label in uid for label in util.AUTOMATED_RELEASE_SIGNING_LABELS):
        return False
    return ("private@" in uid) and (".apache.org" in uid)


def _backfill(
    events: list[Event],
    scope: frozenset[str],
    timeless: list[Timeless],
    unreadable_heads: list[str],
    block_errors: collections.Counter,
) -> Backfill:
    rows: dict[str, list[PlannedRow]] = collections.defaultdict(list)
    final: dict[str, Placements] = {}
    considered: collections.Counter = collections.Counter()
    problems = []
    delta_bytes = 0
    for event in sorted(events, key=lambda e: (e.updated, e.kind, e.path, e.revision)):
        if event.fingerprint not in scope:
            continue
        considered[event.kind] += 1
        previous = final.get(event.fingerprint, frozenset())
        result = previous | event.placements
        if result == previous:
            continue
        if event.actor is None:
            problems.append(f"row-producing event without an actor: {event.source} {event.fingerprint}")
            continue
        deletions, additions = pgp.delta_fragments(previous, result)
        if deletions is not None:
            problems.append(f"deletion computed for a union: {event.source} {event.fingerprint}")
            continue
        delta_bytes += len(additions or b"")
        rows[event.fingerprint].append(
            PlannedRow(
                fingerprint=event.fingerprint,
                seq=len(rows[event.fingerprint]) + 1,
                source=event.source,
                raw=event.raw,
                previous=previous,
                result=result,
                updated=event.updated,
                actor=event.actor,
                role=event.role,
                kind=event.kind,
                confidence=event.confidence,
            )
        )
        final[event.fingerprint] = result
    for missing in sorted(scope - set(rows)):
        problems.append(f"certificate has no chain from any source: {missing}")
    for record in timeless:
        if record.fingerprint not in scope:
            continue
        if not (record.placements <= final.get(record.fingerprint, frozenset())):
            problems.append(
                f"timeless submission holds unplaced packets: {record.channel}/{record.event} {record.fingerprint}"
            )
    return Backfill(dict(rows), final, delta_bytes, considered, problems, unreadable_heads, block_errors)


def _block_certificates(
    block: str, blocks: Blocks, errors: collections.Counter
) -> tuple[list[tuple[str, Placements, bytes]], int]:
    digest = hashlib.sha256(block.encode("utf-8", "replace")).hexdigest()
    if digest in blocks:
        return blocks[digest]
    found: list[tuple[str, Placements, bytes]] = []
    failures = 0
    try:
        spans = pgp.certificate_spans(block)
    except Exception as e:
        errors[f"span: {str(e)[:80]}"] += 1
        blocks[digest] = (found, 1)
        return blocks[digest]
    for span in spans:
        try:
            fingerprint = pgp.certificate_block_fingerprint(pgp._armored(span))
            placements = pgp.fragment_placements(span)
        except Exception as e:
            errors[f"walk: {str(e)[:80]}"] += 1
            failures += 1
            continue
        kept = frozenset(p for p in placements if (p[1] is None) or (not pgp._local_certification_frame(p[1])))
        if kept != placements:
            errors["local certification trimmed"] += len(placements - kept)
        found.append((fingerprint, kept, bytes(span)))
    blocks[digest] = (found, failures)
    return blocks[digest]


def _candidate_names(top: str, rest: tuple[str, ...]) -> list[str]:
    if not rest:
        return [top]
    decomposed = dist.decompose(top, rest, None)
    subproject = decomposed.subproject if decomposed else None
    subproject = dist.module_component(top, subproject) or subproject
    remapped = dist.PROJECT_REMAPS.get((top, subproject))
    return [remapped] if remapped is not None else dist.candidate_keys(top, subproject)


async def _certificates(data: db.Session) -> list[sql.SigningCertificate]:
    result = await data.execute(sqlmodel.select(sql.SigningCertificate))
    return list(result.scalars().all())


async def _deleted_artifact_count(data: db.Session) -> int:
    via = sql.validate_instrumented_attribute
    result = await data.execute(
        sqlalchemy.select(sqlalchemy.func.count())
        .select_from(sql.Artifact)
        .join(sql.SigningKey, via(sql.Artifact.key_fingerprint) == via(sql.SigningKey.fingerprint))
        .where(via(sql.SigningKey.certificate_fingerprint).in_(_DELETED_FINGERPRINTS))
    )
    return result.scalar_one()


def _file_certificates(text: str, blocks: Blocks, errors: collections.Counter) -> dict[str, Placements]:
    merged: dict[str, Placements] = {}
    for block in _armored_blocks(text):
        entries, _failures = _block_certificates(block, blocks, errors)
        for fingerprint, placements, _span in entries:
            merged[fingerprint] = merged.get(fingerprint, frozenset()) | placements
    return merged


def _head_placements(armored: str | bytes) -> Placements:
    text = armored if isinstance(armored, str) else armored.decode("utf-8", "replace")
    merged: Placements = frozenset()
    for span in pgp.certificate_spans(text):
        merged = merged | pgp.fragment_placements(span)
    return merged


def _heads(final: dict[str, Placements]) -> dict[str, Head]:
    heads = {}
    for fingerprint, placements in final.items():
        block = pgp.certificate_block(placements)
        parse_error = None
        latest = None
        try:
            key, _ = openpgp.composed.SignedPublicKey.from_armor(block)
            latest = pgp.latest_self_signature_created_at(key)
            if key.fingerprint.lower() != fingerprint:
                parse_error = f"serialised head parses as {key.fingerprint.lower()}"
            elif pgp.certificate_placements(block) != placements:
                parse_error = "serialised head does not read back to its placements"
        except Exception as e:
            parse_error = str(e)[:120]
        heads[fingerprint] = Head(
            fingerprint=fingerprint,
            block=block,
            uids=tuple(pgp.user_id_texts(block)),
            latest_self_signature=latest,
            placements=len(placements),
            parse_error=parse_error,
        )
    return heads


def _hint_matches(inserted: dict[str, str | None], heads: dict[str, Head], hints: frozenset[str]) -> list[str]:
    if not hints:
        return []
    matches = []
    for fingerprint in sorted(inserted):
        head = heads[fingerprint]
        if head.parse_error:
            continue
        key, _ = openpgp.composed.SignedPublicKey.from_armor(head.block)
        if util.openpgp_member_ids(key) & hints:
            matches.append(fingerprint)
    return matches


def _listed(
    listing: dict,
    history: pathlib.Path,
    switchover: int,
    committees: frozenset[str],
    projects: dict[str, str],
    blocks: Blocks,
    errors: collections.Counter,
) -> Listed:
    if listing["revision"] != switchover:
        raise ValueError(f"the listing is for r{listing['revision']}, not the pinned switchover r{switchover}")
    links = set()
    coverage: dict[str, Placements] = {}
    unresolved = []
    rejected = []
    for entry in listing["paths"]:
        committee, _reason = _resolve(entry["path"], committees, projects)
        if committee is None:
            unresolved.append(entry["path"])
            continue
        payload = (history / "blobs" / f"{entry['sha256']}.KEYS").read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"blob for {entry['path']} has digest {digest}, not {entry['sha256']}")
        text = payload.decode("utf-8", "replace")
        armored = _armored_blocks(text)
        begins = sum(1 for line in text.splitlines() if line.strip() == _ARMOR_BEGIN)
        if begins != len(armored):
            rejected.append(entry["path"])
        for block in armored:
            entries, failures = _block_certificates(block, blocks, errors)
            if failures or (not entries):
                rejected.append(entry["path"])
            for fingerprint, placements, _span in entries:
                links.add((committee, fingerprint))
                coverage[fingerprint] = coverage.get(fingerprint, frozenset()) | placements
    return Listed(links, coverage, unresolved, rejected)


def _manifest(
    args: argparse.Namespace,
    backfill: Backfill,
    heads: dict[str, Head],
    changes: list[UidChange],
    digests: dict[str, str],
    listed: Listed,
    inserted: dict[str, str | None],
    links_added: list[tuple[str, str]],
) -> dict:
    rows = [row for planned in backfill.rows.values() for row in planned]
    grades: collections.Counter = collections.Counter(
        row.confidence for row in rows if (row.kind == _KIND_ATR) and row.confidence
    )
    oversize = [
        head.fingerprint
        for head in heads.values()
        if (len(head.block) > keys_writer._MAX_CERTIFICATE_BYTES)
        or (head.placements > keys_writer._MAX_CERTIFICATE_PLACEMENTS)
    ]
    return {
        "observed": datetime.datetime.now(datetime.UTC).isoformat(),
        "version": metadata.version,
        "commit": metadata.commit,
        "switchover_revision": args.switchover,
        "archive_capture": {"date": _ARCHIVE_CAPTURE_DATE, "sha256": _ARCHIVE_CAPTURE_SHA256},
        "log_format": "1",
        "inputs_sha256": digests,
        "switchover_digest": _switchover_digest(inserted, links_added, listed),
        "inserted": inserted,
        "links_added": [f"{committee}:{fingerprint}" for committee, fingerprint in links_added],
        "unresolved_paths": listed.unresolved,
        "rejected_blocks": listed.rejected,
        "plan_digest": _plan_digest(backfill, heads),
        "deleted_fingerprints": list(_DELETED_FINGERPRINTS),
        "certificates": len(backfill.final),
        "rows": len(rows),
        "delta_bytes": backfill.delta_bytes,
        "rows_by_source": {_KIND_NAMES[k]: n for k, n in sorted(collections.Counter(r.kind for r in rows).items())},
        "events_considered": {_KIND_NAMES[k]: n for k, n in sorted(backfill.events_considered.items())},
        "block_errors": {error: count for error, count in sorted(backfill.block_errors.items())},
        "unreadable_heads": sorted(backfill.unreadable_heads),
        "grades": {grade: count for grade, count in sorted(grades.items())},
        "oversize_heads": sorted(oversize),
        "uid_changes": {
            "digest": _uid_digest(changes),
            "total": len(changes),
            "to_null": sorted(c.fingerprint for c in changes if c.value is None),
            "value_to_value": sorted(
                f"{c.fingerprint}:{c.previous}->{c.value}" for c in changes if c.previous and c.value
            ),
            "all": [
                {"fingerprint": c.fingerprint, "previous": c.previous, "value": c.value, "branch": c.branch}
                for c in sorted(changes, key=lambda c: c.fingerprint)
            ],
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill the certificate log, recompute every head, re-resolve every apache_uid, and"
        " import the certificates and links the KEYS files alive at the switchover hold, per"
        " dev/openpgp-backfill-plan.md phases 2 to 5. Reports by default; --apply writes the backfill,"
        " deletions, insertions, links and heads in one transaction and the uid sweep in a second,"
        " verifies, and emits a manifest."
    )
    parser.add_argument("inputs", type=pathlib.Path)
    parser.add_argument("switchover", type=int)
    parser.add_argument("--apply", action="store_true", help="Write to the database instead of only reporting.")
    parser.add_argument("--manifest", type=pathlib.Path, default=pathlib.Path("keys-backfill-manifest.json"))
    parser.add_argument(
        "--map-minimum",
        type=int,
        default=1000,
        help="Refuse to apply on a smaller email map; set from a known-good size, since a shrunken map"
        " silently leaves certificates unresolved rather than tripping the value-to-NULL guard.",
    )
    parser.add_argument(
        "--null-maximum", type=int, default=10, help="Refuse the uid sweep on more value-to-NULL changes."
    )
    return parser.parse_args()


def _primary_changes(certificates: list[sql.SigningCertificate], heads: dict[str, Head]) -> int:
    changed = 0
    for certificate in certificates:
        head = heads.get(certificate.fingerprint.lower())
        if head is None:
            continue
        primary = head.uids[0] if head.uids else None
        if primary != certificate.primary_declared_uid:
            changed += 1
    return changed


def _plan_digest(backfill: Backfill, heads: dict[str, Head]) -> str:
    digest = hashlib.sha256()
    for fingerprint in sorted(backfill.rows):
        for row in backfill.rows[fingerprint]:
            deletions, additions = pgp.delta_fragments(row.previous, row.result)
            for part in (
                row.fingerprint,
                str(row.seq),
                row.source,
                row.updated.isoformat(),
                row.actor,
                row.role.value,
            ):
                digest.update(part.encode("utf-8"))
                digest.update(b"\x00")
            for blob in (deletions, additions, row.raw):
                digest.update(blob or b"")
                digest.update(b"\x00")
    for fingerprint in sorted(heads):
        digest.update(heads[fingerprint].block.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _print_report(
    certificates: list[sql.SigningCertificate],
    backfill: Backfill,
    heads: dict[str, Head],
    changes: list[UidChange],
    automated_before: frozenset[str],
    map_size: int,
    problems: list[str],
) -> None:
    rows = [row for planned in backfill.rows.values() for row in planned]
    by_kind = collections.Counter(row.kind for row in rows)
    updates = [row.updated for row in rows]
    print(f"certificates {len(certificates)}; in scope {len(backfill.final)}; deleting {len(_DELETED_FINGERPRINTS)}")
    print(f"rows {len(rows)}; delta bytes {backfill.delta_bytes}; plan digest {_plan_digest(backfill, heads)}")
    for kind in sorted(by_kind):
        print(f"  {_KIND_NAMES[kind]}: {by_kind[kind]} rows from {backfill.events_considered[kind]} events")
    if updates:
        print(f"timeline {min(updates).date()} to {max(updates).date()}")
    for error, count in sorted(backfill.block_errors.items()):
        print(f"  block error x{count}: {error}")
    for fingerprint in backfill.unreadable_heads:
        print(f"  stored head unreadable, rebuilt from the sources alone: {fingerprint}")
    oversize = [h.fingerprint for h in heads.values() if len(h.block) > keys_writer._MAX_CERTIFICATE_BYTES]
    changed = _primary_changes(certificates, heads)
    print(f"heads rewritten {len(heads)}; primary uid changes {changed}; over the byte limit {len(oversize)}")
    for fingerprint in oversize:
        print(f"  over the byte limit: {fingerprint}")
    print(f"automated release signing committees before {len(automated_before)}")
    _print_uid_report(changes, map_size)
    for problem in problems:
        print(f"ERROR! {problem}")


def _print_uid_report(changes: list[UidChange], map_size: int) -> None:
    to_value = [c for c in changes if c.previous is None]
    value_to_value = [c for c in changes if c.previous and c.value]
    to_null = [c for c in changes if c.previous and (c.value is None)]
    branches = collections.Counter(change.branch for change in changes)
    print(f"email map size {map_size}; set --map-minimum from a known-good size")
    print(f"uid plan digest {_uid_digest(changes)}")
    print("uid change branches: " + ", ".join(f"{branch} {n}" for branch, n in sorted(branches.items())))
    print(
        f"uid changes {len(changes)}: null-to-value {len(to_value)},"
        f" value-to-value {len(value_to_value)}, value-to-null {len(to_null)}"
    )
    for change in value_to_value:
        print(f"  {change.fingerprint}: {change.previous} -> {change.value} ({change.branch})")
    for change in to_null:
        print(f"  {change.fingerprint}: {change.previous} -> NULL")


async def _registry(data: db.Session) -> tuple[frozenset[str], dict[str, str], frozenset[tuple[str, str]]]:
    via = sql.validate_instrumented_attribute
    committee_rows = await data.execute(sqlmodel.select(via(sql.Committee.key)))
    project_rows = await data.execute(sqlmodel.select(via(sql.Project.key), via(sql.Project.committee_key)))
    link_rows = await data.execute(sqlmodel.select(via(sql.KeyLink.committee_key), via(sql.KeyLink.key_fingerprint)))
    return (
        frozenset(committee_rows.scalars().all()),
        {key: committee for key, committee in project_rows.all()},
        frozenset((committee, fingerprint.lower()) for committee, fingerprint in link_rows.all()),
    )


def _resolve(path: str, committees: frozenset[str], projects: dict[str, str]) -> tuple[str | None, str]:
    parts = path.removeprefix("/release/").split("/")
    top, rest = parts[0], tuple(parts[1:-1])
    names = _candidate_names(top, rest)
    if top not in registry.STANDING_COMMITTEES:
        for name in names:
            if name.startswith(f"{top}-") and (projects.get(name) == top):
                return top, f"subproject {name}"
    for name in names:
        if (name in committees) and (name not in registry.STANDING_COMMITTEES):
            redirect = projects.get(name, name)
            if (redirect != name) and (redirect in committees) and (redirect not in registry.STANDING_COMMITTEES):
                return redirect, f"project {name}"
            return name, "committee"
    for name in names:
        committee = projects.get(name)
        if committee is None:
            continue
        if (committee in committees) and (committee not in registry.STANDING_COMMITTEES):
            return committee, f"project {name}"
    return None, "none"


async def _run(args: argparse.Namespace) -> None:
    await db.init_database_for_worker()
    blocks: Blocks = {}
    errors: collections.Counter = collections.Counter()
    history = args.inputs / "svn-keys-history"
    raws = {
        "index.json": (history / "index.json").read_bytes(),
        "release-keys.json": (history / "release-keys.json").read_bytes(),
        "submissions.jsonl": (args.inputs / "submissions.jsonl").read_bytes(),
    }
    digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in raws.items()}
    svn_events = _svn_events(json.loads(raws["index.json"]), history, args.switchover, blocks, errors)
    archive_events = _archive_events(args.inputs, blocks, errors)
    atr_events, timeless = _submission_events(raws["submissions.jsonl"].decode("utf-8"), blocks, errors)
    async with db.session() as data:
        certificates = await _certificates(data)
        row_count = await data.execute(sqlalchemy.select(sqlalchemy.func.count()).select_from(sql.KeyAttestable))
        existing_rows = row_count.scalar_one()
        user_rows = await data.execute(sqlmodel.select(sql.validate_instrumented_attribute(sql.User.asfuid)))
        users = frozenset(user_rows.scalars().all())
        artifacts = await _deleted_artifact_count(data)
        automated_before = await interaction.automated_release_signing_committees(data)
        marker = await data.ns_text_get("openpgp-log", "format")
        mapping = await data.ns_text_get("openpgp-log", f"archive:{_ARCHIVE_CAPTURE_DATE}")
        committees, projects, existing_links = await _registry(data)
        hint_rows = await data.execute(sqlmodel.select(sql.validate_instrumented_attribute(sql.SignatureHint.hint)))
        hints = frozenset(hint_rows.scalars().all())
    listed = _listed(
        json.loads(raws["release-keys.json"]), history, args.switchover, committees, projects, blocks, errors
    )
    problems_stored = []
    if marker is not None and (marker != "1"):
        problems_stored.append(f"openpgp-log format marker already set to {marker!r}")
    if mapping is not None and (mapping != _ARCHIVE_CAPTURE_SHA256):
        problems_stored.append(f"openpgp-log archive mapping already set to {mapping!r}")
    problems = _sanity(certificates, existing_rows, artifacts) + problems_stored
    stored_fingerprints = frozenset(c.fingerprint.lower() for c in certificates)
    scope = (stored_fingerprints | frozenset(listed.coverage)) - frozenset(_DELETED_FINGERPRINTS)
    unreadable, uncovered, head_problems = _stored_head_check(certificates)
    problems += head_problems
    backfill = _backfill([*svn_events, *archive_events, *atr_events], scope, timeless, unreadable, errors)
    problems += backfill.problems
    problems += _uncovered_problems(uncovered, backfill.final)
    problems += [
        f"listed certificate {fingerprint} has placements no source explains"
        for fingerprint, placements in sorted(listed.coverage.items())
        if not (placements <= backfill.final.get(fingerprint, frozenset()))
    ]
    heads = _heads(backfill.final)
    failures = [h for h in heads.values() if h.parse_error]
    problems += [f"head for {head.fingerprint} fails to serialise: {head.parse_error}" for head in failures]
    problems += _underivable_signing_keys(heads)
    lookup = cache.email_uid_view()
    changes = _sweep(certificates, heads, users, lookup)
    inserted = {
        fingerprint: keys_writer._resolved_apache_uid(list(heads[fingerprint].uids), None, users, lookup)[0]
        for fingerprint in sorted(frozenset(listed.coverage) - stored_fingerprints - frozenset(_DELETED_FINGERPRINTS))
        if fingerprint in heads
    }
    links_added = sorted(
        (committee, fingerprint)
        for committee, fingerprint in listed.links
        if (fingerprint in scope) and ((committee, fingerprint) not in existing_links)
    )
    automated_links = frozenset(
        committee
        for committee, fingerprint in links_added
        if (fingerprint in heads) and heads[fingerprint].uids and _automated_uid(heads[fingerprint].uids[0])
    )
    problems += [
        f"inserted certificate {fingerprint} matches a signature hint; import it through the writer instead"
        for fingerprint in _hint_matches(inserted, heads, hints)
    ]
    _print_report(certificates, backfill, heads, changes, automated_before, len(lookup), problems)
    print(
        f"listed {len(listed.coverage)} certificates; inserting {len(inserted)}; links to add {len(links_added)};"
        f" unresolved paths {len(listed.unresolved)}; rejected blocks {len(listed.rejected)}"
    )
    print(f"switchover digest {_switchover_digest(inserted, links_added, listed)}")
    if automated_links:
        print(f"automation additions via new links: {sorted(automated_links)}")
    if problems:
        sys.exit(1)
    if not args.apply:
        return
    if len(lookup) < args.map_minimum:
        print(f"refusing to apply: the email map holds {len(lookup)} entries, below {args.map_minimum}")
        sys.exit(1)
    to_null = [c for c in changes if (c.previous is not None) and (c.value is None)]
    if len(to_null) > args.null_maximum:
        print(f"refusing to apply: {len(to_null)} value-to-NULL changes, above {args.null_maximum}")
        sys.exit(1)
    manifest = json.dumps(_manifest(args, backfill, heads, changes, digests, listed, inserted, links_added), indent=2)
    args.manifest.write_text('{"incomplete": true}\n')
    listener = _setup_audit_logging()
    try:
        await _write(backfill, heads, automated_before, automated_links, inserted, links_added)
        await _write_sweep(changes)
    finally:
        listener.stop()
    args.manifest.write_text(manifest + "\n")
    print(f"manifest written to {args.manifest}")


def _sanity(certificates: list[sql.SigningCertificate], existing_rows: int, artifacts: int) -> list[str]:
    problems = []
    if existing_rows:
        problems.append(f"keyattestable already holds {existing_rows} rows; restore the snapshot and run again")
    fingerprints = {c.fingerprint.lower() for c in certificates}
    for fingerprint in _DELETED_FINGERPRINTS:
        if fingerprint not in fingerprints:
            problems.append(f"certificate to delete is missing: {fingerprint}")
    if artifacts:
        problems.append(f"a certificate to delete is referenced by {artifacts} artifacts")
    soft_deleted = [c.fingerprint for c in certificates if c.deleted is not None]
    if soft_deleted:
        problems.append(f"soft-deleted certificates exist, but the backfill writes no delete rows: {soft_deleted}")
    return problems


def _setup_audit_logging() -> logging.handlers.QueueListener:
    conf = config.get()
    shared_processors = loggers.shared_processors()
    loggers.configure_structlog(shared_processors)
    return loggers.setup_dedicated_file_logger("atr.storage.audit", conf.STORAGE_AUDIT_LOG_FILE, shared_processors)


def _stored_head_check(
    certificates: list[sql.SigningCertificate],
) -> tuple[list[str], dict[str, Placements], list[str]]:
    unreadable = []
    stored: dict[str, Placements] = {}
    problems = []
    for certificate in certificates:
        fingerprint = certificate.fingerprint.lower()
        if fingerprint in _DELETED_FINGERPRINTS:
            continue
        armored = certificate.ascii_armored_key
        text = armored if isinstance(armored, str) else armored.decode("utf-8", "replace")
        try:
            stored[fingerprint] = _head_placements(text)
        except Exception as e:
            unreadable.append(fingerprint)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if _EXPECTED_UNREADABLE_HEADS.get(fingerprint) != digest:
                problems.append(f"unexpected unreadable stored head: {fingerprint} digest {digest}: {str(e)[:80]}")
    return unreadable, stored, problems


def _submission_events(text: str, blocks: Blocks, errors: collections.Counter) -> tuple[list[Event], list[Timeless]]:
    events = []
    timeless = []
    for line in text.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not str(record.get("content_status", "")).startswith("recovered-exact"):
            continue
        fingerprint = (record.get("fingerprint") or "").lower()
        content = record.get("content")
        if not (fingerprint and content):
            continue
        matching = [
            (placements, span)
            for block in _armored_blocks(content)
            for found, placements, span in _block_certificates(block, blocks, errors)[0]
            if found == fingerprint
        ]
        if not matching:
            errors["submission: no matching span"] += 1
            continue
        placements = frozenset().union(*(p for p, _ in matching))
        raw = b"".join(span for _, span in matching)
        when = record.get("time")
        channel = str(record.get("channel", ""))
        event_name = str(record.get("event", ""))
        if not when:
            timeless.append(Timeless(fingerprint, channel, event_name, placements))
            continue
        actor = record.get("actor") or _ACTOR_OVERRIDES.get((fingerprint, when))
        request_id = record.get("request_id")
        source = f"web:{request_id}" if request_id else f"recovered:{channel}/{event_name}"
        events.append(
            Event(
                updated=_utc(when),
                kind=_KIND_ATR,
                path=fingerprint,
                revision=0,
                source=source,
                fingerprint=fingerprint,
                actor=actor,
                role=sql.KeyRole.USER,
                placements=placements,
                raw=raw,
                confidence=record.get("confidence"),
            )
        )
    return events, timeless


def _svn_events(
    index: list[dict], history: pathlib.Path, switchover: int, blocks: Blocks, errors: collections.Counter
) -> list[Event]:
    top = max(entry["rev"] for entry in index)
    if top != switchover:
        raise ValueError(f"the index reaches r{top}, not the pinned switchover r{switchover}; refresh the fetch first")
    events = []
    for entry in index:
        if not entry.get("sha256"):
            continue
        author = entry.get("author")
        if not author:
            raise ValueError(f"revision without an author: {entry['path']}@{entry['rev']}")
        payload = (history / "blobs" / f"{entry['sha256']}.KEYS").read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"blob for {entry['path']}@{entry['rev']} has digest {digest}, not {entry['sha256']}")
        text = payload.decode("utf-8", "replace")
        updated = _utc(entry["date"])
        for fingerprint, placements in _file_certificates(text, blocks, errors).items():
            events.append(
                Event(
                    updated=updated,
                    kind=_KIND_SVN,
                    path=entry["path"],
                    revision=entry["rev"],
                    source=f"svn:{entry['path']}@{entry['rev']}",
                    fingerprint=fingerprint,
                    actor=author,
                    role=sql.KeyRole.SERVICE,
                    placements=placements,
                    raw=None,
                    confidence=None,
                )
            )
    return events


def _sweep(
    certificates: list[sql.SigningCertificate],
    heads: dict[str, Head],
    users: frozenset[str],
    lookup: cache.EmailUidLookup,
) -> list[UidChange]:
    changes = []
    for certificate in certificates:
        fingerprint = certificate.fingerprint.lower()
        head = heads.get(fingerprint)
        if head is None:
            continue
        value, branch = keys_writer._resolved_apache_uid(list(head.uids), certificate.apache_uid, users, lookup)
        if value != certificate.apache_uid:
            changes.append(UidChange(fingerprint, certificate.apache_uid, value, branch))
    return changes


def _switchover_digest(inserted: dict[str, str | None], links_added: list[tuple[str, str]], listed: Listed) -> str:
    digest = hashlib.sha256()
    parts = [f"inserted:{fingerprint}:{inserted[fingerprint] or ''}" for fingerprint in sorted(inserted)]
    parts += [f"link:{committee}:{fingerprint}" for committee, fingerprint in links_added]
    parts += [f"unresolved:{path}" for path in listed.unresolved]
    parts += [f"rejected:{path}" for path in sorted(listed.rejected)]
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _uncovered_problems(stored: dict[str, Placements], final: dict[str, Placements]) -> list[str]:
    problems = []
    for fingerprint, placements in stored.items():
        missing = placements - final.get(fingerprint, frozenset())
        if missing:
            problems.append(f"the stored head for {fingerprint} holds {len(missing)} placements no source explains")
    return problems


def _underivable_signing_keys(heads: dict[str, Head]) -> list[str]:
    problems = []
    for head in heads.values():
        try:
            derived = keys_writer._signing_key_rows(head.fingerprint, head.block)
        except Exception as e:
            problems.append(f"signing keys underivable for {head.fingerprint}: {str(e)[:80]}")
            continue
        if derived is None:
            problems.append(f"signing keys underivable for {head.fingerprint}: head holds another key")
    return problems


def _uid_digest(changes: list[UidChange]) -> str:
    digest = hashlib.sha256()
    for change in sorted(changes, key=lambda c: c.fingerprint):
        for part in (change.fingerprint, change.previous or "", change.value or "", change.branch):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
    return digest.hexdigest()


def _utc(value: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC)


async def _verify_signing_keys(data: db.Session, heads: dict[str, Head]) -> None:
    stored_keys = {}
    for row in (await data.execute(sqlmodel.select(sql.SigningKey))).scalars().all():
        stored_keys[row.fingerprint.lower()] = row
    for head in heads.values():
        derived = keys_writer._signing_key_rows(head.fingerprint, head.block)
        if derived is None:
            raise RuntimeError(f"signing keys underivable for {head.fingerprint} at verification")
        for expected in derived:
            row = stored_keys.get(expected["fingerprint"].lower())
            if row is None:
                raise RuntimeError(f"derived signing key missing from the database: {expected['fingerprint']}")
            mismatched = [column for column, value in expected.items() if getattr(row, column) != value]
            if mismatched:
                raise RuntimeError(f"signing key {expected['fingerprint']} differs on {mismatched}")


async def _verify_database(data: db.Session, scope: frozenset[str], heads: dict[str, Head]) -> None:
    via = sql.validate_instrumented_attribute
    await _verify_signing_keys(data, heads)
    result = await data.execute(
        sqlmodel.select(sql.KeyAttestable).order_by(via(sql.KeyAttestable.fingerprint), via(sql.KeyAttestable.seq))
    )
    chains: dict[str, list[sql.KeyAttestable]] = collections.defaultdict(list)
    for row in result.scalars().all():
        chains[row.fingerprint].append(row)
    if frozenset(chains) != scope:
        raise RuntimeError(f"chains do not match the scope: {sorted(set(chains) ^ scope)[:5]}")
    certificates = {c.fingerprint.lower(): c for c in await _certificates(data)}
    for fingerprint, rows in chains.items():
        if [row.seq for row in rows] != list(range(1, len(rows) + 1)):
            raise RuntimeError(f"the chain for {fingerprint} is not contiguous")
        fold = pgp.fold_deltas((row.deletions, row.additions) for row in rows)
        armored = certificates[fingerprint].ascii_armored_key
        block = armored if isinstance(armored, str) else armored.decode("utf-8", "replace")
        if pgp.certificate_placements(block) != fold:
            raise RuntimeError(f"the head for {fingerprint} does not equal its fold")
    print(f"verified {len(chains)} chains against their heads")


async def _write(
    backfill: Backfill,
    heads: dict[str, Head],
    automated_before: frozenset[str],
    automated_links: frozenset[str],
    inserted: dict[str, str | None],
    links_added: list[tuple[str, str]],
) -> None:
    via = sql.validate_instrumented_attribute
    authorisation = await principal.Authorisation(constants.SYSTEM_SERVICE_UID)
    async with db.session() as data:
        await data.begin_immediate()
        writer = storage.Write(authorisation, data).as_foundation_committer().keys
        await data.execute(
            sqlmodel.delete(sql.KeyLink).where(via(sql.KeyLink.key_fingerprint).in_(_DELETED_FINGERPRINTS))
        )
        await data.execute(
            sqlmodel.delete(sql.SigningCertificate).where(
                via(sql.SigningCertificate.fingerprint).in_(_DELETED_FINGERPRINTS)
            )
        )
        for fingerprint in sorted(inserted):
            head = heads[fingerprint]
            data.add(
                sql.SigningCertificate(
                    fingerprint=fingerprint,
                    latest_self_signature=head.latest_self_signature,
                    primary_declared_uid=head.uids[0] if head.uids else None,
                    secondary_declared_uids=list(head.uids[1:]),
                    apache_uid=inserted[fingerprint],
                    ascii_armored_key=head.block,
                )
            )
        await data.flush()
        for committee_key, fingerprint in links_added:
            data.add(sql.KeyLink(committee_key=committee_key, key_fingerprint=fingerprint))
        await data.flush()
        for fingerprint in sorted(backfill.rows):
            for row in backfill.rows[fingerprint]:
                writer._append_certificate_row(
                    row.fingerprint,
                    row.seq,
                    sql.KeyOperation.REVISE,
                    row.source,
                    row.raw,
                    row.previous,
                    row.result,
                    actor=row.actor,
                    role=row.role,
                    updated=row.updated,
                )
        certificates = await _certificates(data)
        for certificate in certificates:
            head = heads[certificate.fingerprint.lower()]
            certificate.ascii_armored_key = head.block
            certificate.latest_self_signature = head.latest_self_signature
            certificate.primary_declared_uid = head.uids[0] if head.uids else None
            certificate.secondary_declared_uids = list(head.uids[1:])
        await keys_writer._sync_signing_keys(data, certificates)
        await data.ns_text_set("openpgp-log", "format", "1", commit=False)
        await data.ns_text_set("openpgp-log", f"archive:{_ARCHIVE_CAPTURE_DATE}", _ARCHIVE_CAPTURE_SHA256, commit=False)
        await _verify_database(data, frozenset(backfill.final), heads)
        automated_after = await interaction.automated_release_signing_committees(data)
        removed = automated_before - automated_after
        unexpected = automated_after - automated_before - automated_links
        if removed or unexpected:
            raise RuntimeError(
                f"the automated release signing set changed:"
                f" removed {sorted(removed)}, unexplained {sorted(unexpected)}"
            )
        await data.commit()
    for fingerprint in _DELETED_FINGERPRINTS:
        storage.audit(action="key_hard_delete", fingerprint=fingerprint, phase="backfill")
    for fingerprint in sorted(inserted):
        storage.audit(
            action="key_insert", fingerprint=fingerprint, key_apache_uid=inserted[fingerprint], phase="backfill"
        )
    for committee_key, fingerprint in links_added:
        storage.audit(
            action="key_associate_committee", fingerprint=fingerprint, committee_key=committee_key, phase="backfill"
        )
    print(
        f"wrote {sum(len(r) for r in backfill.rows.values())} rows; rewrote {len(certificates)} heads;"
        f" inserted {len(inserted)} certificates; added {len(links_added)} links"
    )


async def _write_sweep(changes: list[UidChange]) -> None:
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        await data.begin_immediate()
        for change in changes:
            await data.execute(
                sqlmodel.update(sql.SigningCertificate)
                .where(via(sql.SigningCertificate.fingerprint) == change.fingerprint)
                .values(apache_uid=change.value)
            )
        await data.commit()
    for change in changes:
        storage.audit(
            action="key_resolve_apache_uid",
            fingerprint=change.fingerprint,
            previous=change.previous,
            value=change.value,
            branch=change.branch,
        )
    print(f"resolved {len(changes)} apache_uid changes")


if __name__ == "__main__":
    main()
