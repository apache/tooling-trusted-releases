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

import enum
from typing import Annotated, Any, Literal

import pydantic

from . import schema

# We bump versions of all "trees" of classes together
# We only bump when a field type changes, or a field is removed
# We don't bump when a field is added


# Attestable, v1


class HashEntryV1(schema.Strict):
    size: int
    uploaders: list[Annotated[tuple[str, str], pydantic.BeforeValidator(tuple)]]
    basenames: list[str] = schema.factory(list)


class AttestableV1(schema.Strict):
    version: Literal[1] = 1
    paths: dict[str, str] = schema.factory(dict)
    hashes: dict[str, HashEntryV1] = schema.factory(dict)
    policy: dict[str, Any] = schema.factory(dict)


# Attestable, v2


class HashEntryV2(schema.Strict):
    size: int
    uploaders: list[Annotated[tuple[str, str], pydantic.BeforeValidator(tuple)]]
    basenames: list[str] = schema.factory(list)
    swhid_dir_inner: str | None = None


class GeneratorV2(enum.Enum):
    SBOM_FROM_ARTIFACT = "SBOM_from_artifact"
    SHA512_FROM_CONTENT = "SHA512_from_content"
    SHA512_FROM_SIGNATURE = "SHA512_from_signature"


class ProvenanceV2(schema.Strict):
    generator: GeneratorV2
    metadata: dict[str, Any] = schema.factory(dict)


class PathEntryV2(schema.Strict):
    content_hash: str
    classification: str
    provenance: ProvenanceV2 | None = None


class AttestableV2(schema.Strict):
    version: Literal[2] = 2
    hashes: dict[str, HashEntryV2] = schema.factory(dict)
    paths: dict[str, PathEntryV2] = schema.factory(dict)
    policy: dict[str, Any] = schema.factory(dict)


# Attestable, any version


type Attestable = AttestableV1 | AttestableV2


# Attestable Checks, v1


class AttestableChecksV1(schema.Strict):
    version: Literal[1] = 1
    checks: list[int] = schema.factory(list)


# Attestable Checks, v2


class AttestableChecksV2(schema.Strict):
    version: Literal[2] = 2
    checks: dict[str, dict[str, str]] = schema.factory(dict)


# Attestable Checks, any version

type AttestableChecks = AttestableChecksV1 | AttestableChecksV2
