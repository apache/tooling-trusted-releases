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

import atr.classify as classify
import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.storage.types as types


@dataclasses.dataclass
class CheckerAccumulator:
    success: int = 0
    warning: int = 0
    failure: int = 0
    blocker: int = 0
    warning_files: dict[str, int] = dataclasses.field(default_factory=dict)
    failure_files: dict[str, int] = dataclasses.field(default_factory=dict)
    blocker_files: dict[str, int] = dataclasses.field(default_factory=dict)


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

    async def path_info(self, release: sql.Release, all_paths: list[safe.RelPath]) -> types.PathInfo | None:
        info = types.PathInfo()
        latest_revision_number = release.latest_revision_number
        if latest_revision_number is None:
            return None
        await self.__successes_errors_warnings(release, release.safe_latest_revision_number, info)
        base_path = paths.release_directory(release)
        revision_seq = int(str(release.safe_latest_revision_number))
        db_classifications = await self.__data.release_file_classifications_at(release.key, revision_seq)
        # TODO: This should get the matchers from attestable data policy
        # But this branch is only a fallback for pre-AttestableV2 releases
        source_matcher, binary_matcher = classify.matchers_from_policy(
            release.project.policy_source_artifact_paths,
            release.project.policy_binary_artifact_paths,
            base_path,
        )
        for path in all_paths:
            db_value = db_classifications.get(str(path))
            if db_value is not None:
                info.file_types[path] = classify.FileType(db_value)
            else:
                info.file_types[path] = classify.classify(
                    path, base_path=base_path, source_matcher=source_matcher, binary_matcher=binary_matcher
                )
        self.__compute_checker_stats(info, all_paths)
        return info

    def __accumulate_results(
        self,
        results: dict[safe.RelPath, list[sql.CheckResult]],
        paths_set: set[safe.RelPath],
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
                elif result.status == sql.CheckResultStatus.BLOCKER:
                    acc.blocker += 1
                    acc.blocker_files[path_str] = acc.blocker_files.get(path_str, 0) + 1
                else:
                    acc.failure += 1
                    acc.failure_files[path_str] = acc.failure_files.get(path_str, 0) + 1

    def __compute_checker_stats(self, info: types.PathInfo, paths: list[safe.RelPath]) -> None:
        paths_set = set(paths)
        checker_data: dict[str, CheckerAccumulator] = {}

        self.__accumulate_results(info.successes, paths_set, checker_data, "success")
        self.__accumulate_results(info.warnings, paths_set, checker_data, "warning")
        self.__accumulate_results(info.errors, paths_set, checker_data, "failure")

        for checker, acc in sorted(checker_data.items()):
            if (acc.warning == 0) and (acc.failure == 0) and (acc.blocker == 0):
                continue
            info.checker_stats.append(
                types.CheckerStats(
                    checker=checker,
                    success_count=acc.success,
                    warning_count=acc.warning,
                    failure_count=acc.failure,
                    blocker_count=acc.blocker,
                    warning_files=acc.warning_files,
                    failure_files=acc.failure_files,
                    blocker_files=acc.blocker_files,
                )
            )

    async def __successes_errors_warnings(
        self, release: sql.Release, latest_revision_number: safe.RevisionNumber, info: types.PathInfo
    ) -> None:
        match_ignore = await self.__read_as.checks.ignores_matcher(release.safe_project_key)
        attestable_checks = await interaction.checks_for(
            release, revision=latest_revision_number, caller_data=self.__data
        )

        cs = types.ChecksSubset(
            checks=attestable_checks,
            info=info,
            match_ignore=match_ignore,
        )
        # TODO: These get just the ones for the revision.
        # It might be better to get all like we do in by_release_path, filter by hash, then filter by status
        await self.__successes(cs)
        await self.__warnings(cs)
        await self.__errors(cs)
        await self.__blocker(cs)

    async def __blocker(self, cs: types.ChecksSubset) -> None:
        blocker = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.BLOCKER]
        for result in blocker:
            if path := result.safe_primary_rel_path:
                cs.info.errors.setdefault(path, []).append(result)

    async def __errors(self, cs: types.ChecksSubset) -> None:
        errors = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.FAILURE]
        for error in errors:
            if cs.match_ignore(error):
                cs.info.ignored_errors.append(error)
                continue
            if path := error.safe_primary_rel_path:
                cs.info.errors.setdefault(path, []).append(error)

    async def __successes(self, cs: types.ChecksSubset) -> None:
        successes = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.SUCCESS]
        for success in successes:
            # Successes cannot be ignored
            if path := success.safe_primary_rel_path:
                cs.info.successes.setdefault(path, []).append(success)

    async def __warnings(self, cs: types.ChecksSubset) -> None:
        warnings = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.WARNING]
        for warning in warnings:
            if cs.match_ignore(warning):
                cs.info.ignored_warnings.append(warning)
                continue
            if path := warning.safe_primary_rel_path:
                cs.info.warnings.setdefault(path, []).append(warning)
