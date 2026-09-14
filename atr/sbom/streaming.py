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


import dataclasses
import decimal
import pathlib
from typing import Any, BinaryIO, Final

import aiohttp
import blake3
import ijson
import packageurl

import atr.sbom.maintenance as maintenance

MAX_COMPONENTS: Final = 1000000
MAX_PACKAGES: Final = 10000
MAX_RESPONSE_BYTES: Final = 64 * 1024 * 1024
MAX_SBOM_BYTES: Final = 256 * 1024 * 1024
_MAX_DEPTH: Final = 64
_MAX_FIELD: Final = 4096
_MAX_LICENSES: Final = 100
_MAX_MEMBERS: Final = 10000
_MAX_MEMBER_CHARS: Final = 1024 * 1024
_MAX_STRING: Final = 1024 * 1024
_SCALARS: Final = frozenset({"null", "boolean", "number", "string"})
_COMPONENT_FIELDS: Final = frozenset({"name", "purl", "type", "version"})
_LICENSE_EVENTS: Final[dict[tuple[str | None, ...], frozenset[str]]] = {
    ("licenses",): frozenset({"start_array", "end_array"}),
    ("licenses", None): frozenset({"start_map", "end_map"}),
    ("licenses", None, "expression"): _SCALARS,
    ("licenses", None, "license"): frozenset({"start_map", "end_map"}),
    ("licenses", None, "license", "id"): _SCALARS,
    ("licenses", None, "license", "name"): _SCALARS,
}

REPOSITORY_FIELDS: Final = frozenset(
    {
        ("archived",),
        ("commit_stats", "dds"),
        *(("metadata", "files", name) for name in ("security", "code_of_conduct", "contributing")),
    }
)
ISSUE_FIELDS: Final = frozenset({("active_maintainers",)})
PACKAGE_FIELDS: Final = frozenset(
    {
        ("purl",),
        ("repository_url",),
        ("latest_release_published_at",),
        ("latest_release_number",),
        ("rankings", "average"),
    }
    | {("repo_metadata", *path) for path in REPOSITORY_FIELDS}
    | {("issue_metadata", *path) for path in ISSUE_FIELDS}
)

type JsonPath = tuple[str | None, ...]


class Cursor:
    def __init__(self) -> None:
        self.stack: list[Frame] = []

    def advance(self, event: str, value: Any) -> JsonPath:
        if isinstance(value, str) and (len(value) > _MAX_STRING):
            raise LimitError("JSON string exceeds the analysis limit")
        if event == "map_key":
            return self.stack[-1].member(value)
        if event in {"end_map", "end_array"}:
            return self.stack.pop().path
        path: JsonPath = ()
        if self.stack:
            frame = self.stack[-1]
            path = (*frame.path, None if frame.array else frame.key)
        if event in {"start_map", "start_array"}:
            if len(self.stack) >= _MAX_DEPTH:
                raise LimitError("JSON nesting exceeds the analysis limit")
            self.stack.append(Frame(path, event == "start_array"))
        return path


class FileReader:
    def __init__(self, source: BinaryIO):
        self.source = source
        self.size = 0
        self.hash = blake3.blake3()

    def read(self, size: int) -> bytes:
        first = self.size == 0
        content = self.source.read(min(size, MAX_SBOM_BYTES - self.size + 1))
        self.size += len(content)
        if self.size > MAX_SBOM_BYTES:
            raise LimitError("SBOM exceeds the 256 MiB analysis limit")
        self.hash.update(content)
        return content.removeprefix(b"\xef\xbb\xbf") if first else content


@dataclasses.dataclass
class Frame:
    path: JsonPath
    array: bool
    key: str | None = None
    members: set[str] = dataclasses.field(default_factory=set)
    size: int = 0

    def member(self, name: str) -> JsonPath:
        if name in self.members:
            raise MalformedError(f"Duplicate JSON object member name {name[:80]!r}")
        self.members.add(name)
        self.size += len(name)
        if (len(self.members) > _MAX_MEMBERS) or (self.size > _MAX_MEMBER_CHARS):
            raise LimitError("JSON object member names exceed the analysis limit")
        self.key = name
        return self.path


@dataclasses.dataclass
class Inventory:
    components: int = 0
    files: int = 0
    unidentified: int = 0
    purls: list[str] = dataclasses.field(default_factory=list)
    licensed_components: list["LicensedComponent"] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class LicensedComponent:
    name: str
    version: str | None
    purl: str | None
    licenses: list[tuple[str, bool]]


class LimitError(Exception):
    pass


class MalformedError(Exception):
    pass


