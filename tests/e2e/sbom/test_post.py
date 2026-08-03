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


def test_sbom_report(page_release_with_file: Page) -> None:
    # The uploaded SBOM shows up as a file of its own in the release
    sbom_cell = page_release_with_file.locator("#files-table-container").get_by_role(
        "cell", name=f"{sbom_helpers.FILE_NAME}.cdx.json"
    )
    expect(sbom_cell).to_be_visible()

    # The SBOM's own row is the single way in to its report
    view_sbom = page_release_with_file.locator("#files-table-container").get_by_role("link", name="View SBOM")
    expect(view_sbom).to_be_visible()
    view_sbom.click()

    # One report, split into a Content section and a Quality section
    expect(page_release_with_file.get_by_role("heading", name="SBOM report")).to_be_visible()
    expect(page_release_with_file.get_by_role("heading", name="Content", exact=True)).to_be_visible()
    expect(page_release_with_file.get_by_role("heading", name="Components", exact=True)).to_be_visible()
    expect(page_release_with_file.get_by_role("heading", name="Licenses")).to_be_visible()
    expect(page_release_with_file.get_by_role("heading", name="Vulnerabilities")).to_be_visible()
    expect(page_release_with_file.get_by_role("heading", name="Quality", exact=True)).to_be_visible()
    expect(page_release_with_file.get_by_role("heading", name="Conformance report")).to_be_visible()

    # Nothing on the report can change the SBOM in place
    expect(page_release_with_file.get_by_role("button", name="Scan file")).to_have_count(0)
    expect(page_release_with_file.get_by_role("button", name="Augment SBOM")).to_have_count(0)

    # The draft tools no longer offer to generate an SBOM either
    helpers.visit(
        page_release_with_file,
        f"/draft/tools/{sbom_helpers.PROJECT_KEY}/{sbom_helpers.VERSION_KEY}/{sbom_helpers.FILE_NAME}",
    )
    expect(page_release_with_file.get_by_role("button", name="SBOM")).to_have_count(0)
