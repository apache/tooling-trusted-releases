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

PROJECT_KEY: Final[str] = "test"
VERSION_KEY: Final[str] = "0.1+e2e-voting"
FILE_NAME: Final[str] = "apache-test-0.2.tar.gz"
CURRENT_DIR: Final[pathlib.Path] = pathlib.Path(__file__).parent.resolve()
COMPOSE_URL: Final[str] = f"/compose/{PROJECT_KEY}/{VERSION_KEY}"


@pytest.fixture
def page_voting(voting_context: BrowserContext) -> Generator[Page]:
    """Navigate to the voting page via the compose page for each test."""
    page = voting_context.new_page()
    helpers.visit(page, COMPOSE_URL)
    page.locator('a[title="Start a vote on this draft"]').click()
    page.wait_for_url(f"**/voting/{PROJECT_KEY}/{VERSION_KEY}")
    yield page
    page.close()


@pytest.fixture(scope="module")
def voting_context(browser: Browser) -> Generator[BrowserContext]:
    """Create a release ready for voting."""
    context = browser.new_context(ignore_https_errors=True)
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

    helpers.wait_for_upload_and_tasks(page, COMPOSE_URL, FILE_NAME)

    page.close()

    yield context

    context.close()
