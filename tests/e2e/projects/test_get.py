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


def test_archive_banner_absent_when_active(page_active: Page) -> None:
    """No archive warning banner on the compose page when the project is active."""
    expect(page_active.locator(".alert-warning:has-text('archived')")).not_to_be_visible()


def test_archive_banner_visible_when_archived(page_archived: Page) -> None:
    """Archive warning banner appears on the compose page when the project is archived."""
    expect(page_archived.locator(".alert-warning:has-text('archived')")).to_be_visible()
