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

import pytest

import atr.form as form


def test_to_uri_list_rejects_javascript_scheme() -> None:
    with pytest.raises(ValueError, match="Invalid URI"):
        form.to_uri_list("javascript:alert(1)")


def test_to_uri_list_rejects_data_scheme() -> None:
    with pytest.raises(ValueError, match="Invalid URI"):
        form.to_uri_list("data:text/html,<script>alert(1)</script>")


@pytest.mark.parametrize(
    "uri",
    [
        "http://example.com",
        "https://example.com",
        "git://example.com/repo.git",
        "git+ssh://git@example.com/repo.git",
        "git+https://example.com/repo.git",
        "ssh://git@example.com/repo.git",
        "svn://example.com/repo",
        "mailto:user@example.com",
    ],
)
def test_to_uri_list_accepts_allowed_schemes(uri: str) -> None:
    assert form.to_uri_list(uri) == [uri]


def test_to_uri_list_accepts_multiple_lines_of_allowed_schemes() -> None:
    value = "https://example.com/one\nhttps://example.com/two"
    assert form.to_uri_list(value) == ["https://example.com/one", "https://example.com/two"]


def test_to_uri_list_rejects_mixed_valid_and_dangerous_schemes() -> None:
    with pytest.raises(ValueError, match="Invalid URI"):
        form.to_uri_list("https://example.com\njavascript:alert(1)")
