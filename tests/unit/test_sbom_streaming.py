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
import pathlib
import unittest.mock as mock
from typing import Any

import aiohttp
import blake3
import pytest

import atr.sbom.maintenance as maintenance
import atr.sbom.streaming as streaming

_CYCLONEDX = b'{"bomFormat": "CycloneDX", "specVersion": "1.6"'
_LICENSED = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.6",
    "metadata": {"component": {"type": "application", "name": "subject", "licenses": [{"expression": "GPL-3.0-only"}]}},
    "components": [
        {"type": "file", "name": "LICENSE", "licenses": [{"license": {"id": "MIT"}}]},
        {
            "type": "library",
            "name": "named",
            "version": "2",
            "licenses": [
                {"license": {"id": "MIT", "name": "The MIT License", "text": {"content": "x" * 5000}}},
                {"license": {"name": "Custom"}, "bom-ref": "custom", "acknowledgement": "declared"},
                {"expression": "MIT OR GPL-2.0-only"},
                {"license": {"id": "MIT"}},
                {"license": {"id": None, "name": ""}},
            ],
        },
        {
            "type": "library",
            "purl": "pkg:maven/org.example/lib@1.0",
            "licenses": [{"license": {"id": "Apache-2.0"}}],
            "components": [{"purl": "pkg:npm/%40scope/x@2", "licenses": [{"expression": "ISC"}]}],
        },
        {"type": "library", "purl": "pkg:maven/org.example/lib@1.0", "licenses": [{"license": {"name": "BSD"}}]},
        {"type": "library", "name": "bare", "licenses": []},
        {"licenses": [{"license": {"name": "Unlicense"}}]},
    ],
}
_LICENSED_COMPONENTS = [
    streaming.LicensedComponent("named", "2", None, [("MIT", False), ("Custom", False), ("MIT OR GPL-2.0-only", True)]),
    streaming.LicensedComponent("x", "2", "pkg:npm/%40scope/x@2", [("ISC", True)]),
    streaming.LicensedComponent("lib", "1.0", "pkg:maven/org.example/lib@1.0", [("Apache-2.0", False), ("BSD", False)]),
    streaming.LicensedComponent("Unnamed component", None, None, [("Unlicense", False)]),
]
_NESTED = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.6",
    "metadata": {"component": {"type": "application", "name": "subject"}},
    "spdxVersion": None,
    "components": [
        {"type": "file", "name": "README", "properties": [{"name": "k", "value": "1"}, {"name": "k", "value": "2"}]},
        {"type": "library", "name": "anonymous"},
        {"type": "library", "purl": "pkg:maven/org.example/lib@1.0", "components": [{"purl": "pkg:npm/%40scope/x@2"}]},
        {"type": "library", "purl": "pkg:maven/org.example/lib@1.0"},
    ],
}
_RECORD = {
    "purl": "pkg:npm/left-pad",
    "name": "left-pad",
    "latest_release_number": "1.3.0",
    "latest_release_published_at": "2024-01-01T00:00:00Z",
    "rankings": {"average": 12.5, "downloads": 1},
    "repository_url": "https://github.com/example/left-pad",
    "repo_metadata": {
        "archived": False,
        "commit_stats": {"dds": "0.7", "total": 9},
        "metadata": {"files": {"security": "SECURITY.md", "code_of_conduct": None, "contributing": "", "readme": "x"}},
        "stars": 3,
    },
    "issue_metadata": {"active_maintainers": [{"login": "a"}, {"login": "b"}, "c"]},
}
_PROJECTED = {
    "_observed": True,
    "purl": "pkg:npm/left-pad",
    "latest_release_number": "1.3.0",
    "latest_release_published_at": "2024-01-01T00:00:00Z",
    "rankings": {"_observed": True, "average": 12.5},
    "repository_url": "https://github.com/example/left-pad",
    "repo_metadata": {
        "_observed": True,
        "archived": False,
        "commit_stats": {"_observed": True, "dds": "0.7"},
        "metadata": {
            "_observed": True,
            "files": {"_observed": True, "security": "SECURITY.md", "code_of_conduct": None, "contributing": ""},
        },
    },
    "issue_metadata": {"_observed": True, "active_maintainers": [None, None, None]},
}


def _stream(data: bytes, error: Exception | None = None) -> aiohttp.StreamReader:
    reader = aiohttp.StreamReader(mock.Mock(), 2**16)
    reader.feed_data(data)
    if error is None:
        reader.feed_eof()
    else:
        reader.set_exception(error)
    return reader


