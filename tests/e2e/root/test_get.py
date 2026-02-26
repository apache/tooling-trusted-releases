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


def test_about_loads(page_about: Page) -> None:
    expect(page_about).to_have_title("About ATR ~ ATR")


def test_index_has_login_button(page_index: Page) -> None:
    login_button = page_index.get_by_role("link", name="Log in")
    expect(login_button).to_be_visible()


def test_index_loads(page_index: Page) -> None:
    expect(page_index).to_have_title("Apache Trusted Releases")


def test_policies_has_heading(page_policies: Page) -> None:
    heading = page_policies.get_by_role("heading", name="Release policy", level=1)
    expect(heading).to_be_visible()


def test_policies_loads(page_policies: Page) -> None:
    expect(page_policies).to_have_title("Policies ~ ATR")
