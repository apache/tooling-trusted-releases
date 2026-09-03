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

from playwright.sync_api import Page, expect


def test_revoke_ssh_keys_nav_link_exists(page_revoke_ssh_keys: Page) -> None:
    nav_link = page_revoke_ssh_keys.locator('a.nav-link:has-text("Revoke SSH keys")')
    expect(nav_link).to_have_count(1)


def test_revoke_ssh_keys_nonexistent_user_shows_info(page_revoke_ssh_keys: Page) -> None:
    page = page_revoke_ssh_keys
    page.locator('input[name="asf_uid"]').fill("nonexistent_user_abc123")
    page.locator('input[name="confirm_revoke"]').fill("REVOKE")
    page.get_by_role("button", name="Revoke all SSH keys").click()
    page.wait_for_load_state()

    info_message = page.locator('.flash-message:has-text("No SSH keys found")')
    expect(info_message).to_be_visible()


def test_revoke_ssh_keys_page_has_confirmation_input(page_revoke_ssh_keys: Page) -> None:
    confirm_input = page_revoke_ssh_keys.locator('input[name="confirm_revoke"]')
    expect(confirm_input).to_be_visible()


def test_revoke_ssh_keys_page_has_heading(page_revoke_ssh_keys: Page) -> None:
    heading = page_revoke_ssh_keys.get_by_role("heading", name="Revoke user SSH keys")
    expect(heading).to_be_visible()


def test_revoke_ssh_keys_page_has_submit_button(page_revoke_ssh_keys: Page) -> None:
    button = page_revoke_ssh_keys.get_by_role("button", name="Revoke all SSH keys")
    expect(button).to_be_visible()


def test_revoke_ssh_keys_page_has_uid_input(page_revoke_ssh_keys: Page) -> None:
    uid_input = page_revoke_ssh_keys.locator('input[name="asf_uid"]')
    expect(uid_input).to_be_visible()


def test_revoke_ssh_keys_page_loads(page_revoke_ssh_keys: Page) -> None:
    expect(page_revoke_ssh_keys).to_have_title("Users ~ ATR")


def test_revoke_ssh_keys_shows_error_for_wrong_confirmation(page_revoke_ssh_keys: Page) -> None:
    page = page_revoke_ssh_keys
    page.locator('input[name="asf_uid"]').fill("test")
    page.locator('input[name="confirm_revoke"]').fill("WRONG")
    page.get_by_role("button", name="Revoke all SSH keys").click()
    page.wait_for_load_state()

    error_message = page.locator(".flash-message.flash-error")
    expect(error_message).to_be_visible()
