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

import e2e.helpers as root_helpers
import e2e.policy.helpers as helpers
from playwright.sync_api import Page, expect


def test_finish_rejects_announce_recipient_off_apache_org(page: Page) -> None:
    root_helpers.log_in(page)
    root_helpers.visit(page, helpers.PROJECT_FINISH_URL)
    _save_with_recipient(page, "announce@example.com")

    error = page.locator(".flash-message.flash-error")
    expect(error).to_be_visible()
    expect(error).to_contain_text("Announce recipient 'announce@example.com' must be an apache.org address.")


def test_vote_offers_dev_list(page: Page) -> None:
    root_helpers.log_in(page)
    root_helpers.visit(page, helpers.PROJECT_VOTE_URL)

    expect(page.locator(f'input[name="email_to"][value="dev@{helpers.PROJECT_KEY}.apache.org"]')).to_be_visible()


def test_vote_rejects_recipient_off_committee_domain(page: Page) -> None:
    root_helpers.log_in(page)
    root_helpers.visit(page, helpers.PROJECT_VOTE_URL)
    _save_with_recipient(page, "dev@example.apache.org")

    error = page.locator(".flash-message.flash-error")
    expect(error).to_be_visible()
    expect(error).to_contain_text(
        f"Vote recipient 'dev@example.apache.org' must be on '{helpers.PROJECT_KEY}.apache.org'."
    )


def _save_with_recipient(page: Page, address: str) -> None:
    # The form only offers valid lists, so the bad address is put in by hand
    radio = page.locator('input[name="email_to"]').first
    radio.evaluate("(el, address) => { el.value = address; el.checked = true; }", address)
    form = page.locator("form", has=radio)
    form.locator('button[type="submit"]', has_text="Save").click()
    page.wait_for_load_state()
