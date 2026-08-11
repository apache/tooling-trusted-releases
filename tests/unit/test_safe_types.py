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
import pydantic
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


@pytest.mark.parametrize("value", ["abc1234", "0123456789abcdef", "a" * 7, "f" * 64])
def test_commit_hash_accepts_valid_hex(value: str):
    assert str(safe.CommitHash(value)) == value


@pytest.mark.parametrize("bad", ["abc", "abcdef", "a" * 65])
def test_commit_hash_rejects_bad_length(bad: str):
    with pytest.raises(ValueError, match="7 and 64"):
        safe.CommitHash(bad)


@pytest.mark.parametrize("bad", ["ghijklm", "xyz1234", "abc 123"])
def test_commit_hash_rejects_non_hex(bad: str):
    with pytest.raises(ValueError):
        safe.CommitHash(bad)


@pytest.mark.parametrize(("value", "expected"), [("ABC1234", "abc1234"), ("DeadBeef12", "deadbeef12")])
def test_optional_commit_hash_lowercases(value: str, expected: str):
    result = pydantic.TypeAdapter(safe.OptionalCommitHash).validate_python(value)
    assert str(result) == expected


@pytest.mark.parametrize("value", ["", "   "])
def test_optional_commit_hash_blank_is_none(value: str):
    assert pydantic.TypeAdapter(safe.OptionalCommitHash).validate_python(value) is None


@pytest.mark.parametrize("bad", ["abc", "nothex1", "a" * 65])
def test_optional_commit_hash_rejects_invalid(bad: str):
    with pytest.raises(ValueError):
        pydantic.TypeAdapter(safe.OptionalCommitHash).validate_python(bad)
