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

from typing import Final

from playwright.sync_api import Locator, Page

PROJECT_KEY: Final[str] = "test"
PROJECT_COMPOSE_URL: Final[str] = f"/projects/{PROJECT_KEY}?tab=compose"
PROJECT_VOTE_URL: Final[str] = f"/projects/{PROJECT_KEY}?tab=vote"
PROJECT_FINISH_URL: Final[str] = f"/projects/{PROJECT_KEY}?tab=finish"
PROJECT_TP_URL: Final[str] = f"/projects/{PROJECT_KEY}?tab=trusted-publishing"


def compose_form_save_button(page: Page) -> Locator:
    return page.locator('form.atr-canary button[type="submit"]').first


def input_github_repository_branch(page: Page) -> Locator:
    return page.locator('input[name="github_repository_branch"]')


def input_github_repository_name(page: Page) -> Locator:
    return page.locator('input[name="github_repository_name"]')


def textarea_github_compose_workflow_path(page: Page) -> Locator:
    return page.locator('textarea[name="github_compose_workflow_path"]')


def textarea_github_finish_workflow_path(page: Page) -> Locator:
    return page.locator('textarea[name="github_finish_workflow_path"]')


def textarea_github_vote_workflow_path(page: Page) -> Locator:
    return page.locator('textarea[name="github_vote_workflow_path"]')


def textarea_source_excludes_lightweight(page: Page) -> Locator:
    return page.locator('textarea[name="source_excludes_lightweight"]')


def textarea_source_excludes_rat(page: Page) -> Locator:
    return page.locator('textarea[name="source_excludes_rat"]')


def trusted_publishing_form_save_button(page: Page) -> Locator:
    return (
        page.locator('input[name="github_repository_name"]')
        .locator("xpath=ancestor::form")
        .locator('button[type="submit"]')
    )