def _write(tmp_path: pathlib.Path, data: bytes) -> pathlib.Path:
    path = tmp_path / "bom.cdx.json"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("name", "value", "data"),
    [
        ("MAX_RESPONSE_BYTES", 4, b'{"purl": "a"}'),
        ("MAX_PACKAGES", 2, b'{"issue_metadata": {"active_maintainers": [1, 2, 3]}}'),
        ("_MAX_FIELD", 2, b'{"purl": "abc", "other": "abcdef"}'),
    ],
)
async def test_project_limits(monkeypatch: pytest.MonkeyPatch, name: str, value: int, data: bytes) -> None:
    monkeypatch.setattr(streaming, name, value)
    with pytest.raises(streaming.LimitError):
        await streaming.project(_stream(data), streaming.PACKAGE_FIELDS)


async def test_project_record() -> None:
    record = await streaming.project(_stream(json.dumps(_RECORD).encode()), streaming.PACKAGE_FIELDS)
    assert record == _PROJECTED
    records = await streaming.project_records(
        _stream(b'[{"purl": "pkg:npm/a"}, {"repo_metadata": {}}, {}]'), {("purl",)}
    )
    assert records == [{"_observed": True, "purl": "pkg:npm/a"}, {"_observed": True}, {}]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"unused": 1},
        {"metadata": None, "active_maintainers": None},
        {"metadata": {}, "commit_stats": {"dds": None}},
        {"metadata": {"files": {}}},
        {"metadata": {"files": {"security": "S", "code_of_conduct": [], "contributing": {}}}},
        {"active_maintainers": [{"login": "a"}, [], 3]},
    ],
)
async def test_project_parity(payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode()
    repository = await streaming.project(_stream(data), streaming.REPOSITORY_FIELDS)
    assert maintenance.repository(repository) == maintenance.repository(payload)
    issues = await streaming.project(_stream(data), streaming.ISSUE_FIELDS)
    assert maintenance.issues(issues) == maintenance.issues(payload)


@pytest.mark.parametrize(
    ("data", "multiple", "error"),
    [
        (b"{}", True, streaming.MalformedError),
        (b"[]", False, streaming.MalformedError),
        (b"[1]", True, streaming.MalformedError),
        (b'[{"purl": "a", "purl": "a"}]', True, streaming.MalformedError),
        (b'[{"purl": "a"}', True, streaming.MalformedError),
        (b'{"purl": "a"} []', False, streaming.MalformedError),
        (b'{"repo_metadata": {"metadata": ["unexpected"]}}', False, streaming.MalformedError),
        (b'{"repo_metadata": {"metadata": {"files": ""}}}', False, streaming.MalformedError),
        (b'{"rankings": 0}', False, streaming.MalformedError),
        (b'[{"issue_metadata": false}]', True, streaming.MalformedError),
        (b'{"purl": "a"', False, aiohttp.ClientPayloadError),
    ],
)
async def test_project_rejections(data: bytes, multiple: bool, error: type[Exception]) -> None:
    transport = aiohttp.ClientPayloadError("cut") if (error is aiohttp.ClientPayloadError) else None
    call = streaming.project_records if multiple else streaming.project
    with pytest.raises(error):
        await call(_stream(data, transport), streaming.PACKAGE_FIELDS)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (_NESTED, streaming.Inventory(5, 1, 1, ["pkg:maven/org.example/lib@1.0", "pkg:npm/%40scope/x@2"])),
        ({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}, streaming.Inventory()),
        ({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [{"type": "file"}]}, streaming.Inventory(1, 1)),
        (
            _LICENSED,
            streaming.Inventory(
                7, 1, 3, ["pkg:maven/org.example/lib@1.0", "pkg:npm/%40scope/x@2"], _LICENSED_COMPONENTS
            ),
        ),
    ],
)
def test_sbom_inventory_and_hash(
    tmp_path: pathlib.Path, document: dict[str, Any], expected: streaming.Inventory
) -> None:
    data = b"\xef\xbb\xbf" + json.dumps(document).encode()
    inventory, digest = streaming.sbom(_write(tmp_path, data))
    assert inventory == expected
    assert digest == "blake3:" + blake3.blake3(data).hexdigest()


