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
import time
from typing import Any, Final

from playwright.sync_api import APIRequestContext, Page

_ATR_BASE_URL: Final[str] = os.environ.get("ATR_BASE_URL", "https://localhost.apache.org:8080")


def api_get(request: APIRequestContext, path: str) -> dict[str, Any]:
    response = request.get(f"{_ATR_BASE_URL}{path}")
    return response.json()


def delete_release_if_exists(page: Page, project_key: str, version_key: str) -> None:
    release_key = f"{project_key}-{version_key}"
    visit(page, "/admin/delete-release")
    checkbox = page.locator(f'input[name="releases_to_delete"][value="{release_key}"]')
    if checkbox.count() == 0:
        return
    checkbox.check()
    page.locator('input[name="confirm_delete"]').fill("DELETE")
    page.get_by_role("button", name="Delete selected releases permanently").click()
    page.wait_for_load_state()


def log_in(page: Page) -> None:
    page.goto(f"{_ATR_BASE_URL}/test/login")
    page.wait_for_load_state()


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
