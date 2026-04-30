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

import urllib.parse
from typing import Literal

import atr.form as form
import atr.models.sql as sql


def message_id_source_archive_url(message_id: str, vote_recipient: str) -> str:
    list_id = vote_recipient.replace("@", ".")
    query = urllib.parse.urlencode(
        {"id": f"<{message_id}>", "listid": f"<{list_id}>"},
        quote_via=urllib.parse.quote,
        safe="@",
    )
    return f"https://lists.apache.org/api/source.lua?{query}"


class CastVoteForm(form.Form):
    decision: Literal["+1", "0", "-1"] = form.label("Your vote", widget=form.Widget.CUSTOM)
    comment: str = form.label("Comment (optional)", widget=form.Widget.TEXTAREA, max_length=50_000)
    vote_seq: form.OptionalInt = form.label("Vote serial", default=None, widget=form.Widget.HIDDEN)
    vote_mode: sql.VoteMode = form.label("Vote mode", widget=form.Widget.HIDDEN)
