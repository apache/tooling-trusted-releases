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

from typing import Literal

import htpy

import atr.get as get
import atr.htm as htm
import atr.util as util

type Phase = Literal["COMPOSE", "VOTE", "FINISH"]


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
) -> htm.Element:
    radios = []
    for recipient in recipients:
        radio_id = f"email_to_{recipient.replace('@', '_').replace('.', '_')}"
        radio_attrs: dict[str, str] = {
            "type": "radio",
            "name": "email_to",
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
