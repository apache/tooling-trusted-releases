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
import hashlib
from typing import Final

import openpgp.composed
import sqlmodel

import atr.constants as constants
import atr.db as db
import atr.models.sql as sql
import atr.pgp as pgp
import atr.storage.writers.keys as keys_writer
import atr.tasks as tasks

_REPAIRABLE: Final[frozenset[str]] = frozenset({"multi-own-first", "multi-own-not-first"})


@dataclasses.dataclass(frozen=True)
class Finding:
    fingerprint: str
    kind: str
    detail: str
    committees: tuple[str, ...]
    deleted: bool
    text: str | bytes
    replacement: str | None = None


def main() -> None:
    asyncio.run(_run(_parse_args()))


def _certificate_finding(certificate: sql.SigningCertificate, committees: tuple[str, ...]) -> Finding | None:
    fingerprint = certificate.fingerprint.lower()
    deleted = certificate.deleted is not None
    raw = certificate.ascii_armored_key
    text = _text(raw)
    try:
        keys, _ = openpgp.composed.SignedPublicKey.from_armor_many(text)
    except Exception as e:
        return Finding(fingerprint, "unparseable", str(e)[:120], committees, deleted, raw)
    fingerprints = [key.fingerprint.lower() for key in keys]
    matches = [key for key in keys if key.fingerprint.lower() == fingerprint]
    kind = pgp.certificate_block_shape(keys, fingerprint)
    if kind == "single":
        return None
    if kind not in _REPAIRABLE:
        return Finding(fingerprint, kind, f"holds {fingerprints}", committees, deleted, raw)
    detail = f"{len(keys)} certificates" + _drift(certificate, matches[0])
    replacement = matches[0].to_armored()
    problem = _round_trip_problem(matches[0], replacement, fingerprint)
    if problem is not None:
        return Finding(fingerprint, "round-trip-mismatch", f"{detail}; {problem}", committees, deleted, raw)
    return Finding(fingerprint, kind, detail, committees, deleted, raw, replacement)


async def _committees_by_fingerprint(data: db.Session) -> dict[str, tuple[str, ...]]:
    via = sql.validate_instrumented_attribute
    rows = await data.execute(sqlmodel.select(via(sql.KeyLink.key_fingerprint), via(sql.KeyLink.committee_key)))
    grouped: dict[str, list[str]] = collections.defaultdict(list)
    for fingerprint, committee_key in rows.all():
        grouped[fingerprint.lower()].append(committee_key)
    return {fingerprint: tuple(sorted(keys)) for fingerprint, keys in grouped.items()}


def _digest(text: str | bytes) -> str:
    data = text if isinstance(text, bytes) else text.encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()[:12]


def _drift(certificate: sql.SigningCertificate, key: openpgp.composed.SignedPublicKey) -> str:
    notes = []
    uids = list(key.user_ids)
    if certificate.primary_declared_uid not in uids:
        notes.append(f"uid drift {certificate.primary_declared_uid!r} not among {uids!r}")
    latest = pgp.latest_self_signature_created_at(key)
    if certificate.latest_self_signature != latest:
        notes.append(f"self-signature drift {certificate.latest_self_signature} vs {latest}")
    return "".join(f"; {note}" for note in notes)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split stored key blocks holding several certificates so that each row keeps only its own."
        " Reports by default; --apply rewrites the rows which round-trip cleanly. Back up the database first.",
    )
    parser.add_argument("--apply", action="store_true", help="Rewrite repairable rows and requeue signature checks.")
    return parser.parse_args()


async def _recheck_drafts(data: db.Session, committee_keys: set[str]) -> int:
    drafts = await data.release(phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT, _committee=True).all()
    rechecked = 0
    for draft in drafts:
        committee = draft.project.committee
        if (committee is None) or (committee.key not in committee_keys) or (not draft.latest_revision_number):
            continue
        await tasks.draft_checks(
            constants.SYSTEM_SERVICE_UID,
            draft.safe_project_key,
            draft.safe_version_key,
            draft.safe_latest_revision_number,
            suffix_filter=[".asc"],
        )
        rechecked += 1
    return rechecked


