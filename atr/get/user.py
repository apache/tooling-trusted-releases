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
import atr.db as db
import atr.form as form
import atr.htm as htm
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.template as template
import atr.user as user
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
async def sessions(current_session: web.Committer, _user_sessions: Literal["user/sessions"]) -> str:
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
        current = session.sid_hash == current_session.session.sid_hash
        terminate_form = await form.render(
            model_cls=shared.user.TerminateSessionForm,
            form_classes=".mb-0",
            submit_classes="btn-sm btn-danger text-nowrap",
            submit_label="Terminate",
            defaults={"sid_hash": session.sid_hash},
        )
        tbody.tr[
            htm.td[session.uid, htm.span(".badge.text-bg-secondary.ms-2")["Current session"] if current else ""],
            htm.td[datetime.datetime.fromtimestamp(session.cts).strftime("%Y-%m-%d %H:%M:%S")],
            htm.td[
                datetime.datetime.fromtimestamp(session.last_account_check).strftime("%Y-%m-%d %H:%M:%S")
                if session.last_account_check
                else ""
            ],
            htm.td[session.ip_address or "unknown"],
            htm.td[terminate_form],
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
                        htm.th[""],
                    ]
                ],
                tbody.collect(),
            ]
        ]
    ]
    block.append(card)
    terminate_all_form = await form.render(
        model_cls=shared.user.TerminateAllSessionsForm,
        submit_classes="btn-danger",
        submit_label="Terminate all sessions",
        confirm="This will log you out of every session, including this one. Continue?",
    )
    block.append(terminate_all_form)
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


def _render_account_overview(session: web.Committer) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header")[htm.h3(".mb-0")["Account overview"]]
    tbody = htm.Block(htm.tbody)
    tbody.tr[htm.th["User ID"], htm.td[session.uid]]
    if session.fullname:
        tbody.tr[htm.th["Full name"], htm.td[session.fullname]]
    tbody.tr[htm.th["Email"], htm.td[session.email or f"{session.uid}@apache.org"]]
    tbody.tr[
        htm.th["Global roles"],
        htm.td[
            htm.span(".badge.text-bg-primary.me-2")["Admin" if session.is_admin else "User"],
            htm.span(".badge.text-bg-info.me-2")["ASF Member"] if session.is_member else "",
            htm.span(".badge.text-bg-warning.me-2")["PMC Chair"] if session.is_chair else "",
        ],
    ]
    card.div(".card-body")[htm.div(".table-responsive")[htm.table(".table.table-striped.mb-0")[tbody.collect()]]]
    return card.collect()


def _render_allowed_card(has_any_committer: bool, has_any_rm: bool, has_any_pmc: bool, is_admin: bool) -> htm.Element:
    col = htm.Block(htm.div, classes=".col-lg-6.mb-3.mb-lg-0")
    card = htm.Block(htm.div, classes=".card.border-success.h-100")
    card.div(".card-header.bg-success-subtle.text-success-emphasis.fw-bold")[
        htm.i(".bi.bi-check-circle-fill.me-2"),
        "What you can do",
    ]
    ul = htm.Block(htm.ul, classes=".list-group.list-group-flush")

    ul.li(".list-group-item")[
        htm.strong["Committer:"],
        " View releases, committees, and projects; manage personal preferences and active sessions",
    ]
    ul.li(".list-group-item")[
        htm.strong["Security:"],
        " Create and manage Personal Access Tokens (API keys) and manage your public keys (OpenPGP & SSH)",
    ]
    ul.li(".list-group-item")[
        htm.strong["Voting:"],
        " Cast release votes (counted as non-binding unless a PMC member for the release)",
    ]

    if has_any_committer:
        ul.li(".list-group-item")[
            htm.strong["Drafting:"],
            " Start new release candidate versions and upload release artifacts, checksums, and signatures",
        ]

    if has_any_rm:
        ul.li(".list-group-item")[
            htm.strong["Release management:"],
            " Start formal release votes, resolve/tally votes, and publish/announce approved releases",
        ]
        ul.li(".list-group-item")[
            htm.strong["Project metadata:"],
            " Record release distributions and update project metadata and release policy",
        ]

    if has_any_pmc:
        ul.li(".list-group-item")[
            htm.strong["Binding votes:"],
            " Cast binding votes to release a candidate for your assigned committees",
        ]
        ul.li(".list-group-item")[
            htm.strong["Governance:"],
            " Designate and remove project release managers, and manage the committee's KEYS file",
        ]
        ul.li(".list-group-item")[
            htm.strong["Committee projects:"],
            " Create new projects, archive projects, and manage committee check ignores and categories",
        ]

    if is_admin:
        ul.li(".list-group-item")[
            htm.strong["Platform admin:"],
            " Access system management, user management, catalog, database tools, and toggle admin view",
        ]

    card.append(ul)
    col.append(card)
    return col.collect()


