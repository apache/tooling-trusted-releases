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

import atr.blueprints.post as post
import atr.get as get
import atr.models.safe as safe
import atr.shared as shared
import atr.storage as storage
import atr.web as web


@post.typed
async def view(
    session: web.Committer,
    _committees: Literal["committees"],
    name: safe.CommitteeKey,
    committees_form: shared.committees.CommitteesForm,
) -> web.WerkzeugResponse:
    """
    URL: /committees/<name>
    Handle the release manager designation forms on the committee page.
    """
    if committees_form.committee_key != str(name):
        raise RuntimeError("Committee key mismatch")

    match committees_form:
        case shared.committees.AddReleaseManagerForm() as add_form:
            return await _release_manager_add(session, name, add_form)

        case shared.committees.RemoveReleaseManagerForm() as remove_form:
            return await _release_manager_remove(session, name, remove_form)


async def _release_manager_add(
    session: web.Committer, name: safe.CommitteeKey, add_form: shared.committees.AddReleaseManagerForm
) -> web.WerkzeugResponse:
    try:
        async with storage.write_as_committee_member(str(name), session) as wacm:
            added = await wacm.committee.release_manager_add(add_form.asf_uid)
    except storage.AccessError as e:
        return await session.redirect(get.committees.view, error=str(e), name=str(name))
    asf_uid = add_form.asf_uid.strip().lower()
    if added:
        message = f"Designated {asf_uid} as a release manager."
    else:
        message = f"{asf_uid} is already a release manager."
    return await session.redirect(get.committees.view, success=message, name=str(name))


async def _release_manager_remove(
    session: web.Committer, name: safe.CommitteeKey, remove_form: shared.committees.RemoveReleaseManagerForm
) -> web.WerkzeugResponse:
    try:
        async with storage.write_as_committee_member(str(name), session) as wacm:
            removed = await wacm.committee.release_manager_remove(remove_form.asf_uid)
    except storage.AccessError as e:
        return await session.redirect(get.committees.view, error=str(e), name=str(name))
    asf_uid = remove_form.asf_uid.strip().lower()
    if removed:
        return await session.redirect(
            get.committees.view, success=f"Removed {asf_uid} as a release manager.", name=str(name)
        )
    return await session.redirect(get.committees.view, error=f"{asf_uid} is not a release manager.", name=str(name))
