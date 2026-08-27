#!/usr/bin/env python3
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

# Usage: uv run --frozen python3 scripts/keys_rehome.py ASF_UID DECISIONS.jsonl [--apply] [--allow-publish]
#
# Re-homes the catalogue seed's key-to-committee links according to a reviewed decisions file, one
# JSON record per seed link with "committee", "fingerprint", "action" and "targets" (produced by
# dev/keys_rehome_survey.py). Every target is linked first; then every source link whose action is
# "drop" or "rehome", and whose targets are all linked, is removed, through the keys writer as
# committee admin, so each change is audited. Records whose premise no longer holds in the database
# are skipped and reported. Reports by default; --apply acts and then verifies the end state.

import argparse
import asyncio
import collections
import dataclasses
import json
import logging.handlers
import pathlib
import sys
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sqlmodel

import atr.config as config
import atr.db as db
import atr.loggers as loggers
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.storage.outcome as outcome

_ACTIONS: Final[frozenset[str]] = frozenset({"drop", "keep", "keep+add", "rehome"})
_REMOVING_ACTIONS: Final[frozenset[str]] = frozenset({"drop", "rehome"})
_TARGETED_ACTIONS: Final[frozenset[str]] = frozenset({"keep+add", "rehome"})


@dataclasses.dataclass(frozen=True)
class Decision:
    committee: str
    fingerprint: str
    action: str
    targets: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Plan:
    adds: dict[str, list[str]]
    removals: dict[str, list[str]]
    planned: list[Decision]
    skipped: list[str]
    publishing: list[str]


@dataclasses.dataclass(frozen=True)
class State:
    committees: frozenset[str]
    fingerprints: frozenset[str]
    links: frozenset[tuple[str, str]]
    publishing: frozenset[str]


def main() -> None:
    asyncio.run(_run(_parse_args()))


async def _associate(asf_uid: str, committee: str, fingerprints: list[str]) -> int:
    failed = 0
    unpublished = 0
    async with storage.write(asf_uid) as write:
        waca = write.as_committee_admin(committee)
        for fingerprint in fingerprints:
            linked = await waca.keys.associate_fingerprint(fingerprint)
            error = linked.error_or_none()
            if error is not None:
                failed += 1
                print(f"ERROR! associate {committee} {fingerprint}: {error!r}", flush=True)
                continue
            unpublished += _publication_errors(committee, linked.result_or_raise().publication)
    print(f"{committee}: linked {len(fingerprints) - failed} of {len(fingerprints)}", flush=True)
    return failed + unpublished


async def _associate_all(asf_uid: str, adds: dict[str, list[str]]) -> int:
    errors = 0
    for committee, fingerprints in sorted(adds.items()):
        errors += await _associate(asf_uid, committee, fingerprints)
    return errors


async def _dissociate(asf_uid: str, committee: str, fingerprints: list[str]) -> int:
    async with storage.write(asf_uid) as write:
        waca = write.as_committee_admin(committee)
        removed, publication = await waca.keys.dissociate_fingerprints(fingerprints)
    errors = len(fingerprints) - len(removed)
    if publication is not None:
        errors += _publication_errors(committee, publication)
    print(f"{committee}: unlinked {len(removed)} of {len(fingerprints)}", flush=True)
    return errors


async def _dissociate_all(asf_uid: str, removals: dict[str, list[str]]) -> int:
    errors = 0
    for committee, fingerprints in sorted(removals.items()):
        errors += await _dissociate(asf_uid, committee, fingerprints)
    return errors


def _load_decisions(path: pathlib.Path) -> list[Decision]:
    decisions = []
    seen = set()
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        decision = Decision(
            record["committee"],
            record["fingerprint"].lower(),
            record["action"],
            tuple(sorted(set(record["targets"]))),
        )
        problem = _record_problem(decision, seen)
        if problem is not None:
            raise ValueError(f"{path}:{number}: {problem}")
        seen.add((decision.committee, decision.fingerprint))
        decisions.append(decision)
    removing = {(d.committee, d.fingerprint) for d in decisions if d.action in _REMOVING_ACTIONS}
    targeted = {(target, d.fingerprint) for d in decisions for target in d.targets}
    conflicts = sorted(removing & targeted)
    if conflicts:
        raise ValueError(f"{path}: links both removed and targeted: {conflicts[:5]}")
    return decisions


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-home the catalogue seed's key links from a reviewed decisions file."
        " Reports by default; --apply links every target, then removes every dropped or re-homed source link"
        " whose targets are all linked, through the keys writer as committee admin, and verifies the end state."
    )
    parser.add_argument("asf_uid")
    parser.add_argument("decisions", type=pathlib.Path)
    parser.add_argument("--apply", action="store_true", help="Act on the plan instead of only reporting it.")
    parser.add_argument(
        "--allow-publish",
        action="store_true",
        help="Proceed even if an affected committee still has automated KEYS publishing on.",
    )
    return parser.parse_args()


def _plan(decisions: list[Decision], state: State) -> Plan:
    adds: dict[str, list[str]] = collections.defaultdict(list)
    planned = []
    skipped = []
    for decision in decisions:
        reason = _premise_failure(decision, state)
        if reason is not None:
            skipped.append(f"{decision.committee} {decision.fingerprint}: {reason}")
            continue
        planned.append(decision)
        for target in decision.targets:
            if (target, decision.fingerprint) not in state.links:
                adds[target].append(decision.fingerprint)
    expected = state.links | {(target, decision.fingerprint) for decision in planned for target in decision.targets}
    removals = _removals(planned, expected)
    publishing = sorted((set(adds) | set(removals)) & state.publishing)
    return Plan(dict(adds), removals, planned, skipped, publishing)