@pytest.mark.parametrize(
    ("name", "value", "data"),
    [
        ("MAX_SBOM_BYTES", 8, _CYCLONEDX + b"}"),
        ("_MAX_DEPTH", 2, _CYCLONEDX + b', "a": [[[]]]}'),
        ("_MAX_MEMBER_CHARS", 4, _CYCLONEDX + b', "abc": 1}'),
        ("_MAX_MEMBERS", 2, _CYCLONEDX + b', "a": 1}'),
        ("MAX_COMPONENTS", 1, _CYCLONEDX + b', "components": [{"type": "file"}, {"type": "file"}]}'),
        ("MAX_PACKAGES", 1, _CYCLONEDX + b', "components": [{"purl": "pkg:npm/a"}, {"purl": "pkg:npm/b"}]}'),
        ("_MAX_LICENSES", 1, _CYCLONEDX + b', "components":[{"licenses":[{"expression":"a"},{"expression":"b"}]}]}'),
        (
            "MAX_PACKAGES",
            1,
            _CYCLONEDX + b', "components": [{"name": "a", "licenses": [{"expression": "MIT"}]}, '
            b'{"name": "a", "licenses": [{"expression": "ISC"}]}]}',
        ),
        (
            "MAX_PACKAGES",
            1,
            _CYCLONEDX + b', "components": [{"name": "a", "licenses": [{"expression": "MIT"}]}, '
            b'{"name": "b", "licenses": [{"expression": "MIT"}]}]}',
        ),
        ("_MAX_FIELD", 2, _CYCLONEDX + b', "components": [{"licenses": [{"expression": "abc"}]}]}'),
        ("_MAX_FIELD", 2, _CYCLONEDX + b', "components": [{"name": "abc", "licenses": [{"expression": "a"}]}]}'),
        ("_MAX_STRING", 4, _CYCLONEDX + b', "x": "abcde"}'),
    ],
)
def test_sbom_limits(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, name: str, value: int, data: bytes
) -> None:
    monkeypatch.setattr(streaming, name, value)
    with pytest.raises(streaming.LimitError):
        streaming.sbom(_write(tmp_path, data))


@pytest.mark.parametrize(
    "data",
    [
        _CYCLONEDX + b", " + b'"components": [',
        _CYCLONEDX + b"} trailing",
        b"[]",
        b'"CycloneDX"',
        b"{}",
        b'{"bomFormat": 1, "specVersion": "1.6"}',
        b'{"bomFormat": {"CycloneDX": 1}, "specVersion": "1.6"}',
        b'{"bomFormat": "CycloneDX"}',
        b'{"bomFormat": "CycloneDX", "specVersion": 1.6}',
        b'{"bomFormat": "CycloneDX", "specVersion": ""}',
        _CYCLONEDX + b', "metadata": {"x": 1, "x": 1}}',
        _CYCLONEDX + b', "components": {}}',
        _CYCLONEDX + b', "components": [1]}',
        _CYCLONEDX + b', "components": [{"components": {}}]}',
        _CYCLONEDX + b', "components": [{"licenses": {}}]}',
        _CYCLONEDX + b', "components": [{"licenses": [1]}]}',
        _CYCLONEDX + b', "components": [{"licenses": [{}]}]}',
        _CYCLONEDX + b', "components": [{"licenses": [{"license": "MIT"}]}]}',
        _CYCLONEDX + b', "components": [{"licenses": [{"license": {"id": 1}}]}]}',
        _CYCLONEDX + b', "components": [{"licenses": [{"license": {"id": "MIT"}, "expression": "MIT"}]}]}',
        _CYCLONEDX + b', "components": [{"name": 1, "licenses": [{"expression": "MIT"}]}]}',
    ],
)
def test_sbom_malformed(tmp_path: pathlib.Path, data: bytes) -> None:
    with pytest.raises(streaming.MalformedError):
        streaming.sbom(_write(tmp_path, data))


def test_sbom_repeated_components_share_limits(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streaming, "MAX_PACKAGES", 1)
    component = {"purl": "pkg:npm/a", "licenses": [{"expression": "MIT"}]}
    document = {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [component] * 3}
    inventory, _ = streaming.sbom(_write(tmp_path, json.dumps(document).encode()))
    assert inventory.licensed_components == [streaming.LicensedComponent("a", None, "pkg:npm/a", [("MIT", True)])]


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b'{"bomFormat": "SPDX"}', streaming.UnsupportedError),
        (b'{"spdxVersion": "SPDX-2.3"}', streaming.UnsupportedError),
        (_CYCLONEDX + b', "spdxVersion": "SPDX-2.3"}', streaming.UnsupportedError),
    ],
)
def test_sbom_unsupported(tmp_path: pathlib.Path, data: bytes, error: type[Exception]) -> None:
    with pytest.raises(error):
        streaming.sbom(_write(tmp_path, data))
    with pytest.raises(OSError):
        streaming.sbom(tmp_path / "missing.json")