def _render_restricted_card(has_any_committer: bool, has_any_rm: bool, has_any_pmc: bool) -> htm.Element:
    col = htm.Block(htm.div, classes=".col-lg-6")
    card = htm.Block(htm.div, classes=".card.border-danger.h-100")
    card.div(".card-header.bg-danger-subtle.text-danger-emphasis.fw-bold")[
        htm.i(".bi.bi-x-circle-fill.me-2"),
        "What you cannot do",
    ]
    ul = htm.Block(htm.ul, classes=".list-group.list-group-flush")

    if not has_any_pmc:
        ul.li(".list-group-item")[
            htm.strong["Binding votes:"],
            " Cannot cast binding votes for release candidates (your votes will be recorded as non-binding)",
        ]
        ul.li(".list-group-item")[
            htm.strong["Governance:"],
            " Cannot designate or remove release managers, or manage the committee's KEYS file",
        ]
        ul.li(".list-group-item")[
            htm.strong["Project management:"],
            " Cannot create new projects or archive existing projects",
        ]

    if not has_any_rm:
        ul.li(".list-group-item")[
            htm.strong["Votes & publishing:"],
            " Cannot start formal release votes, resolve votes, or publish/announce releases",
        ]
        ul.li(".list-group-item")[
            htm.strong["Policies:"],
            " Cannot edit project release policies or record release distributions",
        ]

    if not has_any_committer:
        ul.li(".list-group-item")[
            htm.strong["Drafts & artifacts:"],
            " Cannot start release candidate drafts or upload release candidate artifacts",
        ]

    if not ul.elements:
        ul.li(".list-group-item.text-muted")["No role-based restrictions currently applied to your account.",]

    card.append(ul)
    col.append(card)
    return col.collect()


def _render_committee_breakdown(
    committees: list[sql.Committee],
    session: web.Committer,
) -> htm.Element:
    card = htm.Block(htm.div, classes=".card.mb-4")
    card.div(".card-header")[htm.h3(".mb-0")["Committee breakdown"]]

    relevant_committees = [
        c
        for c in committees
        if user.is_committee_member(c, session.uid)
        or user.is_release_manager(c, session.uid)
        or user.is_committer(c, session.uid)
    ]

    if relevant_committees:
        tbody = htm.Block(htm.tbody)
        for c in relevant_committees:
            is_pmc_c = user.is_committee_member(c, session.uid)
            is_rm_c = user.is_release_manager(c, session.uid)
            is_committer_c = user.is_committer(c, session.uid)

            if is_pmc_c:
                role_label = htm.span(".badge.text-bg-primary")["PMC member"]
            elif is_rm_c:
                role_label = htm.span(".badge.text-bg-success")["Designated release manager"]
            elif is_committer_c:
                role_label = htm.span(".badge.text-bg-info")["Committer"]
            else:
                role_label = htm.span(".badge.text-bg-secondary")["Non-participant"]

            can_draft = is_pmc_c or is_rm_c or is_committer_c
            can_manage = is_pmc_c or is_rm_c
            can_pmc = is_pmc_c

            badge_yes = htm.span(".badge.text-bg-success")["Yes"]
            badge_no = htm.span(".badge.text-bg-secondary")["No"]
            badge_binding = htm.span(".badge.text-bg-success")["Binding"]
            badge_non_binding = htm.span(".badge.text-bg-secondary")["Non-binding"]

            tbody.tr[
                htm.td[htm.strong[c.name or c.key], htm.span(".text-muted.ms-1")[f"({c.key})"]],
                htm.td[role_label],
                htm.td[badge_yes if can_draft else badge_no],
                htm.td[badge_yes if can_manage else badge_no],
                htm.td[badge_binding if can_pmc else badge_non_binding],
                htm.td[badge_yes if can_pmc else badge_no],
            ]

        card.div(".card-body")[
            htm.div(".table-responsive")[
                htm.table(".table.table-striped.mb-0")[
                    htm.thead[
                        htm.tr[
                            htm.th["Committee"],
                            htm.th["Your role"],
                            htm.th["Draft releases"],
                            htm.th["Start & resolve votes"],
                            htm.th["Voting status"],
                            htm.th["Manage KEYS & RMs"],
                        ]
                    ],
                    tbody.collect(),
                ]
            ]
        ]
    else:
        card.div(".card-body")[
            htm.p[
                "You are not currently a member or committer of any registered committees. "
                "Contact a PMC member of your project to be added."
            ]
        ]

    return card.collect()


@get.typed
async def permissions(session: web.Committer, _user_permissions: Literal["user/permissions"]) -> str:
    """
    URL: /user/permissions
    """
    async with db.session() as data:
        all_committees = await data.committee().all()

    sorted_committees = sorted(all_committees, key=lambda c: c.name or c.key)
    is_admin = session.is_admin

    user_pmc_keys = {c.key for c in sorted_committees if user.is_committee_member(c, session.uid)}
    user_rm_keys = {c.key for c in sorted_committees if user.is_release_manager(c, session.uid)}
    user_committer_keys = {c.key for c in sorted_committees if user.is_committer(c, session.uid)}

    has_any_pmc = bool(user_pmc_keys)
    has_any_rm = bool(user_rm_keys) or has_any_pmc
    has_any_committer = bool(user_committer_keys) or has_any_rm

    block = htm.Block()
    block.h1["User permissions"]
    block.p["Review your active roles, capabilities, and restrictions across Apache Trusted Releases."]

    if is_admin:
        block.div(".alert.alert-info.mb-4", role="alert")[
            htm.strong["You are an administrator."],
            " Administrator access is separate from the committee roles shown below.",
        ]

    block.append(_render_account_overview(session))

    row_caps = htm.Block(htm.div, classes=".row.mb-4")
    row_caps.append(_render_allowed_card(has_any_committer, has_any_rm, has_any_pmc, is_admin))
    row_caps.append(_render_restricted_card(has_any_committer, has_any_rm, has_any_pmc))
    block.append(row_caps)

    block.append(_render_committee_breakdown(sorted_committees, session))

    return await template.blank("User permissions", content=block.collect())
