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
from typing import TYPE_CHECKING

import e2e.helpers as helpers  # type: ignore[reportMissingImports]
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Page

PROJECT_NAME = "test"
VERSION_NAME = "0.1"
FILE_NAME = "apache-test-0.2.tar.gz"
CURRENT_DIR = pathlib.Path(__file__).parent.resolve()


@pytest.fixture
def page_release_with_file(page: Page) -> Generator[Page]:
    helpers.log_in(page)
    helpers.visit(page, f"/start/{PROJECT_NAME}")
    page.get_by_role("textbox").type(VERSION_NAME)
    page.get_by_role("button", name="Start new release").click()
    helpers.visit(page, f"/upload/{PROJECT_NAME}/{VERSION_NAME}")
    page.locator('input[name="file_data"]').set_input_files(f"{CURRENT_DIR}/../test_files/{FILE_NAME}")
    page.get_by_role("button", name="Add files").click()
    helpers.visit(page, f"/compose/{PROJECT_NAME}/{VERSION_NAME}")
    page.wait_for_selector("#ongoing-tasks-banner", state="hidden")
    yield page
