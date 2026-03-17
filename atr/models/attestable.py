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

from typing import Annotated, Any, Literal

import pydantic

from . import schema


class HashEntry(schema.Strict):
    size: int
    uploaders: list[Annotated[tuple[str, str], pydantic.BeforeValidator(tuple)]]
    basenames: list[str] = schema.factory(list)


class AttestableChecksV1(schema.Strict):
    version: Literal[1] = 1
    checks: list[int] = schema.factory(list)


class AttestableChecksV2(schema.Strict):
    version: Literal[2] = 2
    checks: dict[str, dict[str, str]] = schema.factory(dict)


class AttestableV1(schema.Strict):
    version: Literal[1] = 1
    paths: dict[str, str] = schema.factory(dict)
    hashes: dict[str, HashEntry] = schema.factory(dict)
    policy: dict[str, Any] = schema.factory(dict)


class PathEntryV2(schema.Strict):
    content_hash: str
    classification: str


class AttestableV2(schema.Strict):
    version: Literal[2] = 2
    hashes: dict[str, HashEntry] = schema.factory(dict)
    paths: dict[str, PathEntryV2] = schema.factory(dict)
    policy: dict[str, Any] = schema.factory(dict)


type Attestable = AttestableV1 | AttestableV2
