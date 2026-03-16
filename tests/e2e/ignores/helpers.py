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

from playwright.sync_api import Locator, Page

PROJECT_KEY: Final[str] = "test"
IGNORES_URL: Final[str] = f"/ignores/{PROJECT_KEY}"


def button_add_ignore(page: Page) -> Locator:
    return page.get_by_role("button", name="Add ignore")


def button_delete_first_ignore(page: Page) -> Locator:
    return page.locator(".card").first.get_by_role("button", name="Delete")


def ignore_cards(page: Page) -> Locator:
    return page.locator(".card")


def input_checker_glob(page: Page) -> Locator:
    return page.locator('input[name="checker_glob"]')


def input_release_glob(page: Page) -> Locator:
    return page.locator('input[name="release_glob"]')


def select_status(page: Page) -> Locator:
    return page.locator('select[name="status"]')
