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

import htpy

import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.types as types
import atr.util as util


def render_checks_summary(
    info: types.PathInfo | None, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> htm.Element | None:
    if info is None:
        return None
    if (not info.checker_stats) and (not info.release_level_errors):
        return None

    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header")[htpy.h5(".mb-0")["Checks summary"]]

    body = htm.Block(htm.div, classes=".card-body")

    release_errors_by_checker: dict[str, list[sql.CheckResult]] = {}
    for error in info.release_level_errors:
        release_errors_by_checker.setdefault(error.checker, []).append(error)

    stats_by_checker = {stat.checker: stat for stat in info.checker_stats}
    all_checkers = sorted(set(release_errors_by_checker) | {s.checker for s in info.checker_stats})

    for index, checker in enumerate(all_checkers):
        stat = stats_by_checker.get(checker)
        release_errors = release_errors_by_checker.get(checker, [])
        body.append(_render_checker_entry(stat, release_errors, index, project_key, version_key))

    card.append(body.collect())
    return card.collect()


def _checker_display_name(checker: str) -> str:
    return checker.removeprefix("atr.tasks.checks.").replace("_", " ").replace(".", " ").title()


def _render_checker_entry(
    stat: types.CheckerStats | None,
    release_errors: list[sql.CheckResult],
    index: int,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> htm.Element:
    stripe_class = ".atr-stripe-odd" if ((index % 2) == 0) else ".atr-stripe-even"
    details = htm.Block(htm.details, classes=f".mb-0.p-2{stripe_class}")

    checker = stat.checker if (stat is not None) else release_errors[0].checker
    release_failure_count = sum(1 for e in release_errors if e.status != sql.CheckResultStatus.BLOCKER)
    release_blocker_count = sum(1 for e in release_errors if e.status == sql.CheckResultStatus.BLOCKER)
    warning_count = stat.warning_count if (stat is not None) else 0
    failure_count = (stat.failure_count if (stat is not None) else 0) + release_failure_count
    blocker_count = (stat.blocker_count if (stat is not None) else 0) + release_blocker_count

    summary_content: list[htm.Element | str] = []
    if warning_count > 0:
        summary_content.append(htpy.span(".badge.bg-warning.text-dark.me-2")[str(warning_count)])
    if failure_count > 0:
        summary_content.append(htpy.span(".badge.bg-danger.me-2")[str(failure_count)])
    if blocker_count > 0:
        summary_content.append(htpy.span(".badge.atr-bg-blocker.me-2")[str(blocker_count)])
    summary_content.append(htpy.strong[_checker_display_name(checker)])

    details.summary[*summary_content]

    files_div = htm.Block(htm.div, classes=".mt-2.atr-checks-files")

    for error in release_errors:
        is_blocker = error.status == sql.CheckResultStatus.BLOCKER
        badge_class = ".atr-bg-blocker" if is_blocker else ".bg-danger"
        label = util.plural(1, "blocker") if is_blocker else util.plural(1, "error")
        files_div.div[
            htpy.span(f".badge{badge_class}.me-2")[label],
            error.message,
        ]

    if stat is not None:
        all_files = set(stat.failure_files.keys()) | set(stat.warning_files.keys()) | set(stat.blocker_files.keys())
        for file_path in sorted(all_files):
            report_url = f"/report/{project_key!s}/{version_key!s}/{file_path}"
            file_error_count = stat.failure_files.get(file_path, 0)
            file_blocker_count = stat.blocker_files.get(file_path, 0)
            file_warning_count = stat.warning_files.get(file_path, 0)

            file_content: list[htm.Element | str] = []
            if file_error_count > 0:
                file_content.append(htpy.span(".badge.bg-danger.me-2")[util.plural(file_error_count, "error")])
            if file_blocker_count > 0:
                file_content.append(htpy.span(".badge.atr-bg-blocker.me-2")[util.plural(file_blocker_count, "blocker")])
            if file_warning_count > 0:
                file_content.append(
                    htpy.span(".badge.bg-warning.text-dark.me-2")[util.plural(file_warning_count, "warning")]
                )
            file_content.append(htpy.a(href=report_url)[htpy.strong[htpy.code[file_path]]])

            files_div.div[*file_content]

    details.append(files_div.collect())
    return details.collect()
