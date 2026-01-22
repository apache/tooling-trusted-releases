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


def test_tokens_page_has_generate_jwt_button(page_tokens: Page) -> None:
    button = page_tokens.get_by_role("button", name="Generate JWT")
    expect(button).to_be_visible()


def test_tokens_page_has_generate_token_button(page_tokens: Page) -> None:
    button = page_tokens.get_by_role("button", name="Generate token")
    expect(button).to_be_visible()


def test_tokens_page_has_jwt_heading(page_tokens: Page) -> None:
    heading = page_tokens.get_by_role("heading", name="JSON Web Token (JWT)")
    expect(heading).to_be_visible()


def test_tokens_page_has_label_input(page_tokens: Page) -> None:
    label_input = page_tokens.locator('input[name="label"]')
    expect(label_input).to_be_visible()


def test_tokens_page_has_pats_heading(page_tokens: Page) -> None:
    heading = page_tokens.get_by_role("heading", name="Personal Access Tokens (PATs)")
    expect(heading).to_be_visible()


def test_tokens_page_loads(page_tokens: Page) -> None:
    expect(page_tokens).to_have_title("Tokens ~ ATR")
