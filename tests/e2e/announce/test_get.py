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

import re

import e2e.announce.helpers as helpers  # type: ignore[reportMissingImports]
from playwright.sync_api import Page, expect


def test_body_contains_publication_download_url(page_announce: Page) -> None:
    body = page_announce.locator("#body")
    url = f"https://downloads.apache.org/test/{helpers.publish_suffix(helpers.ANNOUNCE_VERSION)}/"
    expect(body).to_have_value(re.compile(f"^{re.escape(url)}$", re.MULTILINE))


def test_submit_button_disabled_until_confirm_typed(page_announce: Page) -> None:
    """The submit button should be disabled until CONFIRM is typed."""
    submit_button = page_announce.get_by_role("button", name="Announce")
    confirm_input = page_announce.locator("#confirm_announce")

    expect(submit_button).to_be_disabled()

    confirm_input.fill("confirm")
    expect(submit_button).to_be_disabled()

    confirm_input.fill("CONFIRM")
    expect(submit_button).to_be_enabled()

    confirm_input.fill("CONFIRME")
    expect(submit_button).to_be_disabled()

    confirm_input.fill("CONFIRM")
    expect(submit_button).to_be_enabled()
