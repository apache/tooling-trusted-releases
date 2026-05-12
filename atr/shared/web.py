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

import collections
from typing import Final

import htpy

import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.types as types
import atr.util as util

_NON_NOTE_STATUSES: Final[tuple[sql.CheckResultStatus, ...]] = (
    sql.CheckResultStatus.SUGGESTION,
    sql.CheckResultStatus.CONCERN,
    sql.CheckResultStatus.BLOCKER,
    sql.CheckResultStatus.EXCEPTION,
)

_STATUS_BADGE_CLASSES: Final[dict[sql.CheckResultStatus, str]] = {
    sql.CheckResultStatus.SUGGESTION: ".bg-warning.text-dark",
    sql.CheckResultStatus.CONCERN: ".bg-danger",
    sql.CheckResultStatus.BLOCKER: ".atr-bg-blocker",
    sql.CheckResultStatus.EXCEPTION: ".atr-bg-exception",
}

_STATUS_LABELS: Final[dict[sql.CheckResultStatus, str]] = {
    sql.CheckResultStatus.SUGGESTION: "suggestion",
    sql.CheckResultStatus.CONCERN: "concern",
    sql.CheckResultStatus.BLOCKER: "blocker",
    sql.CheckResultStatus.EXCEPTION: "exception",
}


def render_checks_summary(
    info: types.PathInfo | None, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> htm.Element | None:
    if info is None:
        return None
    if (
        (not info.checker_stats)
        and (not info.release_level_concerns)
        and (not info.release_level_exceptions)
        and (not info.release_level_blockers)
    ):
        return None

    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header")[htpy.h5(".mb-0")["Checks summary"]]

    body = htm.Block(htm.div, classes=".card-body")

    release_problems_by_checker: dict[str, list[sql.CheckResult]] = {}
    for result in info.release_level_concerns:
        release_problems_by_checker.setdefault(result.checker, []).append(result)
    for result in info.release_level_exceptions:
        release_problems_by_checker.setdefault(result.checker, []).append(result)
    for result in info.release_level_blockers:
        release_problems_by_checker.setdefault(result.checker, []).append(result)

    stats_by_checker = {stat.checker: stat for stat in info.checker_stats}
    all_checkers = sorted(set(release_problems_by_checker) | {stat.checker for stat in info.checker_stats})

    for index, checker in enumerate(all_checkers):
        stat = stats_by_checker.get(checker)
        release_problems = release_problems_by_checker.get(checker, [])
        body.append(_render_checker_entry(stat, release_problems, index, project_key, version_key))

    card.append(body.collect())
    return card.collect()


def _checker_display_name(checker: str) -> str:
    return checker.removeprefix("atr.tasks.checks.").replace("_", " ").replace(".", " ").title()


def _render_checker_entry(
    stat: types.CheckerStats | None,
    release_problems: list[sql.CheckResult],
    index: int,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> htm.Element:
    stripe_class = ".atr-stripe-odd" if ((index % 2) == 0) else ".atr-stripe-even"
    details = htm.Block(htm.details, classes=f".mb-0.p-2{stripe_class}")

    checker = stat.checker if (stat is not None) else release_problems[0].checker
    counts: collections.Counter[sql.CheckResultStatus] = stat.counts if (stat is not None) else collections.Counter()
    release_counts = collections.Counter(p.status for p in release_problems)

    summary_content: list[htm.Element | str] = []
    for status in _NON_NOTE_STATUSES:
        total = counts[status] + release_counts[status]
        if total > 0:
            summary_content.append(htpy.span(f".badge{_STATUS_BADGE_CLASSES[status]}.me-2")[str(total)])
    summary_content.append(htpy.strong[_checker_display_name(checker)])
    details.summary[*summary_content]

    files_div = htm.Block(htm.div, classes=".mt-2.atr-checks-files")
    for result in release_problems:
        files_div.div[*_render_release_problem(result)]

    if stat is not None:
        _render_stat_files(files_div, stat, project_key, version_key)

    details.append(files_div.collect())
    return details.collect()


def _render_release_problem(result: sql.CheckResult) -> list[htm.Element | str]:
    status = result.status
    badge_class = _STATUS_BADGE_CLASSES.get(status, ".bg-danger")
    label_word = _STATUS_LABELS.get(status, "error")
    return [
        htpy.span(f".badge{badge_class}.me-2")[util.plural(1, label_word)],
        result.message,
    ]


def _render_stat_files(
    files_div: htm.Block,
    stat: types.CheckerStats,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> None:
    all_files: set[str] = set()
    for status in _NON_NOTE_STATUSES:
        all_files |= set(stat.files.get(status, {}))
    for file_path in sorted(all_files):
        report_url = f"/report/{project_key!s}/{version_key!s}/{file_path}"
        file_content: list[htm.Element | str] = []
        for status in _NON_NOTE_STATUSES:
            count = stat.files.get(status, {}).get(file_path, 0)
            if count > 0:
                badge_class = _STATUS_BADGE_CLASSES[status]
                label_word = _STATUS_LABELS[status]
                file_content.append(htpy.span(f".badge{badge_class}.me-2")[util.plural(count, label_word)])
        file_content.append(htpy.a(href=report_url)[htpy.strong[htpy.code[file_path]]])
        files_div.div[*file_content]
