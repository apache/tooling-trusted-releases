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
    form_data = await quart.request.form
    decision = form_data.get("decision")
    request_id = form_data.get("request_id", "")
    if (decision not in ("approve", "reject")) or (not request_id.isdecimal()):
        quart.abort(400, description="Invalid gate decision")

    async with storage.write(session) as write:
        status = await write.as_foundation_committer().gates.decide(int(request_id), decision == "approve")
    await quart.flash(f"Your decision is recorded. The request is now {status.value}.", "success")
    return await session.redirect(get.gates.index)
