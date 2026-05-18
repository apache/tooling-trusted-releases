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

import e2e.announce.helpers as helpers  # type: ignore[reportMissingImports]
import e2e.helpers as root_helpers  # type: ignore[reportMissingImports]
from playwright.sync_api import BrowserContext, Page, expect


def test_start_form_hides_auto_archive_when_policy_off(announce_context: BrowserContext) -> None:
    """The per-release opt-in should only appear when the project policy allows it."""
    page = announce_context.new_page()
    try:
        helpers.ensure_policy_auto_archive(page, enabled=False)
        root_helpers.visit(page, helpers.START_URL)
        expect(page.locator("input#auto_archive_prior")).to_have_count(0)
    finally:
        # Leave the policy on so the auto_archive_context fixture finds the
        # project in a usable state regardless of test execution order.
        helpers.ensure_policy_auto_archive(page, enabled=True)
        page.close()


def test_announce_hides_auto_archive_row_for_non_opted_in_release(page_announce: Page) -> None:
    """A release that wasn't opted in at start time shouldn't show the row on announce."""
    expect(page_announce.locator("input#auto_archive")).to_have_count(0)
    expect(page_announce.locator("#auto_archive_release")).to_have_count(0)


def test_announce_archives_prior_release(
    page_auto_archive_announce: Page, auto_archive_context: BrowserContext
) -> None:
    """Announcing with auto-archive on should archive the prior release in the cycle."""
    page = page_auto_archive_announce

    # The opt-in row should be present, pre-checked, and show the prior version.
    expect(page.locator("input#auto_archive")).to_be_checked()
    expect(page.locator("#auto_archive_release")).to_contain_text(helpers.PRIOR_VERSION)

    # Submit the announce form. On success the server redirects to
    # /releases/finished/<project>.
    page.locator("input#confirm_announce").fill("CONFIRM")
    page.get_by_role("button", name="Publish & announce").click()
    page.wait_for_url(f"**{helpers.FINISHED_LIST_URL}**")

    # Verify on a fresh page that the prior release now carries the Archived
    # badge. Using the archived card class keeps us scoped to the right row
    # even if other archived releases appear in the future.
    verify_page = auto_archive_context.new_page()
    try:
        root_helpers.visit(verify_page, helpers.FINISHED_LIST_URL)
        prior_card = verify_page.locator(
            ".atr-archived-release",
            has=verify_page.locator(".card-title", has_text=helpers.PRIOR_VERSION),
        )
        expect(prior_card).to_have_count(1)
        expect(prior_card.locator(".badge", has_text="Archived")).to_be_visible()
        expect(prior_card).to_contain_text("Archived on")
    finally:
        verify_page.close()
