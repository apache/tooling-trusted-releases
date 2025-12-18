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

from typing import Any

import pydantic

from .base import Lax


class QueryResult(Lax):
    vulns: list[VulnerabilityDetails] | None = None
    next_page_token: str | None = None


class ComponentVulnerabilities(Lax):
    ref: str
    vulnerabilities: list[VulnerabilityDetails]


class VulnerabilityDetails(Lax):
    id: str
    summary: str | None = None
    details: str | None = None
    references: list[dict[str, Any]] | None = None
    severity: list[dict[str, Any]] | None = None
    published: str | None = None
    modified: str
    database_specific: dict[str, Any] = pydantic.Field(default={})


class CdxVulnerabilityDetail(Lax):
    bom_ref: str | None = pydantic.Field(default=None, alias="bom-ref")
    id: str
    source: dict[str, str] | None = None
    description: str | None = None
    detail: str | None = None
    advisories: list[dict[str, str]] | None = None
    cwes: list[int] | None = None
    published: str | None = None
    updated: str | None = None
    affects: list[dict[str, str]] | None = None
    ratings: list[dict[str, str | float]] | None = None


CdxVulnAdapter = pydantic.TypeAdapter(CdxVulnerabilityDetail)
