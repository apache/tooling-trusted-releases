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

# Usage: poetry run python3 scripts/keys_import.py

import argparse
import asyncio
import collections
import contextlib
import dataclasses
import os
import pathlib
import sys
import time
import traceback
from typing import TYPE_CHECKING

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import openpgp.composed
import sqlmodel

import atr.cache as cache
import atr.config as config
import atr.db as db
import atr.models.sql as sql
import atr.pgp as pgp
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.storage.outcome as outcome
import atr.storage.writers.keys as keys_writer
import atr.util as util

if TYPE_CHECKING:
    from types import TracebackType


def find_project_root() -> pathlib.Path:
    current = pathlib.Path(__file__).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "atr").is_dir():
            return candidate
    return current.parent


PROJECT_ROOT = find_project_root()


@dataclasses.dataclass(frozen=True)
class Selection:
    fingerprint: str
    action: str
    armored: str
    note: str
    republishes: tuple[str, ...] = ()
    actionable: bool = True


def is_atr_path(path: str) -> bool:
    try:
        resolved = pathlib.Path(path).resolve()
    except OSError:
        return False
    if any((part == ".venv") for part in resolved.parts):
        return False
    try:
        relative = resolved.relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    return "atr" in relative.parts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import committee KEYS files from downloads.apache.org."
        " Reports what would be imported unless --apply is given; --apply imports the selection and republishes"
        " the affected KEYS files. Keys with no row, and keys with a row but no link to the committee, are always"
        " selected; deleted keys only with --allow-undelete; linked keys whose canonical block or metadata differs"
        " only with --allow-refresh.",
    )
    parser.add_argument("asf_uid")
    parser.add_argument(
        "--apply", action="store_true", help="Import the selection and republish the affected KEYS files."
    )
    parser.add_argument("--committee", action="append", default=[], metavar="KEY", help="Restrict to a committee.")
    parser.add_argument(
        "--allow-refresh", action="store_true", help="Also select linked keys whose canonical copy differs."
    )
    parser.add_argument(
        "--allow-undelete", action="store_true", help="Also select deleted keys still in the canonical file."
    )
    return parser.parse_args()


async def plan_committee(
    data: db.Session,
    committee_key: str,
    keys_file_text: str,
    links: dict[str, set[str]],
    args: argparse.Namespace,
) -> list[Selection]:
    canonical = _canonical_certificates(keys_file_text)
    via = sql.validate_instrumented_attribute
    rows = await data.execute(
        sqlmodel.select(sql.SigningCertificate).where(via(sql.SigningCertificate.fingerprint).in_(sorted(canonical)))
    )
    stored = {row.fingerprint.lower(): row for row in rows.scalars().all()}
    selections = []
    for fingerprint, armors in canonical.items():
        row = stored.get(fingerprint)
        linked = links.get(fingerprint, set())
        effective = _effective_armor(fingerprint, armors)
        canonical_text = "\n\n".join(armors)
        if row is None:
            selections.append(_canonical_selection(fingerprint, "import", canonical_text, "no row", effective))
        elif row.deleted is not None:
            if args.allow_undelete:
                others = tuple(sorted(linked - {committee_key}))
                note = f"deleted {row.deleted:%Y-%m-%d}"
                if args.allow_refresh:
                    selections.append(
                        _canonical_selection(fingerprint, "undelete", canonical_text, note, effective, others)
                    )
                else:
                    selections.append(Selection(fingerprint, "undelete", _text(row.ascii_armored_key), note, others))
        elif committee_key not in linked:
            selections.append(Selection(fingerprint, "link", _text(row.ascii_armored_key), "row exists, not linked"))
        elif args.allow_refresh:
            if effective is None:
                selections.append(_canonical_selection(fingerprint, "refresh", canonical_text, "", None))
            elif (note := _refresh_note(fingerprint, row, effective, sorted(linked - {committee_key}))) is not None:
                selections.append(Selection(fingerprint, "refresh", canonical_text, note))
    return selections


def print_and_flush(message: str) -> None:
    print(message)
    sys.stdout.flush()


def release_target_refused() -> bool:
    return util.svn_publish_target() is util.SvnPublishTarget.RELEASE


