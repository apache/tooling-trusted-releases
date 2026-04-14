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

import atr.form as form
import atr.models.sql as sql
import atr.storage as storage


class CastVoteForm(form.Form):
    decision: Literal["+1", "0", "-1"] = form.label("Your vote", widget=form.Widget.CUSTOM)
    comment: str = form.label("Comment (optional)", widget=form.Widget.TEXTAREA, max_length=50_000)


async def is_binding(
    committee: sql.Committee,
    is_pmc_member: bool,
) -> tuple[bool, str]:
    if committee.is_podling:
        async with storage.write() as write:
            try:
                _wacm = write.as_committee_member("incubator")
                is_binding = True
            except storage.AccessError:
                is_binding = False
        binding_committee = "Incubator"
    else:
        is_binding = is_pmc_member
        binding_committee = committee.display_name
    return is_binding, binding_committee
