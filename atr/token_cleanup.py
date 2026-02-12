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

"""Periodic cleanup of Personal Access Tokens for banned or deleted accounts."""

import asyncio
from typing import Final

import sqlalchemy
import sqlmodel

import atr.db as db
import atr.ldap as ldap
import atr.log as log
import atr.models.sql as sql
import atr.storage as storage

# ~1 hour, deliberately offset from the admin poll interval (3631s)
# to avoid simultaneous LDAP request spikes
POLL_INTERVAL_SECONDS: Final[int] = 3617


async def cleanup_loop() -> None:
    """Periodically revoke PATs belonging to banned or deleted LDAP accounts."""
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            await revoke_pats_for_banned_users()
        except Exception as e:
            log.warning(f"PAT banned-user cleanup failed: {e}")


async def revoke_pats_for_banned_users() -> int:
    """Check all PAT-holding users against LDAP and revoke tokens for banned/deleted accounts.

    Returns the total number of tokens revoked.
    """
    # Step 1: Get distinct UIDs that have PATs
    async with db.session() as data:
        stmt = sqlmodel.select(sql.PersonalAccessToken.asfuid).distinct()
        rows = await data.execute_query(stmt)
        uids_with_pats = [row[0] for row in rows]

    if not uids_with_pats:
        return 0

    # Step 2: Check each against LDAP, revoke if banned/deleted
    revoked_total = 0
    for uid in uids_with_pats:
        try:
            account = await ldap.account_lookup(uid)
            if (account is not None) and (not ldap.is_banned(account)):
                continue

            # Account is gone or banned — delete all their tokens
            async with db.session() as data:
                delete_stmt = sqlalchemy.delete(sql.PersonalAccessToken).where(sql.PersonalAccessToken.asfuid == uid)
                result = await data.execute_query(delete_stmt)
                await data.commit()
                count: int = getattr(result, "rowcount", 0)

            if count > 0:
                storage.audit(
                    target_asf_uid=uid,
                    tokens_revoked=count,
                    reason="account_banned_or_deleted",
                    source="pat_cleanup_loop",
                )
                log.info(f"Auto-revoked {count} PAT(s) for banned/deleted user {uid}")
                revoked_total += count

        except Exception as e:
            log.warning(f"PAT cleanup: failed to check/revoke for {uid}: {e}")

    if revoked_total > 0:
        log.info(f"PAT cleanup cycle complete: revoked {revoked_total} total token(s)")

    return revoked_total
