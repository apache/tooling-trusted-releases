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

import e2e.ignores.helpers as helpers
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.skip(reason="Check ignores HTML UI is hidden")


def test_add_ignore_creates_card(page_ignores: Page) -> None:
    helpers.input_checker_glob(page_ignores).fill("atr.tasks.checks.rat.*")
    helpers.select_status(page_ignores).select_option("Suggestion")
    helpers.button_add_ignore(page_ignores).click()
    page_ignores.wait_for_load_state()

    expect(helpers.ignore_cards(page_ignores)).to_have_count(1)


def test_add_ignore_persists_values(page_ignores: Page) -> None:
    helpers.input_release_glob(page_ignores).fill("test-1.0.*")
    helpers.input_checker_glob(page_ignores).fill("atr.tasks.checks.signature.*")
    helpers.select_status(page_ignores).select_option("Exception")
    helpers.button_add_ignore(page_ignores).click()
    page_ignores.wait_for_load_state()

    card = helpers.ignore_cards(page_ignores).first
    expect(card.locator('input[name="release_glob"]')).to_have_value("test-1.0.*")
    expect(card.locator('input[name="checker_glob"]')).to_have_value("atr.tasks.checks.signature.*")


def test_delete_ignore_removes_card(page_ignores: Page) -> None:
    helpers.input_checker_glob(page_ignores).fill("atr.tasks.checks.license.*")
    helpers.select_status(page_ignores).select_option("Concern")
    helpers.button_add_ignore(page_ignores).click()
    page_ignores.wait_for_load_state()

    expect(helpers.ignore_cards(page_ignores)).to_have_count(1)

    helpers.button_delete_first_ignore(page_ignores).click()
    page_ignores.wait_for_load_state()

    expect(helpers.ignore_cards(page_ignores)).to_have_count(0)