def _premise_failure(decision: Decision, state: State) -> str | None:
    if decision.fingerprint not in state.fingerprints:
        return "certificate missing or deleted"
    if (decision.committee, decision.fingerprint) not in state.links:
        return "source link already gone"
    missing = [target for target in decision.targets if target not in state.committees]
    if missing:
        return f"target committee missing: {', '.join(missing)}"
    return None


def _print_plan(plan: Plan, state: State) -> None:
    for reason in plan.skipped:
        print(f"skip {reason}")
    added = sum(len(fingerprints) for fingerprints in plan.adds.values())
    removed = sum(len(fingerprints) for fingerprints in plan.removals.values())
    print(
        f"links now {len(state.links)}; to add {added} across {len(plan.adds)} committees;"
        f" to remove {removed} across {len(plan.removals)} committees; skipped {len(plan.skipped)}"
    )
    for committee, fingerprints in sorted(plan.adds.items()):
        print(f"  + {committee}: {len(fingerprints)}")
    for committee, fingerprints in sorted(plan.removals.items()):
        print(f"  - {committee}: {len(fingerprints)}")
    if plan.publishing:
        print(f"automated KEYS publishing still on for: {', '.join(plan.publishing)}")


def _publication_errors(committee: str, publication: outcome.Outcome[datatypes.KeysPublish]) -> int:
    error = publication.error_or_none()
    if error is not None:
        print(f"ERROR! {committee} KEYS publication: {error!r}", flush=True)
        return 1
    result = publication.result_or_raise()
    if result is not datatypes.KeysPublish.AUTOMATION_DISABLED:
        print(f"{committee} KEYS publication: {result.name}", flush=True)
    return 0


def _record_problem(decision: Decision, seen: set[tuple[str, str]]) -> str | None:
    if decision.action not in _ACTIONS:
        return f"unknown action {decision.action!r}"
    if bool(decision.targets) != (decision.action in _TARGETED_ACTIONS):
        return f"action {decision.action!r} with targets {list(decision.targets)}"
    if decision.committee in decision.targets:
        return "target is the source committee"
    if (decision.committee, decision.fingerprint) in seen:
        return "duplicate record"
    return None


def _removals(planned: list[Decision], links: frozenset[tuple[str, str]]) -> dict[str, list[str]]:
    removals: dict[str, list[str]] = collections.defaultdict(list)
    for decision in planned:
        if decision.action not in _REMOVING_ACTIONS:
            continue
        if all(((target, decision.fingerprint) in links) for target in decision.targets):
            removals[decision.committee].append(decision.fingerprint)
    return dict(removals)


async def _run(args: argparse.Namespace) -> None:
    await db.init_database_for_worker()
    decisions = _load_decisions(args.decisions)
    async with db.session() as data:
        state = await _state(data)
    plan = _plan(decisions, state)
    _print_plan(plan, state)
    if not args.apply:
        return
    if plan.publishing and (not args.allow_publish):
        print("refusing to apply while automated KEYS publishing is on; run phase 0 first or pass --allow-publish")
        sys.exit(1)
    listener = _setup_audit_logging()
    try:
        errors = await _associate_all(args.asf_uid, plan.adds)
        async with db.session() as data:
            linked = (await _state(data)).links
        errors += await _dissociate_all(args.asf_uid, _removals(plan.planned, linked))
    finally:
        listener.stop()
    async with db.session() as data:
        mismatches = _verify(plan.planned, await _state(data))
    for mismatch in mismatches:
        print(f"mismatch {mismatch}")
    print(f"errors {errors}; mismatches {len(mismatches)}")
    if errors or mismatches:
        sys.exit(1)


def _setup_audit_logging() -> logging.handlers.QueueListener:
    conf = config.get()
    shared_processors = loggers.shared_processors()
    loggers.configure_structlog(shared_processors)
    return loggers.setup_dedicated_file_logger("atr.storage.audit", conf.STORAGE_AUDIT_LOG_FILE, shared_processors)


async def _state(data: db.Session) -> State:
    via = sql.validate_instrumented_attribute
    committees = await data.execute(sqlmodel.select(via(sql.Committee.key), via(sql.Committee.keys_mode)))
    committee_rows = committees.all()
    fingerprints = await data.execute(
        sqlmodel.select(via(sql.SigningCertificate.fingerprint)).where(via(sql.SigningCertificate.deleted).is_(None))
    )
    links = await data.execute(sqlmodel.select(via(sql.KeyLink.committee_key), via(sql.KeyLink.key_fingerprint)))
    return State(
        frozenset(key for key, _ in committee_rows),
        frozenset(fingerprint.lower() for fingerprint in fingerprints.scalars().all()),
        frozenset((committee, fingerprint.lower()) for committee, fingerprint in links.all()),
        frozenset(key for key, mode in committee_rows if mode == sql.KeysMode.AUTOMATIC),
    )


def _verify(decisions: list[Decision], state: State) -> list[str]:
    mismatches = []
    for decision in decisions:
        source_present = (decision.committee, decision.fingerprint) in state.links
        if source_present == (decision.action in _REMOVING_ACTIONS):
            expected = "absent" if (decision.action in _REMOVING_ACTIONS) else "present"
            mismatches.append(f"{decision.committee} {decision.fingerprint}: source link should be {expected}")
        for target in decision.targets:
            if (target, decision.fingerprint) not in state.links:
                mismatches.append(f"{decision.committee} {decision.fingerprint}: not linked to {target}")
    return mismatches


if __name__ == "__main__":
    main()
