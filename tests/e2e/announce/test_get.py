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

import pathlib
import re

import e2e.announce.helpers as helpers  # type: ignore[reportMissingImports]
import pytest
from playwright.sync_api import Page, expect


def test_body_contains_publication_download_url(page_announce: Page) -> None:
    body = page_announce.locator("#body")
    url = f"https://downloads.apache.org/test/{helpers.publish_suffix(helpers.ANNOUNCE_VERSION)}/"
    expect(body).to_have_value(re.compile(f"^{re.escape(url)}$", re.MULTILINE))


@pytest.mark.parametrize("published", [True, False])
def test_finish_refreshes_svn_and_announcement_status(page: Page, published: bool) -> None:
    pending = "Cannot announce until download area publication is complete."
    final_message = "" if published else "Cannot announce until SVN and download area publications are complete."
    svn_html = (
        "<p>Published to SVN dist as r123</p>"
        if published
        else '<p>Publication failed</p><form><input name="download_path_suffix" value="original"></form>'
    )
    responses = iter(
        [
            {"message": pending, "ready": False, "svn_html": svn_html},
            {"message": final_message, "ready": published, "svn_html": "Must not replace this section"},
        ]
    )
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.route("https://atr.test/downloads", lambda route: route.fulfill(json=next(responses)))
    page.route("https://atr.test/", lambda route: route.fulfill(body="<html></html>"))
    page.goto("https://atr.test/")
    page.set_content(
        '<a id="finish-announce" class="disabled" aria-disabled="true" tabindex="-1" '
        'data-announce-url="/announce/example/1.0">Announce</a>'
        '<div id="finish-publication-status" data-status-url="/downloads">'
        '<p id="finish-publication-message">Cannot announce until SVN and download area publications are complete.</p>'
        '<p id="finish-publication-refresh" hidden>Refreshing every 30s.</p></div>'
        '<div id="finish-svn-publish"><div id="finish-svn-publishing">Being published</div></div>'
    )
    page.clock.install()
    page.add_script_tag(path=pathlib.Path(__file__).parents[3] / "atr/static/js/src/finish-downloads.js")
    page.evaluate('document.dispatchEvent(new Event("DOMContentLoaded"))')
    button = page.locator("#finish-announce")
    expect(button).to_have_attribute("aria-disabled", "true")
    assert button.get_attribute("href") is None
    expect(page.locator("#finish-publication-refresh")).to_be_visible()
    page.clock.run_for(30000)
    expect(page.locator("#finish-publication-message")).to_have_text(pending)
    expect(button).to_have_attribute("aria-disabled", "true")
    svn = page.locator("#finish-svn-publish")
    expect(page.locator("#finish-svn-publishing")).to_have_count(0)
    expect(svn).to_have_text("Published to SVN dist as r123" if published else "Publication failed")
    if not published:
        svn.locator("input").fill("edited")
    page.clock.run_for(30000)
    expect(page.locator("#finish-publication-message")).to_have_text(final_message)
    expect(svn).not_to_contain_text("Must not replace this section")
    if not published:
        expect(svn.locator("input")).to_have_value("edited")
        expect(button).to_have_attribute("aria-disabled", "true")
        expect(page.locator("#finish-publication-refresh")).to_be_visible()
        assert requests.count("https://atr.test/downloads") == 2
        return
    expect(button).to_have_attribute("href", "/announce/example/1.0")
    expect(button).to_have_attribute("aria-disabled", "false")
    expect(button).not_to_have_class("disabled")
    expect(page.locator("#finish-publication-message")).to_be_hidden()
    expect(page.locator("#finish-publication-refresh")).to_be_hidden()
    page.clock.run_for(60000)
    assert requests.count("https://atr.test/downloads") == 2


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
