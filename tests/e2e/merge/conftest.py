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

from __future__ import annotations

from typing import TYPE_CHECKING

import e2e.helpers as helpers
import e2e.merge.helpers as merge_helpers
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, BrowserContext


@pytest.fixture(scope="module")
def merge_context(browser: Browser) -> Generator[BrowserContext]:
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()

    helpers.log_in(page)
    helpers.delete_release_if_exists(page, merge_helpers.PROJECT_KEY, merge_helpers.VERSION_KEY)

    helpers.visit(page, f"/start/{merge_helpers.PROJECT_KEY}")
    page.locator("input#version_key").fill(merge_helpers.VERSION_KEY)
    page.get_by_role("button", name="Start new release").click()
    page.wait_for_url(f"**/compose/{merge_helpers.PROJECT_KEY}/{merge_helpers.VERSION_KEY}")

    page.close()

    yield context

    context.close()
