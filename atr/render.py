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

from collections.abc import Mapping
from typing import Final, Literal

import htpy

import atr.get as get
import atr.htm as htm
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

_SEVERITY_ORDER: Final[tuple[sql.CheckResultStatus, ...]] = (
    sql.CheckResultStatus.BLOCKER,
    sql.CheckResultStatus.EXCEPTION,
    sql.CheckResultStatus.CONCERN,
    sql.CheckResultStatus.SUGGESTION,
    sql.CheckResultStatus.NOTE,
)


def highest_severity(
    counts: Mapping[sql.CheckResultStatus, int],
) -> sql.CheckResultStatus | None:
    for status in _SEVERITY_ORDER:
        if counts.get(status, 0) > 0:
            return status
    return None


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
