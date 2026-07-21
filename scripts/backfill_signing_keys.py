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

"""
Materialise the subkey SigningKey rows migration 0110 could not seed (issue #1419).

0110 also derives these during its own upgrade, so this is a safety net for a database whose
migration predates that, or which needs re-deriving after a block changed underneath it. It
drives _sync_signing_keys - the same parse the writer runs on every key import - over every
stored certificate, so a subkey materialised here is identical to one materialised on import.

Idempotent: rows are upserted on fingerprint, so a second run is a no-op.

    PYTHONPATH=. python3 scripts/backfill_signing_keys.py [--dry-run]
"""

import argparse
import asyncio
from typing import Final

import sqlalchemy
import sqlmodel

import atr.db as db
import atr.models.sql as sql
import atr.storage.writers.keys as keys_writer

_BATCH: Final[int] = 500


def main() -> None:
    asyncio.run(_run(_parse_args()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialise the SigningKey rows for every stored certificate.")
    parser.add_argument("--dry-run", action="store_true", help="Report the certificate count without writing.")
    return parser.parse_args()


async def _count(data: db.Session, where: sqlalchemy.ColumnElement[bool]) -> int:
    result = await data.execute(sqlmodel.select(sqlalchemy.func.count()).select_from(sql.SigningKey).where(where))
    return result.scalar() or 0


async def _run(args: argparse.Namespace) -> None:
    await db.init_database_for_worker()
    async with db.session() as data:
        certificates = (await data.execute(sqlmodel.select(sql.SigningCertificate))).scalars().all()
        print(f"  certificates        {len(certificates)}")
        if args.dry_run:
            print("  (dry run, nothing written)")
            return

        for start in range(0, len(certificates), _BATCH):
            await keys_writer._sync_signing_keys(data, list(certificates[start : start + _BATCH]))
            await data.commit()

        via = sql.validate_instrumented_attribute
        print(f"  primary keys        {await _count(data, via(sql.SigningKey.is_primary).is_(True))}")
        print(f"  subkeys             {await _count(data, via(sql.SigningKey.is_primary).is_(False))}")
        print(f"  ... revoked         {await _count(data, via(sql.SigningKey.revoked).is_(True))}")
        print(f"  ... cannot sign     {await _count(data, via(sql.SigningKey.can_sign).is_(False))}")


if __name__ == "__main__":
    main()
