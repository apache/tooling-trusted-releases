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
import atr.log as log
import atr.shared as shared
import atr.web as web


@post.typed
async def test_empty(
    _session: web.Public, _test_empty: Literal["test/empty"], _form: form.Empty
) -> web.WerkzeugResponse:
    """
    URL: /test/empty
    """
    msg = "Empty form submitted successfully"
    log.info(msg)
    await quart.flash(msg, "success")
    return await web.redirect(get.test.test_empty)


@post.typed
async def test_multiple(
    _session: web.Public, _test_multiple: Literal["test/multiple"], multiple_form: shared.test.MultipleForm
) -> web.WerkzeugResponse:
    """
    URL: /test/multiple
    """
    match multiple_form:
        case shared.test.AppleForm() as apple:
            msg = f"Apple order received: variety={apple.variety}, quantity={apple.quantity}, organic={apple.organic}"
            log.info(msg)
            await quart.flash(msg, "success")

        case shared.test.BananaForm() as banana:
            msg = f"Banana order received: ripeness={banana.ripeness}, bunch_size={banana.bunch_size}"
            log.info(msg)
            await quart.flash(msg, "success")

    return await web.redirect(get.test.test_multiple)


@post.typed
async def test_single(
    session: web.Public, _test_single: Literal["test/single"], single_form: shared.test.SingleForm
) -> web.WerkzeugResponse:
    """
    URL: /test/single
    """
    file_names = [f.filename for f in single_form.files] if single_form.files else []
    compatibility_names = [f.value for f in single_form.compatibility] if single_form.compatibility else []
    if (single_form.message == "Forbidden message!") and (session is not None):
        return await session.form_error(
            "message",
            "You are not permitted to submit the forbidden message",
        )
    msg = (
        f"Single form received:"
        f" name={single_form.name},"
        f" email={single_form.email},"
        f" message={single_form.message},"
        f" files={file_names},"
        f" compatibility={compatibility_names},"
        f" vote={single_form.vote}"
    )
    log.info(msg)
    await quart.flash(msg, "success")

    return await web.redirect(get.test.test_single)
