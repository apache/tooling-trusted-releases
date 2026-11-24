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

from typing import Final

import e2e.helpers as helpers
from playwright.sync_api import BrowserContext, expect

_PERMISSIONS_CARD: Final[str] = 'div.card:has(h3:text-is("Permissions"))'


def test_test_committee_always_allows_github_uploads(committees_context: BrowserContext) -> None:
    page = committees_context.new_page()
    helpers.visit(page, "/committees/test")
    card = page.locator(_PERMISSIONS_CARD)
    expect(card).to_contain_text("Uploads from GitHub Actions: Allowed")
    expect(card.locator('input[type="checkbox"]')).to_have_count(0)
    page.close()
