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

import e2e.helpers as root_helpers
import e2e.policy.helpers as helpers
from playwright.sync_api import Page, expect


def test_source_excludes_lightweight_can_be_cleared(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    textarea.fill("*.min.js")
    helpers.compose_form_save_button(page_project).click()
    page_project.wait_for_load_state()

    root_helpers.visit(page_project, helpers.PROJECT_URL)
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    textarea.fill("")
    helpers.compose_form_save_button(page_project).click()
    page_project.wait_for_load_state()

    root_helpers.visit(page_project, helpers.PROJECT_URL)
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    expect(textarea).to_have_value("")


def test_source_excludes_lightweight_preserves_internal_whitespace(page_project: Page) -> None:
    # TODO: There is a problem with leading and trailing whitespace in the form
    # Anyway, this is an edge case, and perhaps normalisation would even be better
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    textarea.fill("first\n  middle with spaces  \nlast")
    helpers.compose_form_save_button(page_project).click()
    page_project.wait_for_load_state()

    root_helpers.visit(page_project, helpers.PROJECT_URL)
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    expect(textarea).to_have_value("first\n  middle with spaces  \nlast")


def test_source_excludes_lightweight_value_persists(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    textarea.fill("*.min.js\nvendor/**")
    helpers.compose_form_save_button(page_project).click()
    page_project.wait_for_load_state()

    root_helpers.visit(page_project, helpers.PROJECT_URL)
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    expect(textarea).to_have_value("*.min.js\nvendor/**")


def test_source_excludes_rat_value_persists(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_rat(page_project)
    textarea.fill("third-party/**\n*.generated")
    helpers.compose_form_save_button(page_project).click()
    page_project.wait_for_load_state()

    root_helpers.visit(page_project, helpers.PROJECT_URL)
    textarea = helpers.textarea_source_excludes_rat(page_project)
    expect(textarea).to_have_value("third-party/**\n*.generated")
