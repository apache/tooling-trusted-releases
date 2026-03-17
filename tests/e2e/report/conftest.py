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

    from playwright.sync_api import Browser, BrowserContext, Locator, Page

PROJECT_KEY: Final[str] = "test"
VERSION_KEY: Final[str] = "0.1+e2e-report"
FILE_NAME: Final[str] = "apache-test-0.2.tar.gz"
CURRENT_DIR: Final[pathlib.Path] = pathlib.Path(__file__).parent.resolve()
REPORT_URL: Final[str] = f"/report/{PROJECT_KEY}/{VERSION_KEY}/{FILE_NAME}"
COMPOSE_URL: Final[str] = f"/compose/{PROJECT_KEY}/{VERSION_KEY}"


@pytest.fixture
def details_elements(page_report: Page) -> Locator:
    """Get details elements, fail if none exist."""
    elements = page_report.locator("details")
    if elements.count() == 0:
        pytest.fail("No details elements found")
    return elements


@pytest.fixture
def member_filter_input(page_report: Page, member_rows: Locator) -> Locator:
    """Get member path filter input, fail if not present."""
    filter_input = page_report.locator("#member-path-filter")
    if filter_input.count() == 0:
        pytest.fail("Member path filter not present")
    return filter_input


@pytest.fixture
def member_rows(page_report: Page) -> Locator:
    """Get member result rows, fail if none exist."""
    rows = page_report.locator(".atr-result-member")
    if rows.count() == 0:
        pytest.fail("No member results found")
    return rows


@pytest.fixture
def page_report(report_context: BrowserContext) -> Generator[Page]:
    """Navigate to the report page with a fresh page for each test."""
    page = report_context.new_page()
    helpers.visit(page, REPORT_URL)
    yield page
    page.close()


@pytest.fixture
def primary_success_rows(page_report: Page) -> Locator:
    """Get primary success rows, fail if none exist."""
    rows = page_report.locator(".atr-result-primary.atr-result-status-success")
    if rows.count() == 0:
        pytest.fail("No primary success rows found")
    return rows


@pytest.fixture
def primary_success_toggle(page_report: Page) -> Locator:
    """Get primary success toggle button, fail if not present."""
    toggle = page_report.locator("#btn-toggle-primary-success")
    if toggle.count() == 0:
        pytest.fail("Primary success toggle not present")
    return toggle


@pytest.fixture(scope="module")
def report_context(browser: Browser, verify_license_check_mode: None) -> Generator[BrowserContext]:
    """Create a release with an uploaded file and completed tasks."""
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

    helpers.wait_for_upload_and_tasks(page, f"/compose/{PROJECT_KEY}/{VERSION_KEY}", FILE_NAME)
    _poll_for_member_rows(page, REPORT_URL)

    page.close()

    yield context

    context.close()


@pytest.fixture(scope="module")
def verify_license_check_mode(browser: Browser) -> None:
    """Verify that the test project has the correct license check mode."""
    context = browser.new_context(ignore_https_errors=True)
    policy = helpers.api_get(context.request, f"/api/project/policy/{PROJECT_KEY}")
    context.close()

    mode = policy.get("policy_license_check_mode", "").upper()
    if mode == "RAT":
        pytest.fail(f"Test project has policy_license_check_mode={mode}. Member results will not be produced.")


def _poll_for_member_rows(page: Page, report_url: str, max_attempts: int = 30) -> None:
    """Poll the report page until member rows are available."""
    for _ in range(max_attempts):
        helpers.visit(page, report_url)
        if page.locator(".atr-result-member").count() > 0:
            return
        time.sleep(1)
    pytest.fail("No member results found after waiting for report checks to complete")
