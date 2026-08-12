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

from playwright.sync_api import Page, expect


def test_filter_narrows_committee_count(page_releases: Page) -> None:
    """Filtering by a non-matching name drives the committee count to zero."""
    count_span = page_releases.locator("#committee-count")
    if count_span.count() == 0:
        return
    filter_input = page_releases.locator("#project-filter")
    filter_button = page_releases.locator("#filter-button")

    filter_input.fill("nosuchcommitteeorproject")
    filter_button.click()

    expect(count_span).to_have_text("0")


def test_project_tiles_link_to_catalog(page_releases: Page) -> None:
    """Project tiles link through to a project's catalog page."""
    tiles = page_releases.locator(".page-project-subcard a.stretched-link")
    if tiles.count() == 0:
        return
    href = tiles.first.get_attribute("href")
    assert href is not None
    assert "/catalog/" in href


def test_releases_page_shows_committee_grid(page_releases: Page) -> None:
    """The releases page renders the committee-grouped grid heading."""
    expect(page_releases.locator("h1")).to_have_text("Releases")
