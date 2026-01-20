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

import e2e.helpers as helpers
import e2e.sbom.helpers as sbom_helpers
from playwright.sync_api import Page, expect


def test_sbom_generate(page_release_with_file: Page) -> None:
    # Make sure that the test file exists
    file_cell = page_release_with_file.get_by_role("cell", name=sbom_helpers.FILE_NAME)
    expect(file_cell).to_be_visible()

    # Generate an SBOM for the file
    helpers.visit(
        page_release_with_file,
        f"/draft/tools/{sbom_helpers.PROJECT_NAME}/{sbom_helpers.VERSION_NAME}/{sbom_helpers.FILE_NAME}",
    )
    generate_button = page_release_with_file.get_by_role("button", name="SBOM")
    generate_button.click()

    # Check that the generated SBOM exists now
    helpers.visit(page_release_with_file, f"/compose/{sbom_helpers.PROJECT_NAME}/{sbom_helpers.VERSION_NAME}")
    page_release_with_file.wait_for_selector("#ongoing-tasks-banner", state="hidden")
    page_release_with_file.reload()

    sbom_cell = page_release_with_file.get_by_role("cell", name=f"{sbom_helpers.FILE_NAME}.cdx.json")
    expect(sbom_cell).to_be_visible()
