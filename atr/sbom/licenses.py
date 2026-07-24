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

import re
from typing import TYPE_CHECKING, Final

from cyclonedx.model.license import DisjunctiveLicense, License, LicenseExpression

if TYPE_CHECKING:
    from cyclonedx.model.bom import Bom
    from cyclonedx.model.component import Component

from . import constants, models
from .spdx import license_expression_atoms

# Words that carry no distinguishing meaning, so that "Apache License, Version 2.0" and the SPDX
# name "Apache License 2.0" reduce to the same thing
_NAME_NOISE: Final[frozenset[str]] = frozenset({"the", "license", "licence", "licenses", "licences", "version", "v"})
_NAME_SPLIT: Final[re.Pattern[str]] = re.compile(r"[^0-9a-z]+")
_NAME_VERSION: Final[re.Pattern[str]] = re.compile(r"^v(\d.*)$")


def check(
    bom_value: Bom,
    include_all: bool = False,
) -> tuple[list[models.licenses.Issue], list[models.licenses.Issue], list[models.licenses.Issue]]:
    warnings: list[models.licenses.Issue] = []
    errors: list[models.licenses.Issue] = []
    good: list[models.licenses.Issue] = []

    components: list[Component] = list(bom_value.components)
    if bom_value.metadata and bom_value.metadata.component:
        components = [bom_value.metadata.component, *components]

    for component in components:
        name = component.name or "unknown"
        version = component.version
        scope = component.scope
        type = component.type

        if not component.licenses:
            continue

        for license_choice in component.licenses:
            license_expr = expression(license_choice)
            if not license_expr:
                continue

            parse_failed = False
            if isinstance(license_choice, LicenseExpression):
                try:
                    atoms = license_expression_atoms(license_expr)
                except ValueError:
                    parse_failed = True
                    atoms = {license_expr}
            else:
                atoms = {license_expr}
            got_warning = False
            got_error = False
            any_unknown = parse_failed
            for atom in atoms:
                folded = _folded_atom(atom)
                if folded in constants.licenses.CATEGORY_A_LICENSES_FOLD:
                    continue
                if folded in constants.licenses.CATEGORY_B_LICENSES_FOLD:
                    got_warning = True
                    continue
                if folded in constants.licenses.CATEGORY_X_LICENSES_FOLD:
                    got_error = True
                    continue
                got_error = True
                any_unknown = True
            if got_error:
                errors.append(
                    models.licenses.Issue(
                        component_name=name,
                        component_version=version,
                        license_expression=license_expr,
                        category=models.licenses.Category.X,
                        any_unknown=any_unknown,
                        scope=scope,
                        component_type=type,
                    )
                )
            elif got_warning:
                warnings.append(
                    models.licenses.Issue(
                        component_name=name,
                        component_version=version,
                        license_expression=license_expr,
                        category=models.licenses.Category.B,
                        any_unknown=False,
                        scope=scope,
                        component_type=type,
                    )
                )
            elif include_all:
                good.append(
                    models.licenses.Issue(
                        component_name=name,
                        component_version=version,
                        license_expression=license_expr,
                        category=models.licenses.Category.A,
                        any_unknown=False,
                        scope=scope,
                        component_type=type,
                    )
                )

    return good, warnings, errors


def expression(license_choice: License) -> str | None:
    # A licence is either an SPDX expression or a single licence, and a single licence carries a
    # name of its own where it has no SPDX identifier to give
    if isinstance(license_choice, LicenseExpression):
        return license_choice.value
    if isinstance(license_choice, DisjunctiveLicense):
        return license_choice.id or license_choice.name
    return None


def _folded_atom(atom: str) -> str:
    resolved = _NAME_TO_ID.get(_normalised_name(atom))
    return (resolved if (resolved is not None) else atom).casefold()


def _name_to_id() -> dict[str, str]:
    index: dict[str, str] = {}
    for license_id, name in constants.licenses.LICENSE_NAMES.items():
        index[_normalised_name(name)] = license_id
    for license_ids in constants.licenses.LICENSES.values():
        for license_id in license_ids:
            index.setdefault(_normalised_name(license_id), license_id)
    return index


def _normalised_name(text: str) -> str:
    words = []
    for word in _NAME_SPLIT.split(text.casefold()):
        if (not word) or (word in _NAME_NOISE):
            continue
        version = _NAME_VERSION.match(word)
        words.append(version.group(1) if version else word)
    return " ".join(words)


_NAME_TO_ID: Final[dict[str, str]] = _name_to_id()
