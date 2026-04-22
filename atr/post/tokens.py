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
import hashlib
import secrets
from typing import Final, Literal

import quart_rate_limiter as rate_limiter

import atr.blueprints.post as post
import atr.get as get
import atr.htm as htm
import atr.sessions as sessions
import atr.shared as shared
import atr.storage as storage
import atr.web as web

_EXPIRY_DAYS: Final[int] = 180


@post.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
async def jwt_post(
    session: web.Committer, _tokens_jwt: Literal["tokens/jwt"], form: shared.tokens.IssueForm
) -> web.QuartResponse:
    """
    URL: /tokens/jwt
    """
    async with storage.write(session) as write:
        wafc = write.as_foundation_committer()
        jwt_token = await wafc.tokens.issue_jwt(form.pat)
    response = web.TextResponse(jwt_token)
    response.headers["Cache-Control"] = "no-store"
    return response


@post.typed
@rate_limiter.rate_limit(10, datetime.timedelta(hours=1))
async def tokens(
    session: web.Committer, _tokens: Literal["tokens"], token_form: shared.tokens.TokenForm
) -> web.WerkzeugResponse:
    """
    URL: /tokens
    """
    match token_form:
        case shared.tokens.AddTokenForm() as add_form:
            return await _add_token(session, add_form)

        case shared.tokens.DeleteTokenForm() as delete_form:
            return await _delete_token(session, delete_form)


async def _add_token(session: web.Committer, add_form: shared.tokens.AddTokenForm) -> web.WerkzeugResponse:
    plaintext = secrets.token_urlsafe(32)
    token_hash = hashlib.sha3_256(plaintext.encode()).hexdigest()
    created = datetime.datetime.now(datetime.UTC)
    expires = created + datetime.timedelta(days=_EXPIRY_DAYS)

    async with storage.write() as write:
        wafc = write.as_foundation_committer()
        await wafc.tokens.add_token(
            token_hash,
            created,
            expires,
            add_form.label,
        )

    await web.flash_success(
        htm.p[
            htm.strong["Your new token"],
            " is ",
            htm.code(".bg-light.border.rounded.px-1")[plaintext],
        ],
        htm.p(".mb-0")["Copy it now as you will not be able to see it again."],
    )
    return await session.redirect(get.tokens.tokens)


async def _delete_token(session: web.Committer, delete_form: shared.tokens.DeleteTokenForm) -> web.WerkzeugResponse:
    # audit_guidance token ownership verified in storage layer via authenticated user's asfuid
    async with storage.write(session) as write:
        wafc = write.as_foundation_committer()
        await wafc.tokens.delete_token(delete_form.token_id)
    # audit_guidance revoking a PAT terminates all of this user's OAuth sessions, in case the token was created by
    # an attacker-controlled session
    await sessions.terminate_current_users_sessions(session.uid)
    return await session.redirect(get.tokens.tokens)
