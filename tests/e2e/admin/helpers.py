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

from playwright.sync_api import Page

REVOKE_TOKENS_PATH: Final[str] = "/admin/revoke-user-tokens"
TOKENS_PATH: Final[str] = "/tokens"
TOKEN_LABEL_FOR_TESTING: Final[str] = "e2e-revoke-test-token"


def create_token(page: Page, label: str) -> None:
    """Create a PAT via the tokens page."""
    label_input = page.locator('input[name="label"]')
    label_input.fill(label)
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_load_state()


def get_token_count_for_user(page: Page, uid: str) -> int:
    """Read the token count for a user from the revoke tokens page table."""
    row = page.locator(f'tr:has(td code:text-is("{uid}"))')
    if row.count() == 0:
        return 0
    count_cell = row.locator("td").nth(1)
    return int(count_cell.inner_text())
