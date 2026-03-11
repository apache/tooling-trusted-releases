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
from typing import TYPE_CHECKING, Final

import e2e.helpers as helpers
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, BrowserContext, Page

PROJECT_NAME: Final[str] = "test"
VERSION_NAME: Final[str] = "0.1+e2e-compose"
FILE_NAME: Final[str] = "apache-test-0.2.tar.gz"
CURRENT_DIR: Final[pathlib.Path] = pathlib.Path(__file__).parent.resolve()
COMPOSE_URL: Final[str] = f"/compose/{PROJECT_NAME}/{VERSION_NAME}"


@pytest.fixture(scope="module")
def compose_context(browser: Browser) -> Generator[BrowserContext]:
    """Create a release in the compose phase with completed tasks."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()

    helpers.log_in(page)

    helpers.delete_release_if_exists(page, PROJECT_NAME, VERSION_NAME)

    helpers.visit(page, f"/start/{PROJECT_NAME}")
    page.locator("input#version_name").fill(VERSION_NAME)
    page.get_by_role("button", name="Start new release").click()
    page.wait_for_url(f"**/compose/{PROJECT_NAME}/{VERSION_NAME}")

    helpers.visit(page, f"/upload/{PROJECT_NAME}/{VERSION_NAME}")
    page.locator('input[name="file_data"]').set_input_files(
        [
            f"{CURRENT_DIR}/../test_files/{FILE_NAME}",
            f"{CURRENT_DIR}/../test_files/{FILE_NAME}.sha512",
            f"{CURRENT_DIR}/../test_files/{FILE_NAME}.asc",
        ]
    )
    page.get_by_role("button", name="Add files").click()
    page.wait_for_url(f"**/compose/{PROJECT_NAME}/{VERSION_NAME}")

    helpers.visit(page, f"/compose/{PROJECT_NAME}/{VERSION_NAME}")
    _wait_for_tasks_banner_hidden(page, timeout=60000)

    page.close()

    yield context

    context.close()


@pytest.fixture
def page_compose(compose_context: BrowserContext) -> Generator[Page]:
    """Navigate to the compose page with a fresh page for each test."""
    page = compose_context.new_page()
    helpers.visit(page, COMPOSE_URL)
    _wait_for_tasks_banner_hidden(page, timeout=60000)
    yield page
    page.close()


def _wait_for_tasks_banner_hidden(page: Page, timeout: int = 30000) -> None:
    """Wait for all background tasks to be completed."""
    page.wait_for_selector("#ongoing-tasks-banner", state="hidden", timeout=timeout)
