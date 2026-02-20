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

import e2e.admin.helpers as admin_helpers
import e2e.helpers as helpers
from playwright.sync_api import Page, expect


def test_revoke_tokens_page_loads(page_revoke_tokens: Page) -> None:
    expect(page_revoke_tokens).to_have_title("Revoke user tokens ~ ATR")


def test_revoke_tokens_page_has_heading(page_revoke_tokens: Page) -> None:
    heading = page_revoke_tokens.get_by_role("heading", name="Revoke user tokens")
    expect(heading).to_be_visible()


def test_revoke_tokens_page_has_uid_input(page_revoke_tokens: Page) -> None:
    uid_input = page_revoke_tokens.locator('input[name="asf_uid"]')
    expect(uid_input).to_be_visible()


def test_revoke_tokens_page_has_confirmation_input(page_revoke_tokens: Page) -> None:
    confirm_input = page_revoke_tokens.locator('input[name="confirm_revoke"]')
    expect(confirm_input).to_be_visible()


def test_revoke_tokens_page_has_submit_button(page_revoke_tokens: Page) -> None:
    button = page_revoke_tokens.get_by_role("button", name="Revoke all tokens")
    expect(button).to_be_visible()


def test_revoke_tokens_page_shows_token_counts_table(
    page_revoke_tokens_with_token: Page,
) -> None:
    page = page_revoke_tokens_with_token
    table = page.locator("table")
    expect(table).to_be_visible()
    # The test user should appear in the table
    test_user_row = page.locator('tr:has(td code:text-is("test"))')
    expect(test_user_row).to_be_visible()


def test_revoke_tokens_page_shows_no_tokens_message_when_empty(
    page_revoke_tokens: Page,
) -> None:
    # This test assumes no PATs exist for any user
    # If the table is not visible, the info alert should be
    page = page_revoke_tokens
    table = page.locator("table.table-striped")
    info_alert = page.locator('.alert-info:has-text("No users currently have active tokens")')
    # One of these should be visible
    expect(table.or_(info_alert)).to_be_visible()


def test_revoke_shows_error_for_wrong_confirmation(page_revoke_tokens: Page) -> None:
    page = page_revoke_tokens
    page.locator('input[name="asf_uid"]').fill("test")
    page.locator('input[name="confirm_revoke"]').fill("WRONG")
    page.get_by_role("button", name="Revoke all tokens").click()
    page.wait_for_load_state()

    error_message = page.locator(".flash-message.flash-error")
    expect(error_message).to_be_visible()


def test_revoke_nonexistent_user_shows_info(page_revoke_tokens: Page) -> None:
    page = page_revoke_tokens
    page.locator('input[name="asf_uid"]').fill("nonexistent_user_abc123")
    page.locator('input[name="confirm_revoke"]').fill("REVOKE")
    page.get_by_role("button", name="Revoke all tokens").click()
    page.wait_for_load_state()

    info_message = page.locator('.flash-message:has-text("No tokens found")')
    expect(info_message).to_be_visible()


def test_revoke_deletes_tokens_and_shows_success(
    page_revoke_tokens_with_token: Page,
) -> None:
    page = page_revoke_tokens_with_token

    # Verify the test user has tokens before revocation
    token_count = admin_helpers.get_token_count_for_user(page, "test")
    assert token_count > 0

    # Revoke all tokens for the test user
    page.locator('input[name="asf_uid"]').fill("test")
    page.locator('input[name="confirm_revoke"]').fill("REVOKE")
    page.get_by_role("button", name="Revoke all tokens").click()
    page.wait_for_load_state()

    # Should see success message
    success_message = page.locator('.flash-message.flash-success:has-text("Revoked")')
    expect(success_message).to_be_visible()

    # The test user should no longer appear in the table (or have 0 tokens)
    test_user_row = page.locator('tr:has(td code:text-is("test"))')
    expect(test_user_row).to_have_count(0)


def test_revoke_tokens_removes_tokens_from_user_view(
    page_revoke_tokens_with_token: Page,
) -> None:
    page = page_revoke_tokens_with_token

    # Revoke
    page.locator('input[name="asf_uid"]').fill("test")
    page.locator('input[name="confirm_revoke"]').fill("REVOKE")
    page.get_by_role("button", name="Revoke all tokens").click()
    page.wait_for_load_state()

    # Navigate to the user tokens page and verify tokens are gone
    helpers.visit(page, admin_helpers.TOKENS_PATH)
    token_row = page.locator(f'tr:has(td:text-is("{admin_helpers.TOKEN_LABEL_FOR_TESTING}"))')
    expect(token_row).to_have_count(0)


def test_revoke_tokens_nav_link_exists(page_revoke_tokens: Page) -> None:
    """The admin dropdown should contain a 'Revoke user tokens' link."""
    page = page_revoke_tokens
    nav_link = page.locator('a.dropdown-item:has-text("Revoke user tokens")')
    expect(nav_link).to_have_count(1)