def format_exception_location(exc: BaseException) -> str:
    tb = exc.__traceback__
    frames: list[TracebackType] = []
    while tb is not None:
        frames.append(tb)
        tb = tb.tb_next
    if not frames:
        return f"{type(exc).__name__}: {exc}"

    chosen_tb = None
    for frame_tb in reversed(frames):
        filename = frame_tb.tb_frame.f_code.co_filename
        if is_atr_path(filename):
            chosen_tb = frame_tb
            break
    if chosen_tb is None:
        chosen_tb = frames[-1]

    frame = chosen_tb.tb_frame
    filename_path = pathlib.Path(frame.f_code.co_filename).resolve()
    try:
        filename_relative = filename_path.relative_to(PROJECT_ROOT)
    except ValueError:
        filename_relative = filename_path.name
    filename = str(filename_relative)
    lineno = chosen_tb.tb_lineno
    func = frame.f_code.co_name
    return f"{type(exc).__name__} at {filename}:{lineno} in {func}: {exc}"


def log_outcome_errors(outcomes: outcome.List[datatypes.Key], committee_key: str) -> None:
    for error in outcomes.errors():
        fingerprint = "unknown"
        detail_exception: BaseException = error
        if isinstance(error, datatypes.PublicKeyError):
            fingerprint = error.key.key_model.fingerprint
            detail_exception = error.original_error
        elif isinstance(error, BaseException):
            detail_exception = error
        else:
            print_and_flush(f"ERROR! fingerprint={fingerprint} committee={committee_key} detail={error!r}")
            continue

        detail = format_exception_location(detail_exception)
        print_and_flush(f"ERROR! fingerprint={fingerprint} committee={committee_key} detail={detail}")


@contextlib.contextmanager
def log_to_file(conf: config.AppConfig):
    log_file_path = os.path.join(conf.STATE_DIR, "logs", "keys-import.log")
    # This should not be required
    os.makedirs(conf.STATE_DIR, exist_ok=True)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with open(log_file_path, "a") as f:
        sys.stdout = f
        sys.stderr = f
        try:
            yield
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


async def keys_import(conf: config.AppConfig, args: argparse.Namespace) -> None:
    # Runs as a standalone script, so we need a worker style database connection
    await db.init_database_for_worker()
    # Print the time and current PID
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} by pid {os.getpid()} ---")
    sys.stdout.flush()

    start = time.perf_counter_ns()

    if args.apply:
        await _refresh_email_cache()

    # Get the KEYS file of each committee
    async with db.session() as data:
        committees = await data.committee().all()
        links = await _links_by_fingerprint(data)
    by_key = {committee.key: committee for committee in committees}
    committees = [committee for committee in committees if (not args.committee) or (committee.key in args.committee)]
    committees.sort(key=lambda c: c.key.lower())
    urls = [_keys_url(committee) for committee in committees]

    total_yes = 0
    total_no = 0
    republished: set[str] = set()
    actually_republished: set[str] = set()
    async for url, status, content in util.get_urls_as_completed(urls):
        # For each remote KEYS file, check that it responded 200 OK
        # Extract committee name from URL
        # This works for both /committee/KEYS and /incubator/committee/KEYS
        committee_key = url.rsplit("/", 2)[-2]
        if status != 200:
            print_and_flush(f"{committee_key} error: {status}")
            continue
        keys_file_text = content.decode("utf-8", errors="replace")
        async with db.session() as data:
            selections = await plan_committee(data, committee_key, keys_file_text, links, args)
        print_and_flush(f"{committee_key} would import {_summary(selections)}")
        for selection in selections:
            print_and_flush(f"  {selection.action} {selection.fingerprint}: {selection.note}")
        actionable = [selection for selection in selections if selection.actionable]
        if actionable:
            republished.add(committee_key)
            republished.update(committee for selection in actionable for committee in selection.republishes)
        if (not args.apply) or (not selections):
            continue
        yes, no, published = await _apply_committee(args.asf_uid, committee_key, selections)
        total_yes += yes
        total_no += no
        actually_republished.update(published)
    if args.apply:
        print_and_flush(f"Republished: {_committee_list(sorted(actually_republished), by_key)}")
    else:
        print_and_flush(f"Would republish: {_committee_list(sorted(republished), by_key)}")
    if args.apply:
        print_and_flush(f"Total okay: {total_yes}")
        print_and_flush(f"Total failed: {total_no}")
    end = time.perf_counter_ns()
    print_and_flush(f"Script took {(end - start) / 1000000} ms")
    print_and_flush("")


