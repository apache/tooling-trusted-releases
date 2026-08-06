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


def test_repository_uri_list_accepts_web_and_vcs_schemes() -> None:
    submitted = "https://github.com/apache/x\ngit+ssh://git@host/x.git\nsvn://svn.apache.org/x"

    assert form.to_repository_uri_list(submitted) == [
        "https://github.com/apache/x",
        "git+ssh://git@host/x.git",
        "svn://svn.apache.org/x",
    ]


@pytest.mark.parametrize("uri", ["javascript:alert(1)", "data:text/html,pwned", "no-scheme-here"])
def test_repository_uri_list_rejects_browser_executable_or_schemeless(uri: str) -> None:
    with pytest.raises(ValueError, match="disallowed or missing scheme"):
        form.to_repository_uri_list(uri)


def test_standard_uri_list_accepts_web_schemes() -> None:
    assert form.to_standard_uri_list("https://example.org/spec\nhttp://example.org/older") == [
        "https://example.org/spec",
        "http://example.org/older",
    ]


@pytest.mark.parametrize("uri", ["javascript:alert(1)", "data:text/html,pwned", "git+ssh://git@host/x.git"])
def test_standard_uri_list_rejects_non_web_schemes(uri: str) -> None:
    # A VCS locator is acceptable for a repository but a standard must be a web page
    with pytest.raises(ValueError, match="disallowed or missing scheme"):
        form.to_standard_uri_list(uri)
