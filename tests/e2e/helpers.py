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

import os
import pathlib
import time
from typing import Any, Final

from playwright.sync_api import APIRequestContext, Page

_ATR_BASE_URL: Final[str] = os.environ.get("ATR_BASE_URL", "https://localhost.apache.org:8080")

_TEST_KEY_FILE: Final[pathlib.Path] = (
    pathlib.Path(__file__).parent.resolve() / "test_files" / "ATR_Test_0x1913BD07F118B758_public.asc"
)
_TEST_KEY_ID: Final[str] = "1913BD07F118B758"
_TEST_KEY_COMMITTEE: Final[str] = "test"


def api_get(request: APIRequestContext, path: str) -> dict[str, Any]:
    response = request.get(f"{_ATR_BASE_URL}{path}")
    return response.json()


def api_post(request: APIRequestContext, path: str, data: dict[str, Any]) -> dict[str, Any]:
    response = request.post(f"{_ATR_BASE_URL}{path}", data=data)
    if not response.ok:
        raise RuntimeError(f"POST {path} failed with status {response.status}: {response.text()}")
    return response.json()


def delete_release_if_exists(page: Page, project_key: str, version_key: str) -> None:
    # The confirm page 404s when the release is gone, so a missing confirm field
    # means there is nothing to delete
    visit(page, f"/admin/current/releases/{project_key}/{version_key}/delete")
    confirm = page.locator('input[name="confirm_delete"]')
    if confirm.count() == 0:
        return
    confirm.fill("DELETE")
    page.get_by_role("button", name="Delete this release permanently").click()
    page.wait_for_load_state()


def ensure_test_user_key(page: Page) -> None:
    """Make sure the bundled OpenPGP key is associated with the test user.

    Idempotent - if the key already shows up on the user's keys page, this
    is a no-op. Anything that calls log_in() gets this for free.
    """
    visit(page, "/keys")
    if page.get_by_text(_TEST_KEY_ID, exact=False).count() > 0:
        return
    visit(page, "/keys/add")
    page.locator('input[name="public_key_file"]').set_input_files(str(_TEST_KEY_FILE))
    page.locator(f'input[name="selected_committees"][value="{_TEST_KEY_COMMITTEE}"]').check()
    page.get_by_role("button", name="Add OpenPGP key").click()
    page.wait_for_load_state()


def log_in(page: Page) -> None:
    page.goto(f"{_ATR_BASE_URL}/test/login")
    page.wait_for_load_state()
    ensure_test_user_key(page)


def visit(page: Page, path: str) -> None:
    page.goto(f"{_ATR_BASE_URL}{path}")
    page.wait_for_load_state()


def wait_for_upload_and_tasks(page: Page, compose_url: str, file_name: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while True:
        visit(page, compose_url)
        if page.get_by_role("cell", name=file_name, exact=True).count() > 0:
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"{file_name} did not appear on {compose_url} within {timeout}s")
        time.sleep(1)
    remaining_ms = max(int((deadline - time.monotonic()) * 1000), 1000)
    page.wait_for_selector("#ongoing-tasks-banner", state="hidden", timeout=remaining_ms)