async def _apply_committee(asf_uid: str, committee_key: str, selections: list[Selection]) -> tuple[int, int, set[str]]:
    # Parse the KEYS file and add it to the database
    # We use a separate storage.write() context for each committee to avoid transaction conflicts
    async with storage.write(asf_uid) as write:
        waca = write.as_committee_admin(committee_key)
        selected_text = "\n\n".join(selection.armored for selection in selections)
        outcomes, publications = await waca.keys.ensure_associated(
            selected_text, datatypes.KeySource.DOWNLOADS, committee_key
        )
        log_outcome_errors(outcomes, committee_key)
        disabled = datatypes.KeysPublish.AUTOMATION_DISABLED
        for name, publication in sorted(publications.items()):
            if publication.result_or_none() is disabled:
                print_and_flush(f"{name} KEYS file not published: automated publication disabled")
        yes = outcomes.result_count
        no = outcomes.error_count

        # Print and record the number of keys that were okay and failed
        print_and_flush(f"{committee_key} {yes} {no}")
        published = {
            name
            for name, publication in publications.items()
            if publication.result_or_none() is datatypes.KeysPublish.PUBLISHED
        }
        return yes, no, published


def _canonical_certificates(keys_file_text: str) -> dict[str, list[str]]:
    canonical: dict[str, list[str]] = collections.defaultdict(list)
    for block in util.parse_key_blocks(keys_file_text):
        try:
            keys, _ = openpgp.composed.SignedPublicKey.from_armor_many(block)
        except Exception as e:
            print_and_flush(f"  unparseable block skipped: {str(e)[:80]}")
            continue
        for key in keys:
            canonical[key.fingerprint.lower()].append(block if (len(keys) == 1) else key.to_armored())
    return canonical


def _canonical_selection(
    fingerprint: str,
    action: str,
    canonical_text: str,
    note: str,
    effective: str | None,
    republishes: tuple[str, ...] = (),
) -> Selection:
    if effective is not None:
        return Selection(fingerprint, action, canonical_text, note, republishes)
    incomparable = "canonical copies differ and neither supersedes the other; the importer will report this key"
    note = f"{note}; {incomparable}" if note else incomparable
    return Selection(fingerprint, action, canonical_text, note, republishes, actionable=False)


def _committee_list(committee_keys: list[str], by_key: dict[str, sql.Committee]) -> str:
    if not committee_keys:
        return "nothing"
    labels = []
    for committee_key in committee_keys:
        committee = by_key.get(committee_key)
        disabled = (committee is not None) and (committee.keys_mode is not sql.KeysMode.AUTOMATIC)
        labels.append(f"{committee_key} (publication disabled)" if disabled else committee_key)
    return ", ".join(labels)


def _delta(stored_key: openpgp.composed.SignedPublicKey, canonical_key: openpgp.composed.SignedPublicKey) -> str:
    changes = []
    stored_facts = {facts.fingerprint: facts for facts in pgp.signing_key_facts(stored_key)}
    canonical_facts = {facts.fingerprint: facts for facts in pgp.signing_key_facts(canonical_key)}
    if added := sorted(set(canonical_facts) - set(stored_facts)):
        changes.append(f"subkeys added {[f[-16:] for f in added]}")
    if removed := sorted(set(stored_facts) - set(canonical_facts)):
        changes.append(f"subkeys removed {[f[-16:] for f in removed]}")
    if changed := sorted(f for f in set(stored_facts) & set(canonical_facts) if stored_facts[f] != canonical_facts[f]):
        changes.append(f"key facts changed {[f[-16:] for f in changed]}")
    if set(stored_key.user_ids) != set(canonical_key.user_ids):
        changes.append("uids changed")
    if pgp.key_expires_at(stored_key) != pgp.key_expires_at(canonical_key):
        changes.append(f"expiry {pgp.key_expires_at(stored_key)} -> {pgp.key_expires_at(canonical_key)}")
    if pgp.latest_self_signature_created_at(stored_key) != pgp.latest_self_signature_created_at(canonical_key):
        changes.append("self-signature date changed")
    return ", ".join(changes) or "same facts, different encoding"


