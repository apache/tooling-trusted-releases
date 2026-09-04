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

# Removing this will cause circular imports
from __future__ import annotations

import collections
from typing import TYPE_CHECKING, Final

import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.util as util

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable

MEMBER_STATUSES: Final[tuple[sql.CheckResultStatus, ...]] = (
    sql.CheckResultStatus.BLOCKER,
    sql.CheckResultStatus.CONCERN,
    sql.CheckResultStatus.EXCEPTION,
    sql.CheckResultStatus.SUGGESTION,
)


class GeneralPublic:
    def __init__(
        self,
        read: storage.Read,
        read_as: storage.ReadAsGeneralPublic,
        data: db.Session,
        asf_uid: str | None = None,
    ):
        self.__read = read
        self.__read_as = read_as
        self.__data = data
        self.__asf_uid = read.authorisation.asf_uid

    async def by_release_path(
        self,
        release: sql.Release,
        rel_path: pathlib.Path,
        offset: int,
        limit: int,
        revision: safe.RevisionNumber | None = None,
    ) -> datatypes.CheckResults:
        if revision is None:
            revision = release.safe_latest_revision_number

        path = str(rel_path)
        primary_checks = await interaction.checks_for(
            release, revision, rel_path=path, member=False, caller_data=self.__data
        )
        member_checks = await interaction.checks_for(
            release, revision, rel_path=path, member=True, statuses=MEMBER_STATUSES, caller_data=self.__data
        )
        member_note_count = await interaction.member_note_count(release, path, revision, caller_data=self.__data)

        # Filter out any results that are ignored
        unignored_checks = []
        ignored_checks = []
        match_ignore = await self.ignores_matcher(release.safe_project_key)
        for cr in primary_checks + member_checks:
            if not match_ignore(cr):
                unignored_checks.append(cr)
            else:
                ignored_checks.append(cr)

        # Filter to separate the primary and member results
        primary_results_list = []
        member_results: list[sql.CheckResult] = []
        for result in unignored_checks:
            if result.member_rel_path is None:
                primary_results_list.append(result)
            else:
                member_results.append(result)

        # Order primary results by checker name
        primary_results_list.sort(key=lambda r: r.checker)

        # Order member results by relative path and then by checker name
        member_results.sort(key=lambda r: (r.member_rel_path or "", r.checker))
        member_results_list: dict[str, list[sql.CheckResult]] = {}
        for result in member_results[offset : (offset + limit)]:
            member_results_list.setdefault(result.member_rel_path or "", []).append(result)
        return datatypes.CheckResults(
            primary_results_list,
            member_results_list,
            ignored_checks,
            len(member_results),
            collections.Counter(result.status for result in member_results),
            member_note_count,
        )

    async def ignores(self, project_key: safe.ProjectKey) -> list[sql.CheckResultIgnore]:
        results = await self.__data.check_result_ignore(
            project_key=str(project_key),
        ).all()
        return list(results)

    async def ignores_matcher(
        self,
        project_key: safe.ProjectKey,
    ) -> Callable[[sql.CheckResult], bool]:
        ignores = await self.__data.check_result_ignore(
            project_key=str(project_key),
        ).all()

        def match(cr: sql.CheckResult) -> bool:
            for ignore in ignores:
                if self.__check_ignore_match(cr, ignore):
                    # log.info(f"Ignoring check result {cr} due to ignore {ignore}")
                    return True
            return False

        return match

    def __check_ignore_match(self, cr: sql.CheckResult, cri: sql.CheckResultIgnore) -> bool:
        # Does not check that the project name matches
        if cr.status == sql.CheckResultStatus.NOTE:
            # Notes are never ignored
            return False
        if cr.status == sql.CheckResultStatus.BLOCKER:
            # Blockers are never ignored
            return False
        if cri.release_glob is not None:
            if not self.__check_ignore_match_pattern(cri.release_glob, str(cr.release_key)):
                return False
        if cri.revision_number is not None:
            if cri.revision_number != cr.revision_number:
                return False
        if cri.checker_glob is not None:
            if not self.__check_ignore_match_pattern(cri.checker_glob, cr.checker):
                return False
        return self.__check_ignore_match_2(cr, cri)

    def __check_ignore_match_2(self, cr: sql.CheckResult, cri: sql.CheckResultIgnore) -> bool:
        if cri.primary_rel_path_glob is not None:
            if not self.__check_ignore_match_pattern(cri.primary_rel_path_glob, cr.primary_rel_path):
                return False
        if cri.member_rel_path_glob is not None:
            if not self.__check_ignore_match_pattern(cri.member_rel_path_glob, cr.member_rel_path):
                return False
        if cri.status is not None:
            if cr.status.value != cri.status.value:
                return False
        if cri.message_glob is not None:
            if not self.__check_ignore_match_pattern(cri.message_glob, cr.message):
                return False
        return True

    def __check_ignore_match_pattern(self, pattern: str | None, value: str | None) -> bool:
        return util.match_ignore_pattern(pattern, value)
