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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyclonedx.model.bom import Bom
    from cyclonedx.model.component import Component

from . import licenses, models


def breakdown(bom_value: Bom) -> models.components.Breakdown:
    grouped: dict[str, list[models.components.Item]] = {}
    for component in bom_value.components:
        grouped.setdefault(_type_name(component), []).append(_item(component))

    groups = [
        models.components.Group(component_type=name, items=sorted(items, key=_sort_key))
        for name, items in sorted(grouped.items())
    ]

    subject = None
    if bom_value.metadata and bom_value.metadata.component:
        subject = _item(bom_value.metadata.component)
    return models.components.Breakdown(subject=subject, groups=groups)


def _item(component: Component) -> models.components.Item:
    return models.components.Item(
        name=component.name or "unknown",
        version=component.version,
        licenses=_licenses(component),
        purl=str(component.purl) if component.purl else None,
    )


def _licenses(component: Component) -> list[str]:
    values = {expr for choice in component.licenses if (expr := licenses.expression(choice))}
    return sorted(values)


def _sort_key(item: models.components.Item) -> tuple[str, str]:
    return item.name.casefold(), item.version or ""


def _type_name(component: Component) -> str:
    return component.type.value
