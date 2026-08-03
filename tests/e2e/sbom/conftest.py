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
import e2e.sbom.helpers as sbom_helpers
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Page


@pytest.fixture
def page_release_with_file(page: Page) -> Generator[Page]:
    helpers.log_in(page)

    helpers.delete_release_if_exists(page, sbom_helpers.PROJECT_KEY, sbom_helpers.VERSION_KEY)

    helpers.visit(page, f"/start/{sbom_helpers.PROJECT_KEY}")
    page.get_by_role("textbox").type(sbom_helpers.VERSION_KEY)
    page.get_by_role("button", name="Start new release").click()
    helpers.visit(page, f"/upload/{sbom_helpers.PROJECT_KEY}/{sbom_helpers.VERSION_KEY}")
    page.locator('input[name="file_data"]').set_input_files(
        [
            f"{sbom_helpers.CURRENT_DIR}/../test_files/{sbom_helpers.FILE_NAME}",
            f"{sbom_helpers.CURRENT_DIR}/../test_files/{sbom_helpers.FILE_NAME}.sha512",
            f"{sbom_helpers.CURRENT_DIR}/../test_files/{sbom_helpers.FILE_NAME}.asc",
            f"{sbom_helpers.CURRENT_DIR}/../test_files/{sbom_helpers.FILE_NAME}.cdx.json",
        ]
    )
    page.get_by_role("button", name="Add files").click()
    page.wait_for_url(f"**/compose/{sbom_helpers.PROJECT_KEY}/{sbom_helpers.VERSION_KEY}")
    helpers.wait_for_upload_and_tasks(
        page,
        f"/compose/{sbom_helpers.PROJECT_KEY}/{sbom_helpers.VERSION_KEY}",
        sbom_helpers.FILE_NAME,
    )
    yield page
