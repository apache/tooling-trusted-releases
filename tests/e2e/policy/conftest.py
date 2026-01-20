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
import e2e.policy.helpers as policy_helpers
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Page


@pytest.fixture
def page_project(page: Page) -> Generator[Page]:
    helpers.log_in(page)
    _clear_policy_excludes(page)
    helpers.visit(page, policy_helpers.PROJECT_URL)
    yield page
    _clear_policy_excludes(page)


def _clear_policy_excludes(page: Page) -> None:
    helpers.visit(page, policy_helpers.PROJECT_URL)
    policy_helpers.textarea_source_excludes_lightweight(page).fill("")
    policy_helpers.textarea_source_excludes_rat(page).fill("")
    policy_helpers.compose_form_save_button(page).click()
    page.wait_for_load_state()
