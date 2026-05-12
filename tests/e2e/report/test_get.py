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

import re

import pytest
from playwright.sync_api import Locator, Page, expect


def test_member_path_filter_clear_shows_all_rows(
    page_report: Page,
    member_rows: Locator,
    member_filter_input: Locator,
) -> None:
    """Clearing the member path filter should show all rows again."""
    initial_visible = page_report.locator(".atr-result-member:not(.page-member-path-hide)")
    initial_count = initial_visible.count()

    member_filter_input.fill("NO_SUCH_PATH")
    member_filter_input.fill("")

    visible_after_clear = page_report.locator(".atr-result-member:not(.page-member-path-hide)")
    expect(visible_after_clear).to_have_count(initial_count)


def test_member_path_filter_hides_non_matching_rows(
    page_report: Page,
    member_rows: Locator,
    member_filter_input: Locator,
) -> None:
    """Typing in the member path filter should hide non matching rows."""
    member_filter_input.fill("NO_SUCH_PATH")

    visible_rows = page_report.locator(".atr-result-member:not(.page-member-path-hide):not(.atr-hide)")
    expect(visible_rows).to_have_count(0)


def test_member_path_filter_input_exists(page_report: Page, member_rows: Locator) -> None:
    """The member path filter input should exist if there are member results."""
    filter_input = page_report.locator("#member-path-filter")
    expect(filter_input).to_be_visible()


def test_member_path_filter_shows_matching_rows(
    page_report: Page,
    member_rows: Locator,
    member_filter_input: Locator,
) -> None:
    """Typing a matching path in the filter should show matching rows."""
    first_row = member_rows.first
    first_path = first_row.locator("td").first.text_content()
    if not first_path:
        pytest.fail("First member row has no path text")

    member_filter_input.fill(first_path[:10])

    visible_rows = page_report.locator(".atr-result-member:not(.page-member-path-hide)")
    expect(visible_rows.first).to_be_attached()


def test_member_status_toggle_buttons_exist(page_report: Page, member_rows: Locator) -> None:
    """Member status toggle buttons should exist if there are member results."""
    toggle_buttons = page_report.locator(".page-toggle-status[data-type='member']")
    expect(toggle_buttons.first).to_be_attached()


def test_primary_note_toggle_shows_hidden_rows(
    page_report: Page,
    primary_note_toggle: Locator,
    primary_note_rows: Locator,
) -> None:
    """Clicking the primary success toggle should show hidden success rows."""
    primary_note_toggle.click()

    expect(primary_note_rows.first).not_to_have_class(re.compile(r"atr-hide"))


def test_primary_status_toggle_buttons_exist(page_report: Page) -> None:
    """At least one primary status toggle button should exist."""
    toggle_buttons = page_report.locator(".page-toggle-status[data-type='primary']")
    expect(toggle_buttons.first).to_be_attached()


def test_primary_toggle_button_style_changes(
    page_report: Page,
    primary_note_toggle: Locator,
) -> None:
    """Clicking a primary toggle button should change its style."""
    expect(primary_note_toggle).to_have_class(re.compile(r"btn-outline-success"))

    primary_note_toggle.click()

    expect(primary_note_toggle).to_have_class(re.compile(r"btn-success"))
    expect(primary_note_toggle).not_to_have_class(re.compile(r"btn-outline-success"))


def test_primary_toggle_button_text_changes(
    page_report: Page,
    primary_note_toggle: Locator,
) -> None:
    """Clicking a primary toggle button should change its text."""
    btn_span = primary_note_toggle.locator("span")
    expect(btn_span).to_have_text("Show")

    primary_note_toggle.click()

    expect(btn_span).to_have_text("Hide")


def test_row_striping_updates_after_filter(page_report: Page) -> None:
    """Row striping should update when filtering member results."""
    page_report.evaluate("""() => {
        const tbody = document.querySelector('.atr-result-member').closest('tbody');
        for (const path of ['synth/alpha.py', 'synth/beta.py', 'synth/gamma.py']) {
            const tr = document.createElement('tr');
            tr.className = 'atr-result-member atr-result-status-note';
            const td = document.createElement('td');
            td.textContent = path;
            tr.appendChild(td);
            tbody.appendChild(tr);
        }
        updateMemberStriping();
    }""")

    synth_rows = page_report.locator(".atr-result-member").filter(has_text="synth/")
    expect(synth_rows).to_have_count(3)

    page_report.locator("#member-path-filter").fill("synth/alpha")

    visible = page_report.locator(".atr-result-member:not(.page-member-path-hide):not(.atr-hide)")
    expect(visible).to_have_count(1)
    expect(visible.first).to_have_class(re.compile(r"page-member-visible-odd"))


def test_toggle_all_details_button_visible(page_report: Page) -> None:
    """The toggle all details button should be visible."""
    toggle_btn = page_report.locator("#btn-toggle-all-details")
    expect(toggle_btn).to_be_visible()


def test_toggle_all_details_closes_open_details(
    page_report: Page,
    details_elements: Locator,
) -> None:
    """Clicking toggle all details when all are open should close them."""
    for i in range(details_elements.count()):
        details_elements.nth(i).evaluate("el => el.open = true")

    page_report.locator("#btn-toggle-all-details").click()

    for i in range(details_elements.count()):
        expect(details_elements.nth(i)).not_to_have_attribute("open", "")


def test_toggle_all_details_opens_closed_details(
    page_report: Page,
    details_elements: Locator,
) -> None:
    """Clicking toggle all details should open all closed details elements."""
    for i in range(details_elements.count()):
        details_elements.nth(i).evaluate("el => el.open = false")

    page_report.locator("#btn-toggle-all-details").click()

    for i in range(details_elements.count()):
        expect(details_elements.nth(i)).to_have_attribute("open", "")
