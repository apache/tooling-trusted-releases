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

import json
import os
from typing import TYPE_CHECKING, Any, Final

import e2e.helpers as helpers
import pytest
import strictyaml
from playwright.sync_api import expect

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import APIRequestContext, Browser, BrowserContext, Page

_BASE_URL: Final[str] = os.environ.get("ATR_BASE_URL", "https://localhost.apache.org:8080")

PROJECT_KEY: Final[str] = "test-asfyaml"
EXPORT_URL: Final[str] = f"/project/yaml/{PROJECT_KEY}"

# The .asf.yaml project block we import, then expect to get back unchanged on export.
# It mirrors the inbound schema from infrastructure-asfyaml exactly, so a faithful
# round-trip should reproduce it byte-for-byte once parsed.
SOURCE_YAML: Final[str] = """\
project:
  metadata:
    key: test-asfyaml
    committee: test
    name: Apache Test Asfyaml
    short_description: A round-trip fixture project.
    homepage: https://test.apache.org/
    lifecycle_page: https://test.apache.org/lifecycle
    download_page: https://test.apache.org/download
    bug_database: https://issues.apache.org/jira/browse/TESTASFYAML
    mailing_lists: https://test.apache.org/mail
    repositories:
    - https://github.com/apache/test-asfyaml.git
    standards:
    - https://www.rfc-editor.org/rfc/rfc9999
    categories:
    - build-management
    - testing
    programming_languages:
    - python
  policy:
    vote_recipients:
      to: private@test.apache.org
      cc:
      - dev@test.apache.org
    announce_recipients:
      to: announce@test.apache.org
  features:
    atr_sync: true
"""


@pytest.fixture
def export_page(roundtrip_context: BrowserContext) -> Generator[Page]:
    page = roundtrip_context.new_page()
    helpers.visit(page, EXPORT_URL)
    yield page
    page.close()


@pytest.fixture(scope="module")
def roundtrip_context(browser: Browser) -> Generator[BrowserContext]:
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    helpers.log_in(page)
    _import_project(page.request, _mint_pat_and_jwt(page))
    page.close()
    yield context
    cleanup = context.new_page()
    helpers.log_in(cleanup)
    _delete_project(cleanup)
    cleanup.close()
    context.close()


def test_export_round_trips_imported_yaml(export_page: Page) -> None:
    """Exporting a project reproduces the .asf.yaml block that was imported into it."""
    exported = export_page.locator("#asf-yaml-output").inner_text()
    assert strictyaml.load(exported).data == strictyaml.load(SOURCE_YAML).data


def _delete_project(page: Page) -> None:
    # A project with no releases offers a "Delete project" button in its Actions card.
    helpers.visit(page, f"/projects/{PROJECT_KEY}")
    button = page.get_by_role("button", name="Delete project")
    if button.count() == 0:
        return
    page.once("dialog", lambda dialog: dialog.accept())
    button.click()
    page.wait_for_load_state()


def _import_payload() -> dict[str, Any]:
    # Replays what infrastructure-asfyaml does: key and committee are authored inside
    # the metadata block but the API expects them at the top level, so pop them out.
    block = strictyaml.load(SOURCE_YAML).data["project"]
    metadata = dict(block["metadata"])
    project_key = metadata.pop("key")
    committee_key = metadata.pop("committee")
    payload: dict[str, Any] = {
        "project_key": project_key,
        "committee_key": committee_key,
        "project": metadata,
    }
    if "policy" in block:
        payload["policy"] = block["policy"]
    return payload


def _import_project(request: APIRequestContext, jwt: str) -> None:
    response = request.post(
        f"{_BASE_URL}/api/project/config",
        headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
        data=json.dumps(_import_payload()),
    )
    if not response.ok:
        raise RuntimeError(f"Import via /api/project/config failed ({response.status}): {response.text()}")


def _mint_pat_and_jwt(page: Page) -> str:
    helpers.visit(page, "/tokens")
    page.locator('input[name="label"]').fill("e2e-asfyaml-roundtrip")
    page.get_by_role("button", name="Generate token").click()
    page.wait_for_load_state()
    pat = page.locator(".flash-success code").first.inner_text().strip()

    page.locator('#issue-jwt-form input[name="pat"]').fill(pat)
    page.get_by_role("button", name="Generate JWT").click()
    expect(page.locator("#jwt-container")).to_be_visible()
    return page.locator("#jwt-output").inner_text().strip()
