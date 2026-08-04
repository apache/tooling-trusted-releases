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

import atr.models.safe as safe


@pytest.mark.parametrize("value", [".", "./a", "a/./b", "a/."])
def test_rel_path_rejects_dot_segments(value: str):
    with pytest.raises(ValueError, match="directory traversal"):
        safe.RelPath(value)


@pytest.mark.parametrize("cls", [safe.Alphanumeric, safe.ProjectKey, safe.VersionKey, safe.ReleaseKey])
@pytest.mark.parametrize(
    "bad",
    ["\n", "\t", "\r", "\x1f", "\x7f", "\u200b", "e\u0301"],
)
def test_safe_types_reject_bad_bytes(cls: type[safe.Alphanumeric], bad: str):
    with pytest.raises(ValueError):
        cls("abc" + bad + "def")


@pytest.mark.parametrize("cls", [safe.Alphanumeric, safe.ProjectKey, safe.VersionKey, safe.ReleaseKey])
@pytest.mark.parametrize(
    "bad",
    [
        "hello_world",
        "hello world",
        "hello!",
        "hello@",
    ],
)
def test_safe_types_reject_invalid_characters(cls: type[safe.Alphanumeric], bad: str):
    with pytest.raises(ValueError):
        cls(bad)


@pytest.mark.parametrize("cls", [safe.Alphanumeric, safe.ProjectKey])
def test_safe_alpha_types_reject_valid_version(cls: type[safe.Alphanumeric]):
    with pytest.raises(ValueError):
        cls("0.1+def")


@pytest.mark.parametrize("cls", [safe.Alphanumeric, safe.ProjectKey, safe.VersionKey, safe.ReleaseKey])
def test_safe_types_accept_valid_alpha(cls: type[safe.Alphanumeric]):
    value = cls("abcdef")
    assert str(value) == "abcdef"


@pytest.mark.parametrize("cls", [safe.VersionKey, safe.ReleaseKey])
def test_safe_version_types_accept_valid_version(cls: type[safe.Alphanumeric]):
    value = cls("0.1+def")
    assert str(value) == "0.1+def"


def test_safe_version_types_reject_too_long():
    with pytest.raises(ValueError, match="at most"):
        safe.VersionKey("1" * (safe.MAX_VERSION_LENGTH + 1))
