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
import pathlib
from typing import Final

import sqlalchemy
import sqlmodel
import sqlmodel.sql.expression as expression

import atr.db as db
import atr.hashes as hashes
import atr.models.sql as sql
import atr.paths as paths

_BATCH: Final[int] = 500


def main() -> None:
    asyncio.run(_run(_parse_args()))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Artifact.signature_sha3_256 from the finished release files."
        " Run this before the finished directory is removed, because the digests of"
        " already released signature files can only be computed while those files exist."
        " Idempotent: rows which already carry a digest are left alone. Exits non-zero"
        " if any signature file is missing from disk, since its digest can then never"
        " be recorded."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report the row counts without writing.")
    return parser.parse_args()


def _pending_batch_query(cursor: tuple[str, str, str] | None) -> expression.SelectOfScalar[sql.Artifact]:
    via = sql.validate_instrumented_attribute
    query = (
        sqlmodel.select(sql.Artifact)
        .where(via(sql.Artifact.signature_path).is_not(None))
        .where(via(sql.Artifact.signature_sha3_256).is_(None))
        .where(via(sql.Artifact.managed).is_(True))
        .order_by(via(sql.Artifact.project_key), via(sql.Artifact.version), via(sql.Artifact.artifact_path))
        .limit(_BATCH)
    )
    if cursor is not None:
        identity = sqlalchemy.tuple_(
            via(sql.Artifact.project_key), via(sql.Artifact.version), via(sql.Artifact.artifact_path)
        )
        query = query.where(identity > cursor)
    return query


async def _run(args: argparse.Namespace) -> None:
    await db.init_database_for_worker()
    finished_dir = pathlib.Path(paths.get_finished_dir())
    updated = 0
    missing: list[str] = []
    cursor: tuple[str, str, str] | None = None
    async with db.session() as data:
        while True:
            artifacts = (await data.execute(_pending_batch_query(cursor))).scalars().all()
            if not artifacts:
                break
            for artifact in artifacts:
                file_path = finished_dir / artifact.project_key / artifact.version / str(artifact.signature_path)
                if not file_path.is_file():
                    missing.append(f"{artifact.project_key} {artifact.version} {artifact.signature_path}")
                    continue
                digest = await hashes.file_sha3(str(file_path))
                if not args.dry_run:
                    artifact.signature_sha3_256 = digest
                updated += 1
            if not args.dry_run:
                await data.commit()
            last = artifacts[-1]
            cursor = (last.project_key, last.version, last.artifact_path)
    for identity in missing:
        print(f"  missing file        {identity}")
    print(f"  digests computed    {updated}")
    print(f"  files not on disk   {len(missing)}")
    if args.dry_run:
        print("  (dry run, nothing written)")
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
