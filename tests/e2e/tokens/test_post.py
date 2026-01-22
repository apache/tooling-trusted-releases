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

import e2e.tokens.helpers as token_helpers
from playwright.sync_api import Page, expect


def test_create_token_with_label(page_tokens_clean: Page) -> None:
    page = page_tokens_clean
    label_input = page.locator('input[name="label"]')
    label_input.fill(token_helpers.TOKEN_LABEL_FOR_TESTING)
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_load_state()

    success_message = page.locator(".flash-message.flash-success")
    expect(success_message).to_be_visible()
    expect(success_message).to_contain_text("Your new token")

    token_row = token_helpers.get_token_row_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)
    expect(token_row).to_be_visible()

    token_helpers.delete_token_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)


def test_create_token_without_label_shows_error(page_tokens_clean: Page) -> None:
    page = page_tokens_clean
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_load_state()

    error_message = page.locator(".flash-message.flash-error")
    expect(error_message).to_be_visible()
    expect(error_message).to_contain_text("Label is required")


def test_delete_token_shows_success(page_tokens_clean: Page) -> None:
    page = page_tokens_clean

    label_input = page.locator('input[name="label"]')
    label_input.fill(token_helpers.TOKEN_LABEL_FOR_TESTING)
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_load_state()

    token_row = token_helpers.get_token_row_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)
    expect(token_row).to_be_visible()

    token_row.get_by_role("button", name="Delete").click()
    page.wait_for_load_state()

    success_message = page.locator(".flash-message.flash-success")
    expect(success_message).to_be_visible()
    expect(success_message).to_contain_text("Token deleted successfully")

    token_row = token_helpers.get_token_row_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)
    expect(token_row).to_have_count(0)


def test_token_table_shows_created_date(page_tokens_clean: Page) -> None:
    page = page_tokens_clean

    label_input = page.locator('input[name="label"]')
    label_input.fill(token_helpers.TOKEN_LABEL_FOR_TESTING)
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_load_state()

    token_row = token_helpers.get_token_row_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)
    created_cell = token_row.locator("td").nth(1)
    expect(created_cell).not_to_be_empty()

    token_helpers.delete_token_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)


def test_token_table_shows_expires_date(page_tokens_clean: Page) -> None:
    page = page_tokens_clean

    label_input = page.locator('input[name="label"]')
    label_input.fill(token_helpers.TOKEN_LABEL_FOR_TESTING)
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_load_state()

    token_row = token_helpers.get_token_row_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)
    expires_cell = token_row.locator("td").nth(2)
    expect(expires_cell).not_to_be_empty()

    token_helpers.delete_token_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)
