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

import quart

import atr.blueprints.post as post
import atr.get as get
import atr.storage as storage
import atr.web as web


@post.typed
async def decide(session: web.Committer, _gates_decide: Literal["gates/decide"]) -> web.WerkzeugResponse:
    request_id, approve = await _decision()
    async with storage.write(session) as write:
        status = await write.as_foundation_committer().gates.decide(request_id, approve)
    await quart.flash(f"Your decision is recorded. The request is now {status.value}.", "success")
    return await session.redirect(get.gates.index)


@post.typed
async def decide_config(
    session: web.Committer, _gates_config_decide: Literal["gates/config/decide"]
) -> web.WerkzeugResponse:
    config_id, approve = await _decision()
    async with storage.write(session) as write:
        status = await write.as_foundation_committer().gates.decide_config(config_id, approve)
    await quart.flash(f"Your decision is recorded. The proposal is now {status.value}.", "success")
    return await session.redirect(get.gates.config)


@post.typed
async def propose(session: web.Committer, _gates_propose: Literal["gates/propose"]) -> web.WerkzeugResponse:
    form_data = await quart.request.form
    fields = ("repository", "committee_key", "environment", "workflow", "event", "ref_type", "ref_pattern")
    values = [form_data.get(name, "").strip() for name in fields]
    if not all(values):
        quart.abort(400, description="Every field of a gate is required")
    async with storage.write(session) as write:
        await write.as_foundation_committer().gates.propose(*values)
    await quart.flash("Your proposal is recorded, with your approval.", "success")
    return await session.redirect(get.gates.config)


async def _decision() -> tuple[int, bool]:
    form_data = await quart.request.form
    decision = form_data.get("decision")
    row_id = form_data.get("id", "")
    if (decision not in ("approve", "reject")) or (not row_id.isdecimal()):
        quart.abort(400, description="Invalid gate decision")
    return int(row_id), decision == "approve"
