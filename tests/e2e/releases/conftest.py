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

from typing import TYPE_CHECKING, Final

import e2e.helpers as helpers
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, BrowserContext, Page

RELEASES_URL: Final[str] = "/releases"


@pytest.fixture(scope="module")
def releases_context(browser: Browser) -> Generator[BrowserContext]:
    """Create a browser context with an authenticated user."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    helpers.log_in(page)
    page.close()

    yield context

    context.close()


@pytest.fixture
def page_releases(releases_context: BrowserContext) -> Generator[Page]:
    """Navigate to the releases page with a fresh page for each test."""
    page = releases_context.new_page()
    helpers.visit(page, RELEASES_URL)
    yield page
    page.close()
