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

    for error in info.release_level_errors:
        status_class = ".atr-bg-blocker" if (error.status == sql.CheckResultStatus.BLOCKER) else ".bg-danger"
        body.div(f".alert{status_class}.text-white")[error.message]
    for i, stat in enumerate(info.checker_stats):
        body.append(_render_checker_stat(stat, i, project_key, version_key))

    card.append(body.collect())
    return card.collect()


def _checker_display_name(checker: str) -> str:
    return checker.removeprefix("atr.tasks.checks.").replace("_", " ").replace(".", " ").title()


def _render_checker_stat(
    stat: types.CheckerStats, index: int, project_key: safe.ProjectKey, version_key: safe.VersionKey
) -> htm.Element:
    stripe_class = ".atr-stripe-odd" if ((index % 2) == 0) else ".atr-stripe-even"
    details = htm.Block(htm.details, classes=f".mb-0.p-2{stripe_class}")

    summary_content: list[htm.Element | str] = []
    if stat.warning_count > 0:
        summary_content.append(htpy.span(".badge.bg-warning.text-dark.me-2")[str(stat.warning_count)])
    if stat.failure_count > 0:
        summary_content.append(htpy.span(".badge.bg-danger.me-2")[str(stat.failure_count)])
    if stat.blocker_count > 0:
        summary_content.append(htpy.span(".badge.atr-bg-blocker.me-2")[str(stat.blocker_count)])
    summary_content.append(htpy.strong[_checker_display_name(stat.checker)])

    details.summary[*summary_content]

    files_div = htm.Block(htm.div, classes=".mt-2.atr-checks-files")
    all_files = set(stat.failure_files.keys()) | set(stat.warning_files.keys()) | set(stat.blocker_files.keys())
    for file_path in sorted(all_files):
        report_url = f"/report/{project_key!s}/{version_key!s}/{file_path}"
        error_count = stat.failure_files.get(file_path, 0)
        blocker_count = stat.blocker_files.get(file_path, 0)
        warning_count = stat.warning_files.get(file_path, 0)

        file_content: list[htm.Element | str] = []
        if error_count > 0:
            file_content.append(htpy.span(".badge.bg-danger.me-2")[util.plural(error_count, "error")])
        if blocker_count > 0:
            file_content.append(htpy.span(".badge.atr-bg-blocker.me-2")[util.plural(blocker_count, "blocker")])
        if warning_count > 0:
            file_content.append(htpy.span(".badge.bg-warning.text-dark.me-2")[util.plural(warning_count, "warning")])
        file_content.append(htpy.a(href=report_url)[htpy.strong[htpy.code[file_path]]])

        files_div.div[*file_content]

    details.append(files_div.collect())
    return details.collect()
