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

import json
from typing import Literal

import aiofiles
import asfquart
import asfquart.base as base
import quart
import werkzeug.wrappers.response as response

import atr.blueprints.get as get
import atr.config as config
import atr.db as db
import atr.form as form
import atr.get.root as root
import atr.get.vote as vote
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.paths as paths
import atr.sessions as sessions
import atr.shared as shared
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def test_empty(_session: web.Public, _test_empty: Literal["test/empty"]) -> str:
    """
    URL: /test/empty
    """
    if config.is_production_mode():
        return quart.abort(404)

    empty_form = form.render(
        model_cls=form.Empty,
        submit_label="Submit empty form",
        action="/test/empty",
    )

    forms_html = htm.div[
        htm.h2["Empty form"],
        htm.p["This form only validates the CSRF token and contains no other fields."],
        empty_form,
    ]

    return await template.blank(title="Test empty form", content=forms_html)


@get.typed
async def test_login(_session: web.Public, _test_login: Literal["test/login"]) -> web.WerkzeugResponse:
    """
    URL: /test/login
    """
    # Some test routes work anywhere outside of production
    # but test logins should be Test mode only
    if not config.is_test_mode():
        return quart.abort(404)

    await util.write_session(
        sql.UserSession(
            uid="test",
            fullname="Test User",
            committees=["test"],
            projects=["test"],
        )
    )
    return await web.redirect(root.index)


@get.typed
async def test_login_banned(
    _session: web.Public, _test_login_banned: Literal["test/login-banned"]
) -> web.WerkzeugResponse:
    """
    URL: /test/login-banned
    """
    if not config.is_test_mode():
        return quart.abort(404)

    await util.write_session(
        sql.UserSession(
            uid="test-banned",
            fullname="Banned Test User",
            committees=[],
            projects=[],
        )
    )
    return await web.redirect(root.index)


@get.typed
async def test_merge(
    session: web.Committer,
    _test_merge: Literal["test/merge"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> web.WerkzeugResponse:
    """
    URL: /test/merge/<project_key>/<version_key>
    """
    if config.is_production_mode():
        return quart.abort(404)

    async with storage.write(session) as write_n:
        wacp_n = await write_n.as_project_committee_participant(project_key)

        async def modify_new(path_new: safe.StatePath, _old_rev_new: sql.Revision | None) -> None:
            async with aiofiles.open(path_new / "from_new.txt", "w") as f:
                await f.write("new content")

            async with storage.write(session) as write_p:
                wacp_p = await write_p.as_project_committee_participant(project_key)

                async def modify_prior(path_prior: safe.StatePath, _old_rev_prior: sql.Revision | None) -> None:
                    async with aiofiles.open(path_prior / "from_prior.txt", "w") as f:
                        await f.write("prior content")

                await wacp_p.revision.create_revision_with_quarantine(
                    project_key,
                    version_key,
                    session.uid,
                    allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                    description="Test merge: prior revision",
                    modify=modify_prior,
                )

        await wacp_n.revision.create_revision_with_quarantine(
            project_key,
            version_key,
            session.uid,
            allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
            description="Test merge: new revision",
            modify=modify_new,
        )

    files: list[str] = []
    async with db.session() as data:
        release_key = sql.release_key(project_key, version_key)
        release = await data.release(key=str(release_key), _project=True).demand(
            RuntimeError("Release not found after merge test")
        )
        release_dir = paths.release_directory(release)
    async for path in util.paths_recursive(release_dir):
        files.append(str(path))

    result = json.dumps({"files": sorted(files)})
    # audit_guidance The application/json media type is not defined to have a charset parameter
    return response.Response(result, status=200, mimetype="application/json")


@get.typed
async def test_multiple(_session: web.Public, _test_multiple: Literal["test/multiple"]) -> str:
    """
    URL: /test/multiple
    """
    if config.is_production_mode():
        return quart.abort(404)

    apple_form = form.render(
        model_cls=shared.test.AppleForm,
        submit_label="Order apples",
        action="/test/multiple",
    )

    banana_form = form.render(
        model_cls=shared.test.BananaForm,
        submit_label="Order bananas",
        action="/test/multiple",
    )

    forms_html = htm.div[
        htm.h2["Apple order form"],
        apple_form,
        htm.h2["Banana order form"],
        banana_form,
    ]

    return await template.blank(title="Test multiple forms", content=forms_html)


@get.typed
async def test_recheck_session(
    _session: web.Public, _test_recheck_session: Literal["test/recheck-session"]
) -> web.WerkzeugResponse:
    """
    URL: /test/recheck-session

    Reset the last_account_check to epoch so the next request triggers a re-check.
    """
    if not config.is_test_mode():
        return quart.abort(404)

    existing = await sessions.read()
    if not isinstance(existing, sql.UserSession):
        raise base.ASFQuartException("No session to recheck", errorcode=400)

    existing.last_account_check = 0
    await asfquart.APP.sessions.save(existing, {"last_account_check"})
    return await web.redirect(root.index)


@get.typed
async def test_single(session: web.Public, _test_single: Literal["test/single"]) -> str:
    """
    URL: /test/single
    """
    if config.is_production_mode():
        return quart.abort(404)

    import htpy

    vote_widget = htpy.div(class_="btn-group", role="group")[
        htpy.input(type="radio", class_="btn-check", name="vote", id="vote_0", value="+1", autocomplete="off"),
        htpy.label(class_="btn btn-outline-success", for_="vote_0")["+1"],
        htpy.input(type="radio", class_="btn-check", name="vote", id="vote_1", value="0", autocomplete="off"),
        htpy.label(class_="btn btn-outline-secondary", for_="vote_1")["0"],
        htpy.input(type="radio", class_="btn-check", name="vote", id="vote_2", value="-1", autocomplete="off"),
        htpy.label(class_="btn btn-outline-danger", for_="vote_2")["-1"],
    ]

    single_form = form.render(
        model_cls=shared.test.SingleForm,
        submit_label="Submit",
        action="/test/single",
        custom={"vote": vote_widget},
    )

    forms_html = htm.div[
        htm.h2["Single form"],
        single_form,
    ]

    return await template.blank(title="Test single form", content=forms_html)


@get.typed
async def test_vote(
    session: web.Public,
    _test_vote: Literal["test/vote"],
    category: unsafe.UnsafeStr,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /test/vote/<category>/<project_key>/<version_key>
    """
    if config.is_production_mode():
        return quart.abort(404)

    category_map = {
        "unauthenticated": vote.UserCategory.UNAUTHENTICATED,
        "committer": vote.UserCategory.COMMITTER,
        "committer_rm": vote.UserCategory.COMMITTER_RM,
        "pmc_member": vote.UserCategory.PMC_MEMBER,
        "pmc_member_rm": vote.UserCategory.PMC_MEMBER_RM,
    }

    user_category = category_map.get(str(category).lower())
    if user_category is None:
        raise base.ASFQuartException(
            f"Invalid category: {category!s}. Valid options: {', '.join(category_map.keys())}",
            errorcode=400,
        )

    if (user_category != vote.UserCategory.UNAUTHENTICATED) and (session is None):
        raise base.ASFQuartException("You must be logged in to preview authenticated views", errorcode=401)

    _, release, latest_vote_task = await vote.category_and_release(session, project_key, version_key)

    if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE:
        raise base.ASFQuartException("Release is not a candidate", errorcode=404)

    return await vote.render_options_page(session, release, user_category, latest_vote_task)
