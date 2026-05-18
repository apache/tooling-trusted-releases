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

import e2e.helpers as root_helpers
from playwright.sync_api import Locator, Page

# Auto-archive scenario constants. Picks a permissive cycle_match so the
# regex doesn't reject the older VERSION_KEY in announce/conftest.py
# ('0.1+e2e-announce' maps into cycle '0'), and pairs two releases that
# share cycle '9' so the prior is a real archive candidate.
PRIOR_VERSION: Final[str] = "9.0.0+e2e-aap-prior"
CURRENT_VERSION: Final[str] = "9.0.1+e2e-aap-current"
CYCLE_MATCH: Final[str] = r"^(\d+)\.\d+.*$"
LIFECYCLE_POLICY_URL: Final[str] = "/projects/test?tab=lifecycle"
FINISH_POLICY_URL: Final[str] = "/projects/test?tab=finish"
FINISHED_LIST_URL: Final[str] = "/releases/finished/test"
CURRENT_ANNOUNCE_URL: Final[str] = f"/announce/test/{CURRENT_VERSION}"
START_URL: Final[str] = "/start/test"


def ensure_cycle_match(page: Page, cycle_match: str) -> None:
    """Set the project's cycle_match if it isn't already at the desired value."""
    root_helpers.visit(page, LIFECYCLE_POLICY_URL)
    field = page.locator("input#cycle_match")
    if field.input_value() == cycle_match:
        return
    field.fill(cycle_match)
    page.get_by_role("button", name="Save").click()
    page.wait_for_load_state()


def ensure_policy_auto_archive(page: Page, enabled: bool) -> None:
    """Toggle the project-level auto-archive policy if it isn't where we want it.

    Requires cycle_match to be set first, otherwise the policy form skips
    the archive_prior_release field entirely.
    """
    ensure_cycle_match(page, CYCLE_MATCH)
    root_helpers.visit(page, FINISH_POLICY_URL)
    checkbox = page.locator("input#archive_prior_release")
    if checkbox.is_checked() == enabled:
        return
    if enabled:
        checkbox.check()
    else:
        checkbox.uncheck()
    page.get_by_role("button", name="Save").click()
    page.wait_for_load_state()


def fill_path_suffix(page: Page, value: str) -> Locator:
    """Fill the download path suffix input and return the help text locator."""
    help_text = page.locator("#download_path_suffix + .form-text")
    page.locator("#download_path_suffix").fill(value)
    return help_text
