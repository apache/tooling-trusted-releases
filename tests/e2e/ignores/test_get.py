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
from playwright.sync_api import Page, expect


def test_add_ignore_form_visible(page_ignores: Page) -> None:
    expect(helpers.input_release_glob(page_ignores)).to_be_visible()
    expect(helpers.input_checker_glob(page_ignores)).to_be_visible()
    expect(helpers.select_status(page_ignores)).to_be_visible()
    expect(helpers.button_add_ignore(page_ignores)).to_be_visible()


def test_ignores_page_has_heading(page_ignores: Page) -> None:
    heading = page_ignores.locator("h1")
    expect(heading).to_contain_text("Ignored checks")


def test_ignores_page_shows_project_key(page_ignores: Page) -> None:
    expect(page_ignores.locator("body")).to_contain_text(f"project {helpers.PROJECT_KEY}")


def test_no_ignores_initially(page_ignores: Page) -> None:
    expect(page_ignores.locator("body")).to_contain_text("No ignores found")
