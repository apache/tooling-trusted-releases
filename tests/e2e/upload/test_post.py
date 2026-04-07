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

import time
from typing import Final

import e2e.helpers as helpers
from playwright.sync_api import Page, Request, Route, expect

PROJECT_KEY: Final[str] = "test"
VERSION_KEY: Final[str] = "0.1+e2e-upload"
COMPOSE_URL: Final[str] = f"/compose/{PROJECT_KEY}/{VERSION_KEY}"


def test_multi_file_upload(page_upload: Page) -> None:
    """Two files uploaded in one submission produce a single revision."""
    page = page_upload

    upload_posts: list[str] = []
    page.on("request", lambda req: _record_upload_post(req, upload_posts))

    page.locator('input[name="file_data"]').set_input_files(
        [
            {"name": "NOTICE.txt", "mimeType": "text/plain", "buffer": b"Apache Notice"},
            {"name": "README.txt", "mimeType": "text/plain", "buffer": b"Read me"},
        ]
    )

    page.route("**/upload/test/**", _delay_post_response)
    page.get_by_role("button", name="Add files").click()
    expect(page.locator("#upload-progress-container")).to_be_visible(timeout=5000)
    page.unroute("**/upload/test/**")

    page.wait_for_url(f"**{COMPOSE_URL}", timeout=30000)

    assert len(upload_posts) == 1
    assert f"/upload/{PROJECT_KEY}/" in upload_posts[0]

    helpers.wait_for_upload_and_tasks(page, COMPOSE_URL, "NOTICE.txt", timeout=60)
    files_table = page.locator("#files-table-container")
    expect(files_table.get_by_role("cell", name="README.txt", exact=True)).to_be_visible()

    helpers.visit(page, f"/revisions/{PROJECT_KEY}/{VERSION_KEY}")
    expect(page.locator(".card.mb-3")).to_have_count(2)


def _delay_post_response(route: Route) -> None:
    if route.request.method == "POST":
        time.sleep(1)
    route.continue_()


def _record_upload_post(request: Request, upload_posts: list[str]) -> None:
    if request.method == "POST" and "/upload/" in request.url:
        upload_posts.append(request.url)
