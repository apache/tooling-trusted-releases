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
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final, Literal

import htpy

import atr.get as get
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage.types as types
import atr.util as util

type Phase = Literal["COMPOSE", "VOTE", "FINISH"]

BANNER_STATUS: Final[sql.CheckResultStatus] = sql.CheckResultStatus.EXCEPTION

CELL_TEXT_CLASS: Final[dict[sql.CheckResultStatus, str]] = {
    sql.CheckResultStatus.SUGGESTION: "atr-text-suggestion",
    sql.CheckResultStatus.CONCERN: "atr-text-concern",
    sql.CheckResultStatus.BLOCKER: "atr-text-blocker",
}

COLUMN_HEADERS: Final[dict[sql.CheckResultStatus, str]] = {
    sql.CheckResultStatus.SUGGESTION: "Suggestions",
    sql.CheckResultStatus.CONCERN: "Concerns",
    sql.CheckResultStatus.BLOCKER: "Blockers",
}

HIDDEN_STATUSES: Final[tuple[sql.CheckResultStatus, ...]] = (sql.CheckResultStatus.NOTE,)

PATH_STYLE_CLASS: Final[dict[sql.CheckResultStatus, str]] = {
    sql.CheckResultStatus.BLOCKER: "atr-text-blocker",
    sql.CheckResultStatus.EXCEPTION: "text-danger",
    sql.CheckResultStatus.CONCERN: "atr-text-concern",
    sql.CheckResultStatus.SUGGESTION: "atr-text-suggestion",
}

TABLE_STATUSES: Final[tuple[sql.CheckResultStatus, ...]] = (
    sql.CheckResultStatus.SUGGESTION,
    sql.CheckResultStatus.CONCERN,
    sql.CheckResultStatus.BLOCKER,
)

_CHECKBOX_ID_SANITIZE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9]+")

_NON_NOTE_STATUSES: Final[tuple[sql.CheckResultStatus, ...]] = (
    sql.CheckResultStatus.SUGGESTION,
    sql.CheckResultStatus.CONCERN,
    sql.CheckResultStatus.BLOCKER,
    sql.CheckResultStatus.EXCEPTION,
)

_SEVERITY_ORDER: Final[tuple[sql.CheckResultStatus, ...]] = (
    sql.CheckResultStatus.BLOCKER,
    sql.CheckResultStatus.EXCEPTION,
    sql.CheckResultStatus.CONCERN,
    sql.CheckResultStatus.SUGGESTION,
    sql.CheckResultStatus.NOTE,
)

_STATUS_BADGE_CLASSES: Final[dict[sql.CheckResultStatus, str]] = {
    sql.CheckResultStatus.SUGGESTION: ".atr-bg-suggestion",
    sql.CheckResultStatus.CONCERN: ".atr-bg-concern",
    sql.CheckResultStatus.BLOCKER: ".atr-bg-blocker",
    sql.CheckResultStatus.EXCEPTION: ".atr-bg-exception",
}

_STATUS_LABELS: Final[dict[sql.CheckResultStatus, str]] = {
    sql.CheckResultStatus.SUGGESTION: "suggestion",
    sql.CheckResultStatus.CONCERN: "concern",
    sql.CheckResultStatus.BLOCKER: "blocker",
    sql.CheckResultStatus.EXCEPTION: "exception",
}


def archived_project_banner(project: sql.Project) -> htm.Element | None:
    # Shown on the five top-level release pages when the PMC has archived the
    # project. The storage layer still refuses the write, but the banner tells
    # the viewer why the buttons are inert.
    if project.status != sql.ProjectStatus.RETIRED:
        return None
    return htm.div(".alert.alert-warning.mb-4")["This project is archived. Release actions are disabled."]


def highest_severity(
    counts: Mapping[sql.CheckResultStatus, int],
) -> sql.CheckResultStatus | None:
    for status in _SEVERITY_ORDER:
        if counts.get(status, 0) > 0:
            return status
    return None


def html_concerns_noted_checkboxes(
    groups: Sequence[util.ConcernGroup],
    checked: Iterable[str] = (),
) -> htm.Element:
    if not groups:
        raise ValueError("groups must be non-empty")
    checked_set = set(checked)
    boxes: list[htm.Element] = []
    for index, group in enumerate(groups):
        sanitized = _CHECKBOX_ID_SANITIZE.sub("_", group.checker)
        checkbox_id = f"concerns_noted_{index}_{sanitized}"
        label_text = f"{group.label} ({group.count})"
        attrs: dict[str, Any] = {
            "type": "checkbox",
            "name": "concerns_noted",
            "id": checkbox_id,
            "value": group.checker,
            "class_": "form-check-input",
        }
        if group.checker in checked_set:
            attrs["checked"] = True
        boxes.append(
            htm.div(".form-check.form-check-inline")[
                htpy.input(**attrs),
                htpy.label(for_=checkbox_id, class_="form-check-label")[label_text],
            ]
        )

    helper = htm.div(".form-text.mt-1")[
        "Each concern group must have been manually reviewed. Checking a box asserts that the review was done."
    ]
    group_attrs: dict[str, Any] = {
        "id": "concerns_noted",
        "role": "group",
        "aria_label": "Concerns noted",
    }
    return htpy.div(**group_attrs)[
        htm.div(".d-flex.flex-wrap.gap-3")[boxes],
        helper,
    ]


