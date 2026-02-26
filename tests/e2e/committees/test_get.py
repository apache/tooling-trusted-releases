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


def test_filter_button_filters_committees(page_committees: Page) -> None:
    """Clicking filter button should filter committees by text input."""
    filter_input = page_committees.locator("#project-filter")
    filter_button = page_committees.locator("#filter-button")

    filter_input.fill("test")
    filter_button.click()

    count_span = page_committees.locator("#committee-count")
    expect(count_span).not_to_have_text("0")


def test_filter_clears_participant_mode(page_committees: Page) -> None:
    """Using text filter should reset to all committees mode."""
    participant_button = page_committees.locator("#filter-participant-button")
    filter_input = page_committees.locator("#project-filter")
    filter_button = page_committees.locator("#filter-button")

    expect(participant_button).to_have_text("Show all committees")

    filter_input.fill("a")
    filter_button.click()

    expect(participant_button).to_have_text("Show my committees")


def test_filter_enter_key_triggers_filter(page_committees: Page) -> None:
    """Pressing Enter in filter input should trigger filtering."""
    filter_input = page_committees.locator("#project-filter")
    initial_count = page_committees.locator("#committee-count").text_content()

    filter_input.fill("nosuchcommittee")
    filter_input.press("Enter")

    count_span = page_committees.locator("#committee-count")
    expect(count_span).not_to_have_text(initial_count or "")


def test_filter_updates_committee_count(page_committees: Page) -> None:
    """Filtering should update the displayed committee count."""
    filter_input = page_committees.locator("#project-filter")
    filter_button = page_committees.locator("#filter-button")
    count_span = page_committees.locator("#committee-count")

    initial_count = count_span.text_content()

    filter_input.fill("nosuchcommittee")
    filter_button.click()

    expect(count_span).to_have_text("0")
    expect(count_span).not_to_have_text(initial_count or "")


def test_participant_button_toggles_aria_pressed(page_committees: Page) -> None:
    """Clicking participant button should toggle aria pressed state."""
    participant_button = page_committees.locator("#filter-participant-button")

    expect(participant_button).to_have_attribute("aria-pressed", "true")

    participant_button.click()

    expect(participant_button).to_have_attribute("aria-pressed", "false")

    participant_button.click()

    expect(participant_button).to_have_attribute("aria-pressed", "true")


def test_participant_button_toggles_text(page_committees: Page) -> None:
    """Clicking participant button should toggle the button text."""
    participant_button = page_committees.locator("#filter-participant-button")

    expect(participant_button).to_have_text("Show all committees")

    participant_button.click()

    expect(participant_button).to_have_text("Show my committees")
