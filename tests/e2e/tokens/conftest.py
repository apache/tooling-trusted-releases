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

from __future__ import annotations

from typing import TYPE_CHECKING

import e2e.helpers as helpers
import e2e.tokens.helpers as token_helpers
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Page


@pytest.fixture
def page_tokens(page: Page) -> Generator[Page]:
    helpers.log_in(page)
    helpers.visit(page, token_helpers.TOKENS_PATH)
    yield page


@pytest.fixture
def page_tokens_clean(page: Page) -> Generator[Page]:
    helpers.log_in(page)
    helpers.visit(page, token_helpers.TOKENS_PATH)
    token_helpers.delete_token_by_label(page, token_helpers.TOKEN_LABEL_FOR_TESTING)
    yield page