class Projection:
    def __init__(self, fields: frozenset[JsonPath]):
        self.fields = fields
        self.paths = {path[:length] for path in fields for length in range(len(path) + 1)}
        self.objects = self.paths - fields - {()}
        self.record: dict[str, Any] = {}

    def feed(self, path: JsonPath, event: str, value: Any) -> None:
        if event == "map_key":
            if path in self.paths:
                self._set((*path, "_observed"), True)
            return
        if path and (path[-1] is None) and (path[:-1] in self.fields):
            if event in {"start_map", "start_array", *_SCALARS}:
                self._placeholder(path[:-1])
            return
        if (path not in self.paths) or (event in {"end_map", "end_array"}):
            return
        if (path in self.objects) and (event not in {"start_map", "null"}):
            raise MalformedError(f"Provider member {path[-1]!r} must be an object")
        if isinstance(value, str) and (len(value) > _MAX_FIELD):
            raise LimitError("Provider field exceeds the analysis limit")
        match event:
            case "start_map":
                value = {}
            case "start_array":
                value = []
        self._set(path, float(value) if isinstance(value, decimal.Decimal) else value)

    def _get(self, path: JsonPath) -> Any:
        value: Any = self.record
        for key in path:
            value = value[key]
        return value

    def _placeholder(self, path: JsonPath) -> None:
        values = self._get(path)
        if len(values) >= MAX_PACKAGES:
            raise LimitError("Provider array exceeds the analysis limit")
        values.append(None)

    def _set(self, path: JsonPath, value: Any) -> None:
        if not path:
            self.record = value
            return
        parent = self._get(path[:-1])
        parent[path[-1]] = value


class ResponseReader:
    def __init__(self, source: aiohttp.StreamReader):
        self.source = source
        self.size = 0

    async def read(self, size: int) -> bytes:
        content = await self.source.read(min(size, MAX_RESPONSE_BYTES - self.size + 1))
        self.size += len(content)
        if self.size > MAX_RESPONSE_BYTES:
            raise LimitError("Provider response exceeds the 64 MiB analysis limit")
        return content


class UnsupportedError(Exception):
    pass


@dataclasses.dataclass
class Walk:
    cursor: Cursor = dataclasses.field(default_factory=Cursor)
    inventory: Inventory = dataclasses.field(default_factory=Inventory)
    pending: dict[JsonPath, dict[str, Any]] = dataclasses.field(default_factory=dict)
    purls: set[str] = dataclasses.field(default_factory=set)
    declared: dict[str, Any] = dataclasses.field(default_factory=dict)
    licensed: dict[tuple[str | None, str, str | None], LicensedComponent] = dataclasses.field(default_factory=dict)
    licenses: int = 0

    def feed(self, event: str, value: Any) -> None:
        position = self.cursor.advance(event, value)
        if event == "map_key":
            return
        if not position:
            if event not in {"start_map", "end_map"}:
                raise MalformedError("Expected a JSON object")
            return
        self._value(position, event, value)

    def finish(self) -> Inventory:
        if self.declared.get("bomFormat") != "CycloneDX":
            raise MalformedError("Missing CycloneDX bomFormat")
        version = self.declared.get("specVersion")
        if (not isinstance(version, str)) or (not version):
            raise MalformedError("Missing CycloneDX specVersion")
        self.inventory.purls = sorted(self.purls)
        self.inventory.licensed_components = list(self.licensed.values())
        return self.inventory

    def _choice(self, component: dict[str, Any], event: str) -> None:
        if event == "start_map":
            component["choice"] = {}
            return
        choice = component.pop("choice")
        if ("license" in choice) == ("expression" in choice):
            raise MalformedError("CycloneDX license choice must be a license or an expression")
        text = choice.get("expression") or choice.get("id") or choice.get("name")
        declaration = (text, "expression" in choice)
        declarations = component.setdefault("licenses", [])
        if (not text) or (declaration in declarations):
            return
        declarations.append(declaration)
        if len(declarations) > _MAX_LICENSES:
            raise LimitError("Component exceeds the 100 license declaration analysis limit")

    def _component(self, position: JsonPath, event: str) -> None:
        if event == "start_map":
            self.pending[position] = {}
        elif event == "end_map":
            self._record(self.pending.pop(position))
        else:
            raise MalformedError("CycloneDX components must be objects")

    def _declare(self, name: str | None, value: Any) -> None:
        if (name == "spdxVersion") and isinstance(value, str) and value:
            raise UnsupportedError("SPDX JSON is not a CycloneDX SBOM")
        if (name == "bomFormat") and isinstance(value, str) and (value != "CycloneDX"):
            raise UnsupportedError(f"Unsupported bomFormat {value[:80]!r}")
        if name in {"bomFormat", "specVersion"}:
            self.declared[name] = value

    def _license(self, component: dict[str, Any], suffix: JsonPath, event: str, value: Any) -> None:
        if event not in _LICENSE_EVENTS[suffix]:
            raise MalformedError("CycloneDX component licenses must be an array of license or expression objects")
        if len(suffix) == 2:
            self._choice(component, event)
        elif event == "start_map":
            component["choice"]["license"] = True
        elif event in _SCALARS:
            component["choice"][suffix[-1]] = _text(value)

    def _record(self, component: dict[str, Any]) -> None:
        self.inventory.components += 1
        if self.inventory.components > MAX_COMPONENTS:
            raise LimitError("SBOM exceeds the component analysis limit")
        if component.get("type") == "file":
            self.inventory.files += 1
            return
        purl = maintenance.package_url(component.get("purl"))
        self._retain(component, purl)
        if purl is None:
            self.inventory.unidentified += 1
            return
        if len(str(purl)) > _MAX_FIELD:
            raise LimitError("Package identity exceeds the analysis limit")
        self.purls.add(str(purl))
        if len(self.purls) > MAX_PACKAGES:
            raise LimitError("SBOM exceeds the 10,000 package identity analysis limit")

    def _retain(self, component: dict[str, Any], purl: packageurl.PackageURL | None) -> None:
        declarations = component.get("licenses")
        if not declarations:
            return
        identity = str(purl) if purl else None
        name = _text(component.get("name")) or (purl.name if purl else None) or "Unnamed component"
        version = _text(component.get("version")) or (purl.version if purl else None)
        retained = self.licensed.get((identity, name, version))
        if retained is None:
            retained = self.licensed[identity, name, version] = LicensedComponent(name, version, identity, [])
            if len(self.licensed) > MAX_PACKAGES:
                raise LimitError("SBOM exceeds the 10,000 licensed component analysis limit")
        new = [item for item in declarations if item not in retained.licenses]
        self.licenses += len(new)
        if self.licenses > MAX_PACKAGES:
            raise LimitError("SBOM exceeds the 10,000 license declaration analysis limit")
        retained.licenses.extend(new)

    def _value(self, position: JsonPath, event: str, value: Any) -> None:
        if _component_array(position):
            if event not in {"start_array", "end_array"}:
                raise MalformedError("CycloneDX components must be an array")
        elif _component(position):
            self._component(position, event)
        elif (suffix := _license_suffix(position, self.pending)) is not None:
            self._license(self.pending[position[: -len(suffix)]], suffix, event, value)
        elif event not in _SCALARS:
            return
        elif len(position) == 1:
            self._declare(position[0], value)
        elif (position[:-1] in self.pending) and (position[-1] in _COMPONENT_FIELDS):
            self.pending[position[:-1]][position[-1]] = value


