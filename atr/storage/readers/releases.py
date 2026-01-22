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

import dataclasses
import pathlib
import re

import atr.analysis as analysis
import atr.db as db
import atr.models.sql as sql
import atr.storage as storage
import atr.storage.types as types


@dataclasses.dataclass
class CheckerAccumulator:
    success: int = 0
    warning: int = 0
    failure: int = 0
    warning_files: dict[str, int] = dataclasses.field(default_factory=dict)
    failure_files: dict[str, int] = dataclasses.field(default_factory=dict)


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

    async def path_info(self, release: sql.Release, paths: list[pathlib.Path]) -> types.PathInfo | None:
        info = types.PathInfo()
        latest_revision_number = release.latest_revision_number
        if latest_revision_number is None:
            return None
        await self.__successes_errors_warnings(release, latest_revision_number, info)
        for path in paths:
            # Get artifacts and metadata
            search = re.search(analysis.extension_pattern(), str(path))
            if search:
                if search.group("artifact"):
                    info.artifacts.add(path)
                elif search.group("metadata"):
                    info.metadata.add(path)
        self.__compute_checker_stats(info, paths)
        return info

    def __accumulate_results(
        self,
        results: dict[pathlib.Path, list[sql.CheckResult]],
        paths_set: set[pathlib.Path],
        checker_data: dict[str, CheckerAccumulator],
        kind: str,
    ) -> None:
        for path, results_list in results.items():
            if path not in paths_set:
                continue
            for result in results_list:
                acc = checker_data.setdefault(result.checker, CheckerAccumulator())
                path_str = str(path)
                if kind == "success":
                    acc.success += 1
                elif kind == "warning":
                    acc.warning += 1
                    acc.warning_files[path_str] = acc.warning_files.get(path_str, 0) + 1
                else:
                    acc.failure += 1
                    acc.failure_files[path_str] = acc.failure_files.get(path_str, 0) + 1

    def __compute_checker_stats(self, info: types.PathInfo, paths: list[pathlib.Path]) -> None:
        paths_set = set(paths)
        checker_data: dict[str, CheckerAccumulator] = {}

        self.__accumulate_results(info.successes, paths_set, checker_data, "success")
        self.__accumulate_results(info.warnings, paths_set, checker_data, "warning")
        self.__accumulate_results(info.errors, paths_set, checker_data, "failure")

        for checker, acc in sorted(checker_data.items()):
            if (acc.warning == 0) and (acc.failure == 0):
                continue
            info.checker_stats.append(
                types.CheckerStats(
                    checker=checker,
                    success_count=acc.success,
                    warning_count=acc.warning,
                    failure_count=acc.failure,
                    warning_files=acc.warning_files,
                    failure_files=acc.failure_files,
                )
            )

    async def __successes_errors_warnings(
        self, release: sql.Release, latest_revision_number: str, info: types.PathInfo
    ) -> None:
        if release.committee is None:
            raise ValueError("Release has no committee - Invalid state")

        match_ignore = await self.__read_as.checks.ignores_matcher(release.committee.name)

        cs = types.ChecksSubset(
            release=release,
            latest_revision_number=latest_revision_number,
            info=info,
            match_ignore=match_ignore,
        )
        await self.__successes(cs)
        await self.__warnings(cs)
        await self.__errors(cs)

    async def __errors(self, cs: types.ChecksSubset) -> None:
        errors = await self.__data.check_result(
            release_name=cs.release.name,
            revision_number=cs.latest_revision_number,
            member_rel_path=None,
            status=sql.CheckResultStatus.FAILURE,
        ).all()
        for error in errors:
            if cs.match_ignore(error):
                cs.info.ignored_errors.append(error)
                continue
            if primary_rel_path := error.primary_rel_path:
                cs.info.errors.setdefault(pathlib.Path(primary_rel_path), []).append(error)

    async def __successes(self, cs: types.ChecksSubset) -> None:
        successes = await self.__data.check_result(
            release_name=cs.release.name,
            revision_number=cs.latest_revision_number,
            member_rel_path=None,
            status=sql.CheckResultStatus.SUCCESS,
        ).all()
        for success in successes:
            # Successes cannot be ignored
            if primary_rel_path := success.primary_rel_path:
                cs.info.successes.setdefault(pathlib.Path(primary_rel_path), []).append(success)

    async def __warnings(self, cs: types.ChecksSubset) -> None:
        warnings = await self.__data.check_result(
            release_name=cs.release.name,
            revision_number=cs.latest_revision_number,
            member_rel_path=None,
            status=sql.CheckResultStatus.WARNING,
        ).all()
        for warning in warnings:
            if cs.match_ignore(warning):
                cs.info.ignored_warnings.append(warning)
                continue
            if primary_rel_path := warning.primary_rel_path:
                cs.info.warnings.setdefault(pathlib.Path(primary_rel_path), []).append(warning)