def html_nav(container: htm.Block, back_url: str, back_anchor: str, phase: Phase) -> None:
    classes = ".d-flex.justify-content-between.align-items-center"
    block = htm.Block(htm.p, classes=classes)
    block.a(".atr-back-link", href=back_url)[f"← Back to {back_anchor}"]
    span = htm.Block(htm.span, classes=".atr-phase-nav")

    def _phase(actual: Phase, expected: Phase) -> None:
        match expected:
            case "COMPOSE":
                symbol = "①"
            case "VOTE":
                symbol = "②"
            case "FINISH":
                symbol = "③"
        if actual == expected:
            span.strong(f".atr-phase-{actual}.atr-phase-symbol")[symbol]
            span.span(f".atr-phase-{actual}.atr-phase-label")[actual]
        else:
            span.span(".atr-phase-symbol-other")[symbol]

    _phase(phase, "COMPOSE")
    span.span(".atr-phase-arrow")["→"]
    _phase(phase, "VOTE")
    span.span(".atr-phase-arrow")["→"]
    _phase(phase, "FINISH")

    block.append(span.collect(separator=" "))
    container.append(block)


def html_nav_phase(block: htm.Block, project: str, version: str, staging: bool) -> None:
    label: Phase
    route, label = (get.compose.selected, "COMPOSE")
    if not staging:
        route, label = (get.finish.selected, "FINISH")
    html_nav(
        block,
        util.as_url(
            route,
            project_key=project,
            version_key=version,
        ),
        back_anchor=f"{label.title()} {project} {version}",
        phase=label,
    )


def html_recipients_cc_bcc_table(recipients: list[str]) -> htm.Element:
    header = htpy.thead[
        htpy.tr[
            htpy.th(".text-center.atr-checkbox-col")["CC"],
            htpy.th(".text-center.atr-checkbox-col")["BCC"],
            htpy.th["Recipient"],
        ]
    ]
    rows = []
    for recipient in recipients:
        rows.append(
            htpy.tr[
                htpy.td(".text-center.atr-checkbox-col")[
                    htpy.input(type="checkbox", name="email_cc", value=recipient, class_="form-check-input")
                ],
                htpy.td(".text-center.atr-checkbox-col")[
                    htpy.input(type="checkbox", name="email_bcc", value=recipient, class_="form-check-input")
                ],
                htpy.td[recipient],
            ]
        )
    return htpy.table(".table.table-bordered.mb-0")[header, htpy.tbody[rows]]


def html_recipients_to_radios(
    recipients: list[str],
    default_to: str | None = None,
    documentation: str | None = None,
    field_name: str = "email_to",
) -> htm.Element:
    radios = []
    for recipient in recipients:
        radio_id = f"{field_name}_{recipient.replace('@', '_').replace('.', '_')}"
        radio_attrs: dict[str, str] = {
            "type": "radio",
            "name": field_name,
            "id": radio_id,
            "value": recipient,
            "class_": "form-check-input",
        }
        if recipient == default_to:
            radio_attrs["checked"] = ""
        radios.append(
            htpy.div(".form-check")[
                htpy.input(**radio_attrs),
                htpy.label(".form-check-label", for_=radio_id)[recipient],
            ]
        )
    container = htm.div[radios]
    if documentation is None:
        return container
    return htm.div[container, htm.div(".text-muted.mt-1.form-text")[documentation]]


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


def render_exception_banner(info: types.PathInfo) -> htm.Element | None:
    path_count = sum(len(results) for results in info.exceptions.values())
    release_count = len(info.release_level_exceptions)
    total = path_count + release_count
    if total == 0:
        return None

    affected_paths = sorted(str(p) for p in info.exceptions)
    check_word = "check" if (total == 1) else "checks"

    children: list[htm.Element | str] = [
        htpy.i(".bi.bi-cone-striped.me-2"),
        htpy.strong[f"ATR could not complete {total} automated {check_word}."],
        " These results indicate a tooling failure, not a confirmed release-candidate issue. ",
    ]
    if affected_paths:
        children.append("Affected files: ")
        for index, path_str in enumerate(affected_paths):
            if index > 0:
                children.append(", ")
            children.append(htpy.code[path_str])
        children.append(". ")
    if release_count > 0:
        word = "exception" if (release_count == 1) else "exceptions"
        children.append(f"{release_count} release-level {word}.")
    return htm.div(".alert.atr-bg-exception.mb-3", role="alert")[*children]


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
    summary_content.append(htpy.strong[util.checker_display_name(checker)])
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
