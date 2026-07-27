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

# How sternly ATR views each category, so that min() finds the friendliest of a set and max() the sternest
_CATEGORY_SEVERITY: Final[dict[models.licenses.Category, int]] = {
    models.licenses.Category.A: 0,
    models.licenses.Category.B: 1,
    models.licenses.Category.X: 2,
}


def check(
    bom_value: Bom,
    include_all: bool = False,
    is_source_release: bool = False,
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

            if isinstance(license_choice, LicenseExpression):
                try:
                    atoms = license_expression_atoms(license_expr)
                except ValueError:
                    atoms = {license_expr}
            else:
                atoms = {license_expr}

            categories = [_atom_category(atom) for atom in atoms]
            # "A OR B" lets the component be used under either half, so the friendliest half decides.
            # Anything else - an AND, parentheses, or a licence standing on its own - all applies at
            # once, so there the sternest half wins
            if isinstance(license_choice, LicenseExpression) and _is_simple_disjunction(license_expr):
                category = min(categories, key=_category_severity)
            else:
                category = max(categories, key=_category_severity)

            # A licence ATR cannot place reads as None here, and that is as concerning as Category X
            issue = models.licenses.Issue(
                component_name=name,
                component_version=version,
                license_expression=license_expr,
                category=category if (category is not None) else models.licenses.Category.X,
                any_unknown=(category is None),
                scope=scope,
                component_type=type,
            )
            if category == models.licenses.Category.A:
                if include_all:
                    good.append(issue)
            elif category == models.licenses.Category.B:
                # Category B may ship in a binary but not in source, so a source release makes it an error
                (errors if is_source_release else warnings).append(issue)
            else:
                errors.append(issue)

    return good, warnings, errors


def expression(license_choice: License) -> str | None:
    # A licence is either an SPDX expression or a single licence, and a single licence carries a
    # name of its own where it has no SPDX identifier to give
    if isinstance(license_choice, LicenseExpression):
        return license_choice.value
    if isinstance(license_choice, DisjunctiveLicense):
        return license_choice.id or license_choice.name
    return None


def _atom_category(atom: str) -> models.licenses.Category | None:
    # None where ATR does not recognise the licence at all, which the caller treats as sternly as X
    folded = _folded_atom(atom)
    if folded in constants.licenses.CATEGORY_A_LICENSES_FOLD:
        return models.licenses.Category.A
    if folded in constants.licenses.CATEGORY_B_LICENSES_FOLD:
        return models.licenses.Category.B
    if folded in constants.licenses.CATEGORY_X_LICENSES_FOLD:
        return models.licenses.Category.X
    return None


def _category_severity(category: models.licenses.Category | None) -> int:
    # A is the easiest to live with and an unrecognised licence the sternest, so min() picks the
    # friendliest category of a set and max() the sternest
    return 3 if (category is None) else _CATEGORY_SEVERITY[category]


def _folded_atom(atom: str) -> str:
    resolved = _NAME_TO_ID.get(_normalised_name(atom))
    return (resolved if (resolved is not None) else atom).casefold()


def _is_simple_disjunction(expr: str) -> bool:
    # SPDX writes its operators in capitals, so a plain "A OR B" is easy to spot. Parentheses or an
    # AND could hide a licence that all applies at once, so we relax only the unambiguous shape
    return (" OR " in expr) and (" AND " not in expr) and ("(" not in expr)


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
