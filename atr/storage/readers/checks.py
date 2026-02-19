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

from typing import TYPE_CHECKING

import atr.attestable as attestable
import atr.db as db
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.types as types
import atr.util as util

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable


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

    async def by_release_path(self, release: sql.Release, rel_path: pathlib.Path) -> types.CheckResults:
        if release.latest_revision_number is None:
            raise ValueError("Release has no revision - Invalid state")

        file_path_checks = await attestable.load_checks(
            release.project_name, release.version, release.latest_revision_number
        )
        all_check_results = (
            [
                a
                for a in await self.__data.check_result(
                    inputs_hash_in=[
                        h
                        for key in ("", str(rel_path))
                        if key in file_path_checks
                        for h in file_path_checks[key].values()
                    ],
                    primary_rel_path=str(rel_path),
                )
                .order_by(
                    sql.validate_instrumented_attribute(sql.CheckResult.checker).asc(),
                    sql.validate_instrumented_attribute(sql.CheckResult.created).desc(),
                )
                .all()
            ]
            if file_path_checks
            else []
        )

        # Filter out any results that are ignored
        unignored_checks = []
        ignored_checks = []
        match_ignore = await self.ignores_matcher(release.project_name)
        for cr in all_check_results:
            if not match_ignore(cr):
                unignored_checks.append(cr)
            else:
                ignored_checks.append(cr)

        # Filter to separate the primary and member results
        primary_results_list = []
        member_results_list: dict[str, list[sql.CheckResult]] = {}
        for result in unignored_checks:
            if result.member_rel_path is None:
                primary_results_list.append(result)
            else:
                member_results_list.setdefault(result.member_rel_path, []).append(result)

        # Order primary results by checker name
        primary_results_list.sort(key=lambda r: r.checker)

        # Order member results by relative path and then by checker name
        for member_rel_path in sorted(member_results_list.keys()):
            member_results_list[member_rel_path].sort(key=lambda r: r.checker)
        return types.CheckResults(primary_results_list, member_results_list, ignored_checks)

    async def ignores(self, project_name: str) -> list[sql.CheckResultIgnore]:
        results = await self.__data.check_result_ignore(
            project_name=project_name,
        ).all()
        return list(results)

    async def ignores_matcher(
        self,
        project_name: str,
    ) -> Callable[[sql.CheckResult], bool]:
        ignores = await self.__data.check_result_ignore(
            project_name=project_name,
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
        if cr.status == sql.CheckResultStatus.SUCCESS:
            # Successes are never ignored
            return False
        if cr.status == sql.CheckResultStatus.BLOCKER:
            # Blockers are never ignored
            return False
        if cri.release_glob is not None:
            if not self.__check_ignore_match_pattern(cri.release_glob, cr.release_name):
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
            if cr.status != cri.status:
                return False
        if cri.message_glob is not None:
            if not self.__check_ignore_match_pattern(cri.message_glob, cr.message):
                return False
        return True

    def __check_ignore_match_pattern(self, pattern: str | None, value: str | None) -> bool:
        return util.match_ignore_pattern(pattern, value)