async def project(source: aiohttp.StreamReader, fields: frozenset[JsonPath]) -> dict[str, Any]:
    records = await _project(source, fields, multiple=False)
    return records[0]


async def project_records(source: aiohttp.StreamReader, fields: frozenset[JsonPath]) -> list[dict[str, Any]]:
    return await _project(source, fields, multiple=True)


def sbom(path: pathlib.Path) -> tuple[Inventory, str]:
    walk = Walk()
    with path.open("rb") as source:
        reader = FileReader(source)
        try:
            for event, value in ijson.basic_parse(reader):
                walk.feed(event, value)
        except ijson.JSONError as error:
            raise MalformedError(str(error).splitlines()[0]) from error
    return walk.finish(), f"blake3:{reader.hash.hexdigest()}"


def _boundary(event: str, projection: Projection, records: list[dict[str, Any]]) -> None:
    if event == "end_map":
        records.append(projection.record)
        if len(records) > MAX_PACKAGES:
            raise LimitError("Provider response exceeds the package limit")
    elif event not in {"start_map", "map_key"}:
        raise MalformedError("Invalid provider record")


def _component(path: JsonPath) -> bool:
    return (
        (len(path) % 2 == 0)
        and bool(path)
        and all((part == "components") if (index % 2 == 0) else (part is None) for index, part in enumerate(path))
    )


def _component_array(path: JsonPath) -> bool:
    return bool(path) and (path[-1] == "components") and ((len(path) == 1) or _component(path[:-1]))


def _license_suffix(path: JsonPath, pending: dict[JsonPath, dict[str, Any]]) -> JsonPath | None:
    for length in range(1, 5):
        if (path[-length:] in _LICENSE_EVENTS) and (path[:-length] in pending):
            return path[-length:]
    return None


async def _project(
    source: aiohttp.StreamReader, fields: frozenset[JsonPath], *, multiple: bool
) -> list[dict[str, Any]]:
    cursor = Cursor()
    projection = Projection(fields)
    records: list[dict[str, Any]] = []
    container = {"start_array", "end_array"} if multiple else {"start_map", "end_map", "map_key"}
    try:
        async for event, value in ijson.basic_parse_async(ResponseReader(source)):
            path = cursor.advance(event, value)
            if not path:
                if event not in container:
                    raise MalformedError("Invalid provider response")
                if multiple:
                    continue
            if multiple and (path == (None,)):
                _boundary(event, projection, records)
            projection.feed(path[1:] if multiple else path, event, value)
    except ijson.JSONError as error:
        raise MalformedError(str(error).splitlines()[0]) from error
    return records if multiple else [projection.record]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedError("CycloneDX component text must be a string")
    if len(value) > _MAX_FIELD:
        raise LimitError("CycloneDX component text exceeds the analysis limit")
    return value
