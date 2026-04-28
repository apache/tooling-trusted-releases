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

"""user.py"""

import functools

import atr.cache as cache
import atr.config as config
import atr.db as db
import atr.models.sql as sql
import atr.util as util


async def candidate_drafts(uid: str, user_projects: list[sql.Project] | None = None) -> list[sql.Release]:
    # Must be imported here, to avoid a circular import
    import atr.db.interaction as interaction

    if user_projects is None:
        user_projects = await projects(uid)
    user_candidate_drafts: list[sql.Release] = []
    for p in user_projects:
        releases = await interaction.candidate_drafts(p)
        user_candidate_drafts.extend(releases)
    return user_candidate_drafts


def is_admin(user_id: str | None) -> bool:
    if user_id is None:
        return False
    if config.is_test_mode() and (user_id == "test"):
        return True
    # TODO: is_user_session_downgraded only works in a Quart async context
    if util.is_user_session_downgraded():
        return False
    if user_id in _get_additional_admin_users():
        return True
    return user_id in cache.admins_get()


async def is_admin_async(user_id: str | None) -> bool:
    if user_id is None:
        return False
    if config.is_test_mode() and (user_id == "test"):
        return True
    if util.is_user_session_downgraded():
        return False
    if user_id in _get_additional_admin_users():
        return True
    return user_id in await cache.admins_get_async()


async def is_binding_for_release(
    committee: sql.Committee,
    asf_uid: str,
    vote_round: int | None,
) -> tuple[bool, str]:
    if not committee.is_podling:
        if vote_round is not None:
            raise ValueError("Non-podling votes require vote_round to be None")
        return is_committee_member(committee, asf_uid), committee.display_name

    if vote_round is None:
        raise ValueError("Podling votes require vote_round 1 or 2")
    if vote_round not in (1, 2):
        raise ValueError(f"Unexpected podling vote_round: {vote_round!r}")
    async with db.session() as data:
        incubator = await data.committee(key="incubator").get()
    return is_committee_member(incubator, asf_uid), "Incubator"


def is_committee_member(committee: sql.Committee | None, uid: str) -> bool:
    if committee is None:
        return False
    return any((member_uid == uid) for member_uid in committee.committee_members)


def is_committer(committee: sql.Committee | None, uid: str) -> bool:
    if committee is None:
        return False
    return any((committer_uid == uid) for committer_uid in committee.committers)


async def projects(uid: str, committee_only: bool = False, super_project: bool = False) -> list[sql.Project]:
    user_projects: list[sql.Project] = []
    async with db.session() as data:
        # Must have releases, because this is used in candidate_drafts
        projects = await data.project(
            status=sql.ProjectStatus.ACTIVE, _committee=True, _releases=True, _super_project=super_project
        ).all()
        for p in projects:
            if p.committee is None:
                continue

            # Allow access to test project in Test mode
            # This means that the Test project will show in the user interface for everyone
            if config.is_test_mode() and (p.committee.key == "test"):
                user_projects.append(p)
                continue

            if committee_only:
                if uid in p.committee.committee_members:
                    user_projects.append(p)
            else:
                if (uid in p.committee.committee_members) or (uid in p.committee.committers):
                    user_projects.append(p)
    return user_projects


@functools.cache
def _get_additional_admin_users() -> frozenset[str]:
    additional = config.get().ADMIN_USERS_ADDITIONAL
    if not additional:
        return frozenset()
    return frozenset(additional.split(","))