def _effective_armor(fingerprint: str, armors: list[str]) -> str | None:
    candidates: dict[bytes, str] = {}
    for armored in armors:
        key = pgp.certificate_for_fingerprint(armored, fingerprint)
        if key is not None:
            candidates.setdefault(key.to_bytes(), armored)
    for candidate in candidates.values():
        others = [other for other in candidates.values() if other is not candidate]
        if all(_supersedes(fingerprint, candidate, other) for other in others):
            return candidate
    return None


def _keys_url(committee: sql.Committee) -> str:
    if committee.is_podling:
        return f"https://downloads.apache.org/incubator/{committee.key}/KEYS"
    return f"https://downloads.apache.org/{committee.key}/KEYS"


async def _links_by_fingerprint(data: db.Session) -> dict[str, set[str]]:
    via = sql.validate_instrumented_attribute
    rows = await data.execute(sqlmodel.select(via(sql.KeyLink.key_fingerprint), via(sql.KeyLink.committee_key)))
    links: dict[str, set[str]] = collections.defaultdict(set)
    for fingerprint, committee_key in rows.all():
        links[fingerprint.lower()].add(committee_key)
    return links


async def _refresh_email_cache() -> None:
    ldap_start = time.perf_counter_ns()
    try:
        await cache.email_uid_refresh()
    except Exception as e:
        print_and_flush(f"Email-to-UID cache refresh failed: {e}")
    ldap_end = time.perf_counter_ns()
    print_and_flush(f"LDAP refresh took {(ldap_end - ldap_start) / 1000000} ms")


def _refresh_note(fingerprint: str, row: sql.SigningCertificate, armored: str, others: list[str]) -> str | None:
    stored_text = _text(row.ascii_armored_key)
    try:
        stored_key = pgp.certificate_for_fingerprint(stored_text, fingerprint)
        canonical_key = pgp.certificate_for_fingerprint(armored, fingerprint)
    except Exception as e:
        return f"stored block unreadable ({str(e)[:60]})"
    if (stored_key is None) or (canonical_key is None):
        return "stored block does not hold this key"
    notes = []
    if stored_key.to_bytes() != canonical_key.to_bytes():
        notes.append(f"block differs ({_delta(stored_key, canonical_key)})")
        if (reason := keys_writer._block_downgrade_reason(fingerprint, stored_text, armored)) is not None:
            notes.append(f"downgrade guard would refuse: {reason}")
    if row.primary_declared_uid not in list(canonical_key.user_ids):
        notes.append(f"stored uid {row.primary_declared_uid!r} not in certificate")
    if row.latest_self_signature != pgp.latest_self_signature_created_at(canonical_key):
        notes.append("stored self-signature date differs")
    if not notes:
        return None
    if others:
        notes.append(f"shared with {', '.join(others)}, which would not be republished")
    return "; ".join(notes)


def _summary(selections: list[Selection]) -> str:
    counts = collections.Counter(selection.action for selection in selections)
    return ", ".join(f"{count} {action}" for action, count in sorted(counts.items())) or "nothing"


def _supersedes(fingerprint: str, candidate: str, other: str) -> bool:
    if keys_writer._block_downgrade_reason(fingerprint, other, candidate) is not None:
        return False
    return keys_writer._block_downgrade_reason(fingerprint, candidate, other) is not None


def _text(armored: str | bytes) -> str:
    if isinstance(armored, bytes):
        return armored.decode("utf-8", errors="replace")
    return armored


async def amain() -> None:
    conf = config.AppConfig()
    with log_to_file(conf):
        if release_target_refused():
            print_and_flush(
                "Refusing: ATR publishes to the release area, so this would overwrite every committee's KEYS file there"
            )
            sys.exit(2)
        try:
            await keys_import(conf, parse_args())
        except Exception as e:
            detail = format_exception_location(e)
            print_and_flush(f"Error: {detail}")
            traceback.print_exc()
            sys.stdout.flush()
            sys.exit(1)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
