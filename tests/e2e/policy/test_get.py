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

import e2e.policy.helpers as helpers
from playwright.sync_api import Page, expect


def test_source_excludes_lightweight_initially_empty(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    expect(textarea).to_have_value("")


def test_source_excludes_lightweight_textarea_is_editable(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    expect(textarea).to_be_editable()


def test_source_excludes_lightweight_textarea_is_visible(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_lightweight(page_project)
    expect(textarea).to_be_visible()


def test_source_excludes_rat_initially_empty(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_rat(page_project)
    expect(textarea).to_have_value("")


def test_source_excludes_rat_textarea_is_editable(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_rat(page_project)
    expect(textarea).to_be_editable()


def test_source_excludes_rat_textarea_is_visible(page_project: Page) -> None:
    textarea = helpers.textarea_source_excludes_rat(page_project)
    expect(textarea).to_be_visible()
