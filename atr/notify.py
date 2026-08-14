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

import atr.constants as constants
import atr.log as log
import atr.models.sql as sql
import atr.storage as storage


async def user(
    asf_uid: str,
    message: str,
    level: sql.NotificationLevel,
    link: str | None = None,
    link_text: str | None = None,
) -> None:
    # Record a notification the user sees as a flash next time they load the UI. The system
    # service has no inbox, so skip it, and a failed notification never fails the caller.
    if asf_uid == constants.SYSTEM_SERVICE_UID:
        return
    try:
        async with storage.write_as_user_service(asf_uid) as waus:
            await waus.notifications_create(message, level, link=link, link_text=link_text)
    except Exception:
        log.exception("Failed to record notification")
