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


@get.typed
async def index(session: web.Committer, _gates: Literal["gates"]) -> str:
    via = sql.validate_instrumented_attribute
    async with db.session() as data:
        requests = await data.gate_request().order_by(via(sql.GateRequest.id).desc()).all()
        approved = await data.gate_request(status=sql.GateStatus.APPROVED).order_by(via(sql.GateRequest.decided)).all()

    latest = {request.repository_id: request.head_sha for request in approved}
    active = []
    resolved = []
    for request in requests:
        gate = gates.gate(request.repository_id)
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


def _current_decisions(request: sql.GateRequest) -> str:
    decisions = ", ".join(f"{uid} {'approved' if ok else 'rejected'}" for uid, ok in sorted(request.decisions.items()))
    if request.status != sql.GateStatus.PENDING:
        return decisions
    approvals = len(request.decisions)
    progress = f"{approvals} of {gates.THRESHOLD} approvals, waiting for {gates.THRESHOLD - approvals} more"
    return f"{progress}: {decisions}" if decisions else progress


def _decision_cell(request: sql.GateRequest, uid: str) -> htm.Element:
    if uid in request.decisions:
        return htm.td["approved" if request.decisions[uid] else "rejected"]
    return htm.td[
        htm.form(method="post", action=util.as_url(post.gates.decide))[
            form.csrf_input(),
            htpy.input(type="hidden", name="request_id", value=str(request.id)),
            htm.button(".btn.btn-sm.btn-success.me-1", type="submit", name="decision", value="approve")["Approve"],
            htm.button(".btn.btn-sm.btn-danger", type="submit", name="decision", value="reject")["Reject"],
        ]
    ]


def _table(request: sql.GateRequest, gate: gates.Gate, latest_sha: str | None, uid: str) -> htm.Element:
    github = f"https://github.com/{gate.repository}"
    if latest_sha is None:
        changes = htm.a(href=f"{github}/tree/{request.head_sha}")["Full tree"]
    else:
        changes = htm.a(href=f"{github}/compare/{latest_sha}..{request.head_sha}")["Since latest approved"]
    starter = f"{request.actor_asf_uid or 'no linked ASF account'} ({request.actor} on GitHub)"
    decision_row = htm.tr[htm.th["Outcome"], htm.td[request.status.value]]
    if request.status == sql.GateStatus.PENDING:
        decision_row = htm.tr[htm.th["Your decision"], _decision_cell(request, uid)]
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
