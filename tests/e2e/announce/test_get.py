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

import e2e.announce.helpers as helpers  # type: ignore[reportMissingImports]
from playwright.sync_api import Page, expect


def test_path_adds_leading_slash(page_announce: Page) -> None:
    """Paths without a leading '/' should have one added."""
    help_text = helpers.fill_path_suffix(page_announce, "apple/banana")
    expect(help_text).to_contain_text("/apple/banana/")


def test_path_adds_trailing_slash(page_announce: Page) -> None:
    """Paths without a trailing '/' should have one added."""
    help_text = helpers.fill_path_suffix(page_announce, "/apple/banana")
    expect(help_text).to_contain_text("/apple/banana/")


def test_path_normalises_dot_slash_prefix(page_announce: Page) -> None:
    """Paths starting with './' should have it converted to '/'."""
    help_text = helpers.fill_path_suffix(page_announce, "./apple")
    expect(help_text).to_contain_text("/apple/")
    expect(help_text).not_to_contain_text("./")


def test_path_normalises_single_dot(page_announce: Page) -> None:
    """A path of '.' should be normalised to '/'."""
    import re

    help_text = helpers.fill_path_suffix(page_announce, ".")
    expect(help_text).to_have_text(re.compile(r"/$"))


def test_path_rejects_double_dots(page_announce: Page) -> None:
    """Paths containing '..' should show an error message."""
    help_text = helpers.fill_path_suffix(page_announce, "../etc/passwd")
    expect(help_text).to_contain_text("must not contain .. or //")


def test_path_rejects_double_slashes(page_announce: Page) -> None:
    """Paths containing '//' should show an error message."""
    help_text = helpers.fill_path_suffix(page_announce, "apple//banana")
    expect(help_text).to_contain_text("must not contain .. or //")


def test_path_rejects_hidden_directory(page_announce: Page) -> None:
    """Paths containing '/.' should show an error message."""
    help_text = helpers.fill_path_suffix(page_announce, "/apple/.hidden/banana")
    expect(help_text).to_contain_text("must not contain /.")


def test_submit_button_disabled_until_confirm_typed(page_announce: Page) -> None:
    """The submit button should be disabled until CONFIRM is typed."""
    submit_button = page_announce.get_by_role("button", name="Send announcement email")
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
