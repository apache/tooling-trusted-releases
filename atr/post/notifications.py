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

from __future__ import annotations

from typing import Literal

import quart

import atr.blueprints.post as post
import atr.storage as storage
import atr.web as web


@post.typed
async def dismiss(
    session: web.Committer,
    _notifications_dismiss: Literal["notifications/dismiss"],
) -> web.QuartResponse:
    form_data = await quart.request.form
    raw_id = form_data.get("notification_id", "")
    try:
        notification_id = int(raw_id)
    except ValueError:
        return quart.Response(status=400)
    if notification_id <= 0:
        return quart.Response(status=204)

    async with storage.write(session) as write:
        wafc = write.as_foundation_committer()
        await wafc.notifications.dismiss(notification_id)
    return quart.Response(status=204)