async def _repair(data: db.Session, finding: Finding) -> bool:
    via = sql.validate_instrumented_attribute
    await data.begin_immediate()
    result = await data.execute(
        sqlmodel.update(sql.SigningCertificate)
        .where(via(sql.SigningCertificate.fingerprint) == finding.fingerprint)
        .where(via(sql.SigningCertificate.ascii_armored_key) == finding.text)
        .values(ascii_armored_key=finding.replacement)
    )
    if getattr(result, "rowcount", 0) != 1:
        await data.rollback()
        print(f"  changed concurrently, skipped: {finding.fingerprint}")
        return False
    certificate = await data.signing_certificate(fingerprint=finding.fingerprint, deleted=db.NOT_SET).get()
    if certificate is None:
        await data.rollback()
        print(f"  vanished concurrently, skipped: {finding.fingerprint}")
        return False
    await keys_writer._sync_signing_keys(data, [certificate])
    await data.commit()
    digests = f"{_digest(finding.text)} -> {_digest(finding.replacement or '')}"
    print(f"  repaired {finding.fingerprint} {digests} {list(finding.committees)}")
    return True


def _round_trip_problem(key: openpgp.composed.SignedPublicKey, replacement: str, fingerprint: str) -> str | None:
    try:
        reparsed, _ = openpgp.composed.SignedPublicKey.from_armor_many(replacement)
    except Exception as e:
        return f"re-armoured text does not parse: {e}"
    if len(reparsed) != 1:
        return f"re-armoured text holds {len(reparsed)} certificates"
    if reparsed[0].fingerprint.lower() != fingerprint:
        return f"re-armoured text is {reparsed[0].fingerprint.lower()}"
    if reparsed[0].to_bytes() != key.to_bytes():
        return "re-armoured bytes differ"
    if pgp.latest_self_signature_created_at(reparsed[0]) != pgp.latest_self_signature_created_at(key):
        return "re-armoured self-signature differs"
    if pgp.revocations_dropped(key, reparsed[0]):
        return "re-armouring drops a revocation"
    return None


async def _run(args: argparse.Namespace) -> None:
    await db.init_database_for_worker()
    async with db.session() as data:
        certificates = (await data.execute(sqlmodel.select(sql.SigningCertificate))).scalars().all()
        committees = await _committees_by_fingerprint(data)
        findings = []
        for certificate in certificates:
            finding = _certificate_finding(certificate, committees.get(certificate.fingerprint.lower(), ()))
            if finding is not None:
                findings.append(finding)
        _summarise(len(certificates), findings)
        affected = {committee for finding in findings for committee in finding.committees}
        if not args.apply:
            print(f"  (dry run: {len(affected)} committees would be rechecked; use --apply to rewrite)")
            return
        repaired = 0
        for finding in findings:
            if finding.replacement is not None:
                repaired += await _repair(data, finding)
        rechecked = await _recheck_drafts(data, affected)
        print(f"  repaired            {repaired}")
        print(f"  drafts rechecked    {rechecked} across {len(affected)} committees")


def _summarise(total: int, findings: list[Finding]) -> None:
    counts = collections.Counter(finding.kind for finding in findings)
    print(f"  certificates        {total}")
    for kind, count in sorted(counts.items()):
        print(f"  {kind:26s}{count}")
    for finding in sorted(findings, key=lambda finding: (finding.kind, finding.fingerprint)):
        state = " deleted" if finding.deleted else ""
        print(f"  {finding.kind} {finding.fingerprint}{state} {list(finding.committees)}: {finding.detail}")
    print(f"  repairable          {sum(1 for finding in findings if finding.kind in _REPAIRABLE)}")


def _text(armored: str | bytes) -> str:
    if isinstance(armored, bytes):
        return armored.decode("utf-8", errors="replace")
    return armored


if __name__ == "__main__":
    main()
