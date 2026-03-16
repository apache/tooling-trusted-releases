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

from typing import TYPE_CHECKING

import e2e.helpers as helpers
import e2e.merge.helpers as merge_helpers

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext


def test_merge_interleaved_revisions(merge_context: BrowserContext) -> None:
    result = helpers.api_get(
        merge_context.request,
        f"/test/merge/{merge_helpers.PROJECT_KEY}/{merge_helpers.VERSION_KEY}",
    )
    files = result["files"]
    assert "from_new.txt" in files
    assert "from_prior.txt" in files
