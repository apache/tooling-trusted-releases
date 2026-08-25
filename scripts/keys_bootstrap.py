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
import datetime
import os
import pathlib
import sys
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["STATE_DIR"] = str(pathlib.Path(os.environ.get("STATE_DIR", "state")).resolve())

import openpgp.composed
import sqlmodel

import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.models.sql as sql
import atr.pgp as pgp
import atr.principal as principal
import atr.storage as storage

_LARGE: Final = 1000
_SOURCE: Final = "repair:keys_bootstrap"


def main() -> None:
    asyncio.run(_run(_parse_args()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Give every certificate which has no log chain a synthetic genesis chain built from its"
        " own stored head, so that the writer accepts changes to it again. For development instances; a real"
        " instance needs the provenance backfill. Reports by default; --apply writes in one transaction."
    )
    parser.add_argument("--apply", action="store_true", help="Write to the database instead of only reporting.")
    parser.add_argument("--large", action="store_true", help="Bootstrap more certificates than the safety limit.")
    return parser.parse_args()


def _problems(marker: str | None, scope: list[sql.SigningCertificate], large: bool) -> list[str]:
    problems = []
    if (marker is not None) and (marker != "1"):
        problems.append(f"unknown openpgp-log format marker {marker!r}")
    if (marker == "1") and scope:
        problems.append("a backfilled log is missing chains; investigate the certificates above, do not bootstrap")
    if (len(scope) > _LARGE) and (not large):
        problems.append(f"{len(scope)} certificates exceeds {_LARGE}; use the provenance backfill, or pass --large")
    return problems


async def _run(args: argparse.Namespace) -> None:
    conf = config.get()
    db_path = pathlib.Path(conf.STATE_DIR) / conf.SQLITE_DB_PATH
    if not db_path.is_file():
        print(f"ERROR! no database at {db_path}")
        sys.exit(1)
    await db.init_database_for_worker()
    async with db.session() as data:
        certificates = list((await data.execute(sqlmodel.select(sql.SigningCertificate))).scalars().all())
        via = sql.validate_instrumented_attribute
        chained_rows = await data.execute(sqlmodel.select(via(sql.KeyAttestable.fingerprint)).distinct())
        chained = frozenset(fingerprint.lower() for fingerprint in chained_rows.scalars().all())
        marker = await data.ns_text_get("openpgp-log", "format")
    scope = sorted((c for c in certificates if c.fingerprint.lower() not in chained), key=lambda c: c.fingerprint)
    plan = []
    skipped = []
    for certificate in scope:
        placements, reason = _sane(certificate)
        if placements is None:
            skipped.append(f"  skipped {certificate.fingerprint}: {reason}")
        else:
            plan.append((certificate, placements))
    deletions = sum(1 for certificate, _ in plan if certificate.deleted is not None)
    print(
        f"certificates {len(certificates)}; chained {len(certificates) - len(scope)};"
        f" bootstrapping {len(plan)}; of which deleted {deletions}; skipped {len(skipped)}"
    )
    print("\n".join(skipped), end="\n" if skipped else "")
    problems = _problems(marker, scope, args.large)
    for problem in problems:
        print(f"ERROR! {problem}")
    if problems:
        sys.exit(1)
    if not plan:
        print("nothing to bootstrap")
        return
    if args.apply:
        await _write(plan, marker)


def _sane(certificate: sql.SigningCertificate) -> tuple[frozenset[pgp.Placement] | None, str | None]:
    if certificate.fingerprint != certificate.fingerprint.lower():
        return None, "fingerprint is not lowercase"
    text = certificate.ascii_armored_key
    begins = sum(1 for line in text.splitlines() if line.strip() == "-----BEGIN PGP PUBLIC KEY BLOCK-----")
    if begins != 1:
        return None, f"{begins} armor blocks"
    try:
        placements = pgp.certificate_placements(text)
        block = pgp.certificate_block(placements)
        key, _ = openpgp.composed.SignedPublicKey.from_armor(block)
    except Exception as e:
        return None, str(e)[:80]
    if key.fingerprint.lower() != certificate.fingerprint:
        return None, f"parses as {key.fingerprint.lower()}"
    if pgp.certificate_placements(block) != placements:
        return None, "does not read back to its placements"
    return placements, None


async def _verify(data: db.Session) -> None:
    result = await data.execute(sqlmodel.select(sql.SigningCertificate))
    certificates = {c.fingerprint.lower(): c for c in result.scalars().all()}
    via = sql.validate_instrumented_attribute
    rows_result = await data.execute(
        sqlmodel.select(sql.KeyAttestable).order_by(via(sql.KeyAttestable.fingerprint), via(sql.KeyAttestable.seq))
    )
    chains: dict[str, list[sql.KeyAttestable]] = collections.defaultdict(list)
    for row in rows_result.scalars().all():
        chains[row.fingerprint.lower()].append(row)
    for fingerprint, rows in chains.items():
        certificate = certificates.get(fingerprint)
        if certificate is None:
            raise RuntimeError(f"a chain exists for {fingerprint} but no certificate does")
        if [row.seq for row in rows] != list(range(1, len(rows) + 1)):
            raise RuntimeError(f"the chain for {fingerprint} is not contiguous")
        fold = pgp.fold_deltas((row.deletions, row.additions) for row in rows)
        if pgp.certificate_placements(certificate.ascii_armored_key) != fold:
            raise RuntimeError(f"the head for {fingerprint} does not equal its fold")
        if (certificate.deleted is not None) != (rows[-1].operation == sql.KeyOperation.DELETE):
            raise RuntimeError(f"the chain for {fingerprint} disagrees with its deletion state")
    print(f"verified {len(chains)} chains against their heads")


async def _write(plan: list[tuple[sql.SigningCertificate, frozenset[pgp.Placement]]], marker: str | None) -> None:
    updated = datetime.datetime.now(datetime.UTC)
    authorisation = await principal.Authorisation(constants.SYSTEM_SERVICE_UID)
    rows = len(plan) + sum(1 for certificate, _ in plan if certificate.deleted is not None)
    async with db.session() as data:
        await data.begin_immediate()
        writer = storage.Write(authorisation, data).as_foundation_committer().keys
        for certificate, placements in plan:
            fingerprint = certificate.fingerprint
            args = {"actor": constants.SYSTEM_SERVICE_UID, "role": sql.KeyRole.SERVICE, "updated": updated}
            writer._append_certificate_row(
                fingerprint, 1, sql.KeyOperation.REVISE, _SOURCE, None, frozenset(), placements, **args
            )
            if certificate.deleted is not None:
                writer._append_certificate_row(
                    fingerprint, 2, sql.KeyOperation.DELETE, _SOURCE, None, placements, placements, **args
                )
        if marker is None:
            await data.ns_text_set("openpgp-log", "format", "1", commit=False)
        await _verify(data)
        await data.commit()
    print(f"wrote {rows} rows for {len(plan)} certificates")


if __name__ == "__main__":
    main()
