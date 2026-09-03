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
import dataclasses

import atr.analysis as analysis
import atr.classify as classify
import atr.db as db
import atr.db.interaction as interaction
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.storage.datatypes as datatypes


@dataclasses.dataclass
class CheckerAccumulator:
    counts: collections.Counter[sql.CheckResultStatus] = dataclasses.field(default_factory=collections.Counter)
    files: dict[sql.CheckResultStatus, dict[str, int]] = dataclasses.field(default_factory=dict)


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

    async def path_info(self, release: sql.Release, all_paths: list[safe.RelPath]) -> datatypes.PathInfo | None:
        info = datatypes.PathInfo()
        latest_revision_number = release.latest_revision_number
        if latest_revision_number is None:
            return None
        tally = await interaction.checks_tally_for(
            release,
            revision=release.safe_latest_revision_number,
            statuses=sql.CHECK_RESULT_NON_NOTE_STATUSES,
            caller_data=self.__data,
        )
        await self.__suggestions_concerns(release, info, tally.results)
        self.__note_counts(info, tally.counts)
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
            if isinstance(path, safe.RelDirPath):
                info.file_types[path] = classify.FileType.DIRECTORY
            else:
                db_value = db_classifications.get(str(path))
                if db_value is not None:
                    info.file_types[path] = classify.FileType(db_value)
                else:
                    info.file_types[path] = await classify.classify(
                        path, base_path=base_path, source_matcher=source_matcher, binary_matcher=binary_matcher
                    )
        self.__pair_sboms(info, all_paths)
        self.__compute_checker_stats(info, all_paths, tally.counts)
        return info

    def __accumulate_results(
        self,
        results: dict[safe.RelPath, list[sql.CheckResult]],
        paths_set: set[safe.RelPath],
        checker_data: dict[str, CheckerAccumulator],
    ) -> None:
        for path, results_list in results.items():
            if path not in paths_set:
                continue
            for result in results_list:
                acc = checker_data.setdefault(result.checker, CheckerAccumulator())
                status = result.status
                acc.counts[status] += 1
                if status != sql.CheckResultStatus.NOTE:
                    path_str = str(path)
                    files_for_status = acc.files.setdefault(status, {})
                    files_for_status[path_str] = files_for_status.get(path_str, 0) + 1

    def __compute_checker_stats(
        self, info: datatypes.PathInfo, paths: list[safe.RelPath], counts: list[interaction.CheckCount]
    ) -> None:
        paths_set = set(paths)
        path_strs = {str(path) for path in paths}
        checker_data: dict[str, CheckerAccumulator] = {}

        for count in counts:
            if (count.status == sql.CheckResultStatus.NOTE) and (count.primary_rel_path in path_strs):
                checker_data.setdefault(count.checker, CheckerAccumulator()).counts[count.status] += count.total
        self.__accumulate_results(info.suggestions, paths_set, checker_data)
        self.__accumulate_results(info.concerns, paths_set, checker_data)
        self.__accumulate_results(info.exceptions, paths_set, checker_data)
        self.__accumulate_results(info.blockers, paths_set, checker_data)

        for checker, acc in sorted(checker_data.items()):
            non_note_total = sum(
                count for status, count in acc.counts.items() if (status != sql.CheckResultStatus.NOTE)
            )
            if non_note_total == 0:
                continue
            info.checker_stats.append(
                datatypes.CheckerStats(
                    checker=checker,
                    counts=acc.counts,
                    files=acc.files,
                )
            )

    def __pair_sboms(self, info: datatypes.PathInfo, paths: list[safe.RelPath]) -> None:
        # Only the JSON suffixes pair here, because the SBOM tooling reads JSON alone
        path_strs = {str(path) for path in paths}
        for path in paths:
            for candidate in analysis.sbom_candidates(str(path), analysis.CYCLONEDX_JSON_SUFFIXES):
                if candidate in path_strs:
                    info.sbom_paths[path] = safe.RelPath(candidate)
                    break

    async def __suggestions_concerns(
        self, release: sql.Release, info: datatypes.PathInfo, results: list[sql.CheckResult]
    ) -> None:
        match_ignore = await self.__read_as.checks.ignores_matcher(release.safe_project_key)

        cs = datatypes.ChecksSubset(
            checks=results,
            info=info,
            match_ignore=match_ignore,
        )
        # TODO: These get just the ones for the revision.
        # It might be better to get all like we do in by_release_path, filter by hash, then filter by status
        await self.__suggestions(cs)
        await self.__concerns(cs)
        await self.__exceptions(cs)
        await self.__blocker(cs)

    async def __blocker(self, cs: datatypes.ChecksSubset) -> None:
        blocker = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.BLOCKER]
        for result in blocker:
            if path := result.safe_primary_rel_path:
                cs.info.blockers.setdefault(path, []).append(result)
            else:
                cs.info.release_level_blockers.append(result)

    async def __concerns(self, cs: datatypes.ChecksSubset) -> None:
        concerns = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.CONCERN]
        for concern in concerns:
            if cs.match_ignore(concern):
                cs.info.ignored_concerns.append(concern)
                continue
            if path := concern.safe_primary_rel_path:
                cs.info.concerns.setdefault(path, []).append(concern)
            else:
                cs.info.release_level_concerns.append(concern)

    async def __exceptions(self, cs: datatypes.ChecksSubset) -> None:
        exceptions = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.EXCEPTION]
        for exception in exceptions:
            if cs.match_ignore(exception):
                cs.info.ignored_exceptions.append(exception)
                continue
            if path := exception.safe_primary_rel_path:
                cs.info.exceptions.setdefault(path, []).append(exception)
            else:
                cs.info.release_level_exceptions.append(exception)

    def __note_counts(self, info: datatypes.PathInfo, counts: list[interaction.CheckCount]) -> None:
        for count in counts:
            if (count.status == sql.CheckResultStatus.NOTE) and count.primary_rel_path:
                path = safe.RelPath(count.primary_rel_path)
                info.note_counts[path] = info.note_counts.get(path, 0) + count.total

    async def __suggestions(self, cs: datatypes.ChecksSubset) -> None:
        suggestions = [cr for cr in cs.checks if cr.status == sql.CheckResultStatus.SUGGESTION]
        for suggestion in suggestions:
            if cs.match_ignore(suggestion):
                cs.info.ignored_suggestions.append(suggestion)
                continue
            if path := suggestion.safe_primary_rel_path:
                cs.info.suggestions.setdefault(path, []).append(suggestion)
            else:
                cs.info.release_level_suggestions.append(suggestion)
