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

from typing import Final, Literal

import htpy

import atr.blueprints.get as get
import atr.db as db
import atr.form as form
import atr.gates as gates
import atr.htm as htm
import atr.models.sql as sql
import atr.post as post
import atr.template as template
import atr.util as util
import atr.web as web

_FIELDS: Final = (
    ("Repository", "repository"),
    ("Committee", "committee_key"),
    ("Environment", "environment"),
    ("Workflow", "workflow"),
    ("Event", "event"),
    ("Ref type", "ref_type"),
    ("Branch or tag pattern", "ref_pattern"),
)


@get.typed
async def config(session: web.Committer, _gates_config: Literal["gates/config"]) -> str:
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        approved = await data.gate_config(status=sql.GateStatus.APPROVED).order_by(via(sql.GateConfig.decided)).all()
        pending = await data.gate_config(status=sql.GateStatus.PENDING).order_by(via(sql.GateConfig.id).desc()).all()

    current = {gate.repository_id: gate for gate in approved}
    proposals = [
        _proposal_table(proposal, current.get(proposal.repository_id), session.uid)
        for proposal in pending
        if proposal.previous_id == getattr(current.get(proposal.repository_id), "id", None)
    ]
    page = htm.Block()
    page.h1["Gate configuration"]
    page.p[
        f"""A change to a gate takes effect once {gates.THRESHOLD} active members of the repository's
        release team approve it. Proposing a change counts as your approval."""
    ]
    page.h2["Gates"]
    page.div[[_gate_table(gate) for gate in current.values()] or htm.p["There are no gates."]]
    page.h2["Proposed changes"]
    page.div[proposals or htm.p["There are no proposed changes."]]
    page.h2["Propose a gate or a change"]
    page.form(method="post", action=util.as_url(post.gates.propose))[
        form.csrf_input(),
        *(
            htm.div(".mb-3")[
                htpy.label(".form-label", for_=name)[label],
                _field(name),
            ]
            for label, name in _FIELDS
        ),
        htm.button(".btn.btn-primary", type="submit")["Propose"],
    ]
    return await template.blank("Gate configuration", content=page.collect())


@get.typed
async def index(session: web.Committer, _gates: Literal["gates"]) -> str:
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        requests = await data.gate_request().order_by(via(sql.GateRequest.id).desc()).all()
        approved = await data.gate_request(status=sql.GateStatus.APPROVED).order_by(via(sql.GateRequest.decided)).all()
        configs = await data.gate_config(status=sql.GateStatus.APPROVED).order_by(via(sql.GateConfig.decided)).all()

    current = {gate.repository_id: gate for gate in configs}
    latest = {request.repository_id: request.head_sha for request in approved}
    active = []
    resolved = []
    for request in requests:
        gate = current.get(request.repository_id)
        if gate is None:
            continue
        table = _table(request, gate, latest.get(request.repository_id), session.uid)
        if request.status == sql.GateStatus.PENDING:
            active.append(table)
        else:
            resolved.append(table)

    page = htm.Block()
    page.h1["Gates"]
    page.p[
        f"""A gated run continues once {gates.THRESHOLD} active members of the repository's release
        team approve it here. Review the changes and the workflow before you approve."""
    ]
    page.p[htm.a(href=util.as_url(config))["Manage gates"]]
    page.h2["Active requests"]
    page.div[active or htm.p["There are no active requests."]]
    page.h2["Resolved requests"]
    page.div[resolved or htm.p["There are no resolved requests."]]
    return await template.blank("Gates", content=page.collect())


@get.typed
async def status(
    _session: web.Public,
    _gates_status: Literal["gates/status"],
    repository_id: int,
    run_id: int,
    run_attempt: int,
) -> web.Response:
    async with db.session() as data:
        request = await data.gate_request(repository_id=repository_id, run_id=run_id, run_attempt=run_attempt).get()
    if request is None:
        return web.TextResponse("NOT_FOUND\n", status=404)
    return web.TextResponse(f"{request.status.name}\n")


def _current_decisions(row: sql.GateConfig | sql.GateRequest) -> str:
    decisions = ", ".join(f"{uid} {'approved' if ok else 'rejected'}" for uid, ok in sorted(row.decisions.items()))
    if row.status != sql.GateStatus.PENDING:
        return decisions
    approvals = len(row.decisions)
    progress = f"{approvals} of {gates.THRESHOLD} approvals, waiting for {gates.THRESHOLD - approvals} more"
    return f"{progress}: {decisions}" if decisions else progress


