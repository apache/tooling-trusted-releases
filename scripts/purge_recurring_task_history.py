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
Drop the completed recurring-task rows that predate the log-and-delete change.

From #1404 onwards a recurring task logs a line and deletes itself on success, so
only the historical backlog needs a one-off sweep. This deletes the completed rows
for the four recurring types and VACUUMs to hand the freed pages back to the OS.
Failed rows stay put - their errors are the reason we keep them.

    PYTHONPATH=. uv run python3 scripts/purge_recurring_task_history.py            # dry run
    PYTHONPATH=. uv run python3 scripts/purge_recurring_task_history.py --apply
    PYTHONPATH=. uv run python3 scripts/purge_recurring_task_history.py --apply --log-first
    PYTHONPATH=. uv run python3 scripts/purge_recurring_task_history.py --db /var/opt/atr-staging/database/atr.db

The SQLAlchemy enum column stores the member name, so task_type/status are the
uppercase names here, not their lowercase values.
"""

import argparse
import json
import sqlite3
import sys
from typing import Final

# Mirrors sql.RECURRING_TASK_TYPES, as member names (how the column stores them)
_RECURRING_NAMES: Final[tuple[str, ...]] = (
    "DISTRIBUTION_STATUS",
    "MAINTENANCE",
    "METADATA_UPDATE",
    "WORKFLOW_STATUS",
)


def main() -> None:
    args = _parse_args()
    connection = sqlite3.connect(args.db)
    connection.isolation_level = None
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    try:
        _purge(connection, apply=args.apply, log_first=args.log_first, log_file=args.log_file)
    finally:
        connection.close()


def _purge(connection: sqlite3.Connection, apply: bool, log_first: bool, log_file: str) -> None:
    counts = _counts(connection)
    total = sum(counts.values())
    for name in _RECURRING_NAMES:
        print(f"  {name}: {counts.get(name, 0)} completed rows")
    if total == 0:
        print("Nothing to purge.")
        return
    if not apply:
        print(f"Would delete {total} completed recurring rows. Re-run with --apply.")
        return

    if log_first:
        written = _log_rows(connection, log_file)
        print(f"Logged {written} rows to {log_file}")

    pages_before = _page_count(connection)
    deleted = _delete(connection)
    connection.execute("VACUUM")
    pages_after = _page_count(connection)
    reclaimed = (pages_before - pages_after) * _page_size(connection)
    print(f"Deleted {deleted} rows; VACUUM reclaimed {reclaimed / (1024 * 1024):.1f} MiB.")


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    placeholders = ", ".join("?" for _ in _RECURRING_NAMES)
    rows = connection.execute(
        f"SELECT task_type, COUNT(*) FROM task"
        f" WHERE status = 'COMPLETED' AND task_type IN ({placeholders})"
        f" GROUP BY task_type",
        _RECURRING_NAMES,
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _delete(connection: sqlite3.Connection) -> int:
    placeholders = ", ".join("?" for _ in _RECURRING_NAMES)
    connection.execute("BEGIN IMMEDIATE")
    try:
        cursor = connection.execute(
            f"DELETE FROM task WHERE status = 'COMPLETED' AND task_type IN ({placeholders})",
            _RECURRING_NAMES,
        )
        deleted = cursor.rowcount
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return deleted


def _log_rows(connection: sqlite3.Connection, log_file: str) -> int:
    placeholders = ", ".join("?" for _ in _RECURRING_NAMES)
    rows = connection.execute(
        f"SELECT id, task_type, asf_uid, added, started, completed, task_args, result, pid FROM task"
        f" WHERE status = 'COMPLETED' AND task_type IN ({placeholders})",
        _RECURRING_NAMES,
    ).fetchall()
    written = 0
    with open(log_file, "a", encoding="utf-8") as handle:
        for row in rows:
            record = {
                "id": row[0],
                "task_type": row[1],
                "status": "COMPLETED",
                "asf_uid": row[2],
                "added": row[3],
                "started": row[4],
                "completed": row[5],
                "task_args": _maybe_json(row[6]),
                "result": _maybe_json(row[7]),
                "pid": row[8],
            }
            handle.write(json.dumps(record, allow_nan=False) + "\n")
            written += 1
    return written


def _maybe_json(value: str | None) -> object:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _page_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA page_count").fetchone()[0])


def _page_size(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA page_size").fetchone()[0])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Purge historical completed recurring-task rows.")
    parser.add_argument("--db", default="state/database/atr.db", help="Path to the SQLite database.")
    parser.add_argument("--apply", action="store_true", help="Delete the rows and VACUUM; otherwise only count.")
    parser.add_argument("--log-first", action="store_true", help="Append the rows as JSON lines before deleting.")
    parser.add_argument("--log-file", default="state/logs/tasks.log", help="Destination for --log-first.")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
