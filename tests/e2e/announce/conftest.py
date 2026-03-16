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

import pathlib
import time
from typing import TYPE_CHECKING, Final

import e2e.helpers as helpers
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, BrowserContext, Page

PROJECT_KEY: Final[str] = "test"
# TODO: We need a convention to scope this per test
VERSION_KEY: Final[str] = "0.1+e2e-announce"
FILE_NAME: Final[str] = "apache-test-0.2.tar.gz"
CURRENT_DIR: Final[pathlib.Path] = pathlib.Path(__file__).parent.resolve()
ANNOUNCE_URL: Final[str] = f"/announce/{PROJECT_KEY}/{VERSION_KEY}"
FINISH_URL: Final[str] = f"/finish/{PROJECT_KEY}/{VERSION_KEY}"


@pytest.fixture(scope="module")
def announce_context(browser: Browser) -> Generator[BrowserContext]:
    """Create a release in the finish phase."""

    context = browser.new_context(
        ignore_https_errors=True,
        # Needed for the copy variable buttons
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = context.new_page()

    helpers.log_in(page)

    helpers.delete_release_if_exists(page, PROJECT_KEY, VERSION_KEY)

    helpers.visit(page, f"/start/{PROJECT_KEY}")
    page.locator("input#version_key").fill(VERSION_KEY)
    page.get_by_role("button", name="Start new release").click()
    page.wait_for_url(f"**/compose/{PROJECT_KEY}/{VERSION_KEY}")

    helpers.visit(page, f"/upload/{PROJECT_KEY}/{VERSION_KEY}")
    page.locator('input[name="file_data"]').set_input_files(
        [
            f"{CURRENT_DIR}/../test_files/{FILE_NAME}",
            f"{CURRENT_DIR}/../test_files/{FILE_NAME}.sha512",
            f"{CURRENT_DIR}/../test_files/{FILE_NAME}.asc",
        ]
    )
    page.get_by_role("button", name="Add files").click()
    page.wait_for_url(f"**/compose/{PROJECT_KEY}/{VERSION_KEY}")

    helpers.wait_for_upload_and_tasks(page, f"/compose/{PROJECT_KEY}/{VERSION_KEY}", FILE_NAME)

    page.locator('a[title="Start a vote on this draft"]').click()
    page.wait_for_load_state()

    page.get_by_role("button", name="Send vote email").click()
    page.wait_for_url(f"**/vote/{PROJECT_KEY}/{VERSION_KEY}")

    helpers.visit(page, f"/vote/{PROJECT_KEY}/{VERSION_KEY}")
    _poll_for_vote_thread_link(page)

    resolve_form = page.locator(f'form[action="/resolve/{PROJECT_KEY}/{VERSION_KEY}"]')
    resolve_form.get_by_role("button", name="Resolve vote").click()
    page.wait_for_url(f"**/resolve/{PROJECT_KEY}/{VERSION_KEY}")

    page.locator('input[name="vote_result"][value="Passed"]').check()
    page.get_by_role("button", name="Resolve vote").click()
    page.wait_for_url(f"**/finish/{PROJECT_KEY}/{VERSION_KEY}")

    page.close()

    yield context

    context.close()


@pytest.fixture
def page_announce(announce_context: BrowserContext) -> Generator[Page]:
    """Navigate to the announce page with a fresh page for each test."""
    page = announce_context.new_page()
    helpers.visit(page, ANNOUNCE_URL)
    yield page
    page.close()


@pytest.fixture
def page_finish(announce_context: BrowserContext) -> Generator[Page]:
    """Navigate to the finish page with a fresh page for each test."""
    page = announce_context.new_page()
    helpers.visit(page, FINISH_URL)
    yield page
    page.close()


def _poll_for_vote_thread_link(page: Page, max_attempts: int = 30) -> None:
    """Poll for the vote task to be completed."""
    thread_link_locator = page.locator('a:has-text("view thread")')
    for _ in range(max_attempts):
        if thread_link_locator.is_visible(timeout=500):
            return
        time.sleep(0.5)
        page.reload()
