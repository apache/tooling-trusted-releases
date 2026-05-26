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

import datetime
from typing import Literal

import quart

import atr.blueprints.get as get
import atr.config as config
import atr.form as form
import atr.htm as htm
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def cache_get(session: web.Committer, _user_cache: Literal["user/cache"]) -> str:
    """
    URL: /user/cache
    """
    if config.is_production_mode():
        return quart.abort(404)

    cache_data = await util.session_cache_read()
    user_cached = session.uid in cache_data

    block = htm.Block()

    block.h1["Session cache management"]

    block.p[
        """This page allows you to cache your ASFQuart session information for use in
        contexts where web authentication is not available, such as SSH and rsync, the
        API, and background tasks. This is intended for developers only."""
    ]

    if user_cached:
        cached_entry = cache_data[session.uid]
        block.h2["Your cached session"]
        block.p["Your session is currently cached."]

        tbody = htm.Block(htm.tbody)
        tbody.append(htm.tr[htm.th["User ID"], htm.td[session.uid]])
        if "fullname" in cached_entry:
            tbody.append(htm.tr[htm.th["Full name"], htm.td[cached_entry["fullname"]]])
        if "email" in cached_entry:
            tbody.append(htm.tr[htm.th["Email"], htm.td[cached_entry["email"]]])
        if "pmcs" in cached_entry:
            committees = ", ".join(cached_entry["pmcs"]) if cached_entry["pmcs"] else "-"
            tbody.append(htm.tr[htm.th["Committees"], htm.td[committees]])
        if "projects" in cached_entry:
            projects = ", ".join(cached_entry["projects"]) if cached_entry["projects"] else "-"
            tbody.append(htm.tr[htm.th["Projects"], htm.td[projects]])

        block.table(".table.table-striped.table-bordered")[tbody.collect()]

        block.h3["Delete cache"]
        block.p["Remove your cached session information:"]
        delete_cache_form = await form.render(
            model_cls=shared.user.DeleteCacheForm,
            submit_label="Delete my cache",
            submit_classes="btn-danger",
        )
        block.append(delete_cache_form)
    else:
        block.h2["No cached session"]
        block.p["Your session is not currently cached."]

        block.h3["Cache current session"]
        block.p["Press the button below to cache your current session information:"]
        cache_form = await form.render(
            model_cls=shared.user.CacheUserForm,
            submit_label="Cache me!",
            submit_classes="btn-primary",
        )
        block.append(cache_form)

    return await template.blank("Session cache management", content=block.collect())


@get.typed
async def preferences(session: web.Committer, _user_preferences: Literal["user/preferences"]) -> str:
    """
    URL: /user/preferences
    """

    existing_prefs = None
    async with storage.read_as_foundation_committer() as rafc:
        user = await rafc.user.user_preferences()
        if user:
            existing_prefs = user.preferences

    block = htm.Block()
    block.h1["User preferences"]
    block.p["Select your preferences below."]
    prefs_form = await form.render(
        model_cls=shared.user.UserPreferencesForm,
        submit_label="Save",
        defaults={
            "colour_blindness_mode": existing_prefs.colour_blindness_mode.value if existing_prefs else None,
            "nav_pinned": existing_prefs.nav_pinned if existing_prefs else True,
        },
    )
    block.append(prefs_form)
    return await template.blank("User preferences", content=block.collect())


@get.typed
async def sessions(_session: web.Committer, _user_sessions: Literal["user/sessions"]) -> str:
    """
    URL: /user/sessions
    """

    sessions: list[sql.UserSession] = []
    async with storage.read_as_foundation_committer() as rafc:
        sessions = sorted(await rafc.user.user_sessions(), key=lambda s: s.last_account_check or s.cts, reverse=True)

    block = htm.Block()
    block.h1["User sessions"]
    block.p["Below is a list of your recent sessions"]

    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header.d-flex.justify-content-between.align-items-center")[htm.h3(".mb-0")["Sessions"]]

    tbody = htm.Block(htm.tbody)
    for session in sessions:
        tbody.tr[
            htm.td[session.uid],
            htm.td[datetime.datetime.fromtimestamp(session.cts).strftime("%Y-%m-%d %H:%M:%S")],
            htm.td[datetime.datetime.fromtimestamp(session.last_account_check).strftime("%Y-%m-%d %H:%M:%S")]
            if session.last_account_check
            else "",
            htm.td[session.ip_address or "unknown"],
        ]

    card.div(".card-body")[
        htm.div(".table-responsive")[
            htm.table(".table.table-striped")[
                htm.thead[
                    htm.tr[
                        htm.th["User ID"],
                        htm.th["Created"],
                        htm.th["Last verified"],
                        htm.th["IP Address"],
                    ]
                ],
                tbody.collect(),
            ]
        ]
    ]
    block.append(card)
    return await template.blank("User sessions", content=block.collect())


@get.typed
async def tally(_session: web.Committer, _user_tally: Literal["user/tally"]) -> str:
    """
    URL: /user/tally
    """
    block = htm.Block()
    block.h1["Vote tally"]
    block.p["Enter a lists.apache.org thread URL or thread ID to count the votes in the thread."]
    tally_form = await form.render(
        model_cls=shared.user.TallyForm,
        submit_label="Count votes",
    )
    block.append(tally_form)
    return await template.blank("Vote tally", content=block.collect())