def _decision(row: sql.GateConfig | sql.GateRequest, uid: str, action: str) -> htm.Element | str:
    if uid in row.decisions:
        return "approved" if row.decisions[uid] else "rejected"
    return htm.form(method="post", action=action)[
        form.csrf_input(),
        htpy.input(type="hidden", name="id", value=str(row.id)),
        htm.button(".btn.btn-sm.btn-success.me-1", type="submit", name="decision", value="approve")["Approve"],
        htm.button(".btn.btn-sm.btn-danger", type="submit", name="decision", value="reject")["Reject"],
    ]


def _field(name: str) -> htm.Element | htm.VoidElement:
    if name == "ref_type":
        return htm.div[
            [
                htm.div(".form-check.form-check-inline")[
                    htpy.input(
                        ".form-check-input", type="radio", id=ref_type, name=name, value=ref_type, required=True
                    ),
                    htpy.label(".form-check-label", for_=ref_type)[ref_type],
                ]
                for ref_type in gates.REF_TYPES
            ]
        ]
    choices = {"committee_key": tuple(gates.TEAMS), "event": gates.EVENTS}.get(name)
    if choices is None:
        return htpy.input(".form-control", id=name, name=name, required=True)
    return htpy.select(".form-select", id=name, name=name, required=True)[
        [htpy.option(value=choice)[choice] for choice in choices]
    ]


def _gate_table(gate: sql.GateConfig) -> htm.Element:
    return htm.table(".table.table-striped.table-bordered.align-middle.mb-5")[
        htpy.colgroup[htpy.col(".w-25"), htpy.col],
        htm.tbody[
            [htm.tr[htm.th[label], htm.td[htm.code[getattr(gate, name)]]] for label, name in _FIELDS],
            htm.tr[htm.th["Team"], htm.td[htm.code[gates.TEAMS[gate.committee_key]]]],
            htm.tr[htm.th["Approvals needed"], htm.td[str(gates.THRESHOLD)]],
        ],
    ]


def _proposal_table(proposal: sql.GateConfig, before: sql.GateConfig | None, uid: str) -> htm.Element:
    action = util.as_url(post.gates.decide_config)
    return htm.table(".table.table-striped.table-bordered.align-middle.mb-5")[
        htpy.colgroup[htpy.col(".w-25"), htpy.col, htpy.col],
        htm.thead[htm.tr[htm.th, htm.th["Current"], htm.th["Proposed"]]],
        htm.tbody[
            [
                htm.tr[
                    htm.th[label],
                    htm.td[htm.code[getattr(before, name)] if before else "none"],
                    htm.td[htm.code[getattr(proposal, name)]],
                ]
                for label, name in _FIELDS
            ],
            htm.tr[htm.th["Current decisions"], htm.td(colspan="2")[_current_decisions(proposal)]],
            htm.tr[htm.th["Your decision"], htm.td(colspan="2")[_decision(proposal, uid, action)]],
        ],
    ]


def _table(request: sql.GateRequest, gate: sql.GateConfig, latest_sha: str | None, uid: str) -> htm.Element:
    github = f"https://github.com/{gate.repository}"
    if latest_sha is None:
        changes = htm.a(href=f"{github}/tree/{request.head_sha}")["Full tree"]
    else:
        changes = htm.a(href=f"{github}/compare/{latest_sha}..{request.head_sha}")["Since latest approved"]
    starter = f"{request.actor_asf_uid or 'no linked ASF account'} ({request.actor} on GitHub)"
    decision_row = htm.tr[htm.th["Outcome"], htm.td[request.status.value]]
    if request.status == sql.GateStatus.PENDING:
        decision = _decision(request, uid, util.as_url(post.gates.decide))
        decision_row = htm.tr[htm.th["Your decision"], htm.td[decision]]
    run_url = f"{github}/actions/runs/{request.run_id}/attempts/{request.run_attempt}"
    return htm.table(".table.table-striped.table-bordered.align-middle.mb-5")[
        htpy.colgroup[htpy.col(".w-25"), htpy.col],
        htm.tbody[
            htm.tr[htm.th["Repository"], htm.td[gate.repository]],
            htm.tr[htm.th["Ref"], htm.td[htm.code[request.ref], " ", htm.code[request.head_sha]]],
            htm.tr[
                htm.th["Changes"],
                htm.td[changes, " · ", htm.a(href=f"{github}/blob/{request.head_sha}/{gate.workflow}")["Workflow"]],
            ],
            htm.tr[htm.th["Run"], htm.td[htm.a(href=run_url)[f"{request.run_id} attempt {request.run_attempt}"]]],
            htm.tr[htm.th["Started by ASF UID"], htm.td[starter]],
            htm.tr[htm.th["Current decisions"], htm.td[_current_decisions(request)]],
            decision_row,
        ],
    ]
