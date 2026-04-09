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
import atr.form as form
import atr.get as get
import atr.htm as htm
import atr.models as models
import atr.shared as shared
import atr.storage as storage
import atr.tabulate as tabulate
import atr.template as template
import atr.util as util
import atr.web as web


@post.typed
async def save_preferences(
    session: web.Committer,
    _user_preferences: Literal["user/preferences"],
    preferences_form: shared.user.UserPreferencesForm,
) -> web.WerkzeugResponse:
    """
    URL: /user/preferences
    """
    async with storage.write() as write:
        prefs = models.sql.UserPreferencesEntry(
            colour_blindness_mode=models.sql.ColourBlindnessMode(preferences_form.colour_blindness_mode.value)
        )
        await write.as_foundation_committer().user.set_user_preferences(prefs)
    await quart.flash("Preferences saved", "success")
    return await session.redirect(get.user.preferences)


@post.typed
async def session_post(
    session: web.Committer, _user_cache: Literal["user/cache"], user_cache_form: shared.user.UserCacheForm
) -> web.WerkzeugResponse:
    """
    URL: /user/cache
    """
    match user_cache_form:
        case shared.user.CacheUserForm():
            await _cache_session(session)
            await quart.flash("Your session has been cached successfully", "success")

        case shared.user.DeleteCacheForm():
            await _delete_session_cache(session)
            await quart.flash("Your cached session has been deleted", "success")

    return await session.redirect(get.user.cache_get)


@post.typed
async def tally(
    session: web.Committer,
    _user_tally: Literal["user/tally"],
    tally_form: shared.user.TallyForm,
) -> str:
    """
    URL: /user/tally
    """
    thread_id = _extract_thread_id(tally_form.thread)

    block = htm.Block()
    block.h1["Vote tally"]

    tally_resubmit = form.render(
        model_cls=shared.user.TallyForm,
        submit_label="Count votes",
        defaults={"thread": tally_form.thread},
    )
    block.append(tally_resubmit)

    if not thread_id:
        block.div(".alert.alert-danger")["Please enter a thread URL or ID."]
        return await template.blank("Vote tally", content=block.collect())

    try:
        _start_unixtime, tabulated_votes = await tabulate.votes(None, thread_id)
    except (util.FetchError, ValueError) as e:
        block.div(".alert.alert-danger")[str(e)]
        return await template.blank("Vote tally", content=block.collect())

    if not tabulated_votes:
        block.p["No votes found in this thread."]
        return await template.blank("Vote tally", content=block.collect())

    summary = tabulate.vote_summary(tabulated_votes)

    block.h2["Votes"]
    _render_votes_table(block, tabulated_votes)

    block.h2["Summary"]
    _render_summary_table(block, summary)

    return await template.blank("Vote tally", content=block.collect())


async def _cache_session(session: web.Committer) -> None:
    cache_data = await util.session_cache_read()

    session_data = {
        "uid": session.uid,
        "dn": session.dn,
        "fullname": session.fullname,
        "email": session.email,
        "isMember": session.is_member,
        "isChair": session.is_chair,
        # "isRoot": session.is_root,
        "pmcs": session.committees,
        "projects": session.projects,
        "mfa": session.mfa,
        "roleaccount": session.is_role,
    }

    cache_data[session.uid] = session_data

    await util.session_cache_write(cache_data)


async def _delete_session_cache(session: web.Committer) -> None:
    cache_data = await util.session_cache_read()

    if session.uid in cache_data:
        del cache_data[session.uid]
        await util.session_cache_write(cache_data)


def _extract_thread_id(value: str) -> str:
    value = value.strip().rstrip("/")
    marker = "lists.apache.org/thread/"
    index = value.find(marker)
    if index >= 0:
        return value[index + len(marker) :]
    return value


def _render_summary_table(block: htm.Block, summary: dict[str, int]) -> None:
    thead = htm.thead[htm.tr[htm.th["Category"], htm.th["Yes"], htm.th["No"], htm.th["Abstain"], htm.th["Total"]]]
    tbody = htm.Block(htm.tbody)
    for label, prefix in [("Binding", "binding"), ("Non-binding", "non_binding"), ("Unknown", "unknown")]:
        total = summary[f"{prefix}_votes"]
        if total == 0:
            continue
        tbody.append(
            htm.tr[
                htm.td[label],
                htm.td[str(summary[f"{prefix}_votes_yes"])],
                htm.td[str(summary[f"{prefix}_votes_no"])],
                htm.td[str(summary[f"{prefix}_votes_abstain"])],
                htm.td[str(total)],
            ]
        )
    block.table(".table.table-striped")[thead, tbody.collect()]


def _render_votes_table(block: htm.Block, tabulated_votes: dict[str, models.tabulate.VoteEmail]) -> None:
    thead = htm.thead[
        htm.tr[
            htm.th["Name"],
            htm.th["UID or email"],
            htm.th(".text-center")["Vote"],
            htm.th(".text-center")["Status"],
            htm.th["Quotation"],
        ]
    ]
    tbody = htm.Block(htm.tbody)
    for vote_email in tabulated_votes.values():
        vote_class = ""
        if vote_email.vote == models.tabulate.Vote.YES:
            vote_class = ".atr-green"
        elif vote_email.vote == models.tabulate.Vote.NO:
            vote_class = ".atr-red"
        tbody.append(
            htm.tr[
                htm.td(".atr-nowrap")[vote_email.name],
                htm.td(".atr-nowrap")[vote_email.asf_uid_or_email],
                htm.td(f".atr-nowrap.text-center{vote_class}")[vote_email.vote.value],
                htm.td(".atr-nowrap.text-center")[vote_email.status.value],
                htm.td[vote_email.quotation],
            ]
        )
    block.table(".table.table-striped")[thead, tbody.collect()]
