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

import json
import os
from typing import Final

from playwright.sync_api import Page

_BASE_URL: Final[str] = os.environ.get("ATR_BASE_URL", "https://localhost.apache.org:8080")


def test_test_pat_mints_pat_accepted_by_jwt_create(page: Page) -> None:
    pat_response = page.request.get(f"{_BASE_URL}/test/pat")
    assert pat_response.ok
    plaintext = pat_response.text().strip()
    assert plaintext.startswith("secret_")

    jwt_response = page.request.post(
        f"{_BASE_URL}/api/jwt/create",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"asfuid": "test", "pat": plaintext}),
    )
    assert jwt_response.ok
    assert jwt_response.json()["jwt"]
