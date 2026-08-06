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

import pydantic

# pydantic resolves this annotation at runtime, so the import has to stay out of a type-checking block
from . import licenses  # noqa: TC001
from .base import Strict


class Item(Strict):
    name: str
    version: str | None = None
    license_choices: list[licenses.Choice] = pydantic.Field(default_factory=list)
    purl: str | None = None


class Group(Strict):
    component_type: str
    items: list[Item] = pydantic.Field(default_factory=list)


class Breakdown(Strict):
    # The component the BOM is about, from metadata.component. Absent in a BOM that omits it,
    # and it is not counted in the groups
    subject: Item | None = None
    groups: list[Group] = pydantic.Field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(group.items) for group in self.groups)
