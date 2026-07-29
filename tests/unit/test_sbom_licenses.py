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

import cyclonedx.model.license as cdx_license

import atr.sbom.constants as constants
import atr.sbom.licenses as licenses
import atr.sbom.models as models
import tests.unit.sboms as sboms


def test_expression() -> None:
    assert licenses.expression(cdx_license.LicenseExpression(value="Apache-2.0 OR MIT")) == "Apache-2.0 OR MIT"
    assert licenses.expression(cdx_license.DisjunctiveLicense(id="Apache-2.0")) == "Apache-2.0"
    # A licence with no SPDX identifier still carries a name of its own
    assert licenses.expression(cdx_license.DisjunctiveLicense(name="Some bespoke licence")) == "Some bespoke licence"


def test_check() -> None:
    good, warnings, errors = licenses.check(
        sboms.with_components(
            {"type": "library", "name": "permissive", "licenses": [{"license": {"id": "Apache-2.0"}}]},
            {"type": "library", "name": "forbidden", "licenses": [{"license": {"id": "GPL-3.0-only"}}]},
            {"type": "library", "name": "named", "licenses": [{"license": {"name": "Some bespoke licence"}}]},
            {"type": "library", "name": "unlicensed"},
        ),
        include_all=True,
    )

    assert [(issue.component_name, issue.category) for issue in good] == [
        ("permissive", models.licenses.Category.A),
    ]
    assert warnings == []
    # A licence ATR cannot place is worth a look, so it may not pass by unremarked
    assert sorted((issue.component_name, issue.category, issue.any_unknown) for issue in errors) == [
        ("forbidden", models.licenses.Category.X, False),
        ("named", models.licenses.Category.X, True),
    ]


def test_check_categorises_a_licence_named_in_full() -> None:
    # Components that give no SPDX identifier name their licence however their build file spelled
    # it, so the category must not depend on that spelling
    good, warnings, errors = licenses.check(
        sboms.with_components(
            {
                "type": "library",
                "name": "maven-style",
                "licenses": [{"license": {"name": "Apache License, Version 2.0"}}],
            },
            {"type": "library", "name": "spdx-style", "licenses": [{"license": {"name": "Apache License 2.0"}}]},
            {"type": "library", "name": "definite", "licenses": [{"license": {"name": "The MIT License"}}]},
            {
                "type": "library",
                "name": "abbreviated",
                "licenses": [{"license": {"name": "Eclipse Public License v2.0"}}],
            },
        ),
        include_all=True,
    )

    assert sorted(issue.component_name for issue in good) == ["definite", "maven-style", "spdx-style"]
    assert [issue.component_name for issue in warnings] == ["abbreviated"]
    assert [issue.category for issue in warnings] == [models.licenses.Category.B]
    assert errors == []


def test_check_categorises_a_public_domain_declaration_as_category_a() -> None:
    # ASF policy lets us include works in the public domain, and components declaring one tend to
    # name it rather than reach for an SPDX identifier
    good, warnings, errors = licenses.check(
        sboms.with_components(
            {"type": "library", "name": "named", "licenses": [{"license": {"name": "Public Domain"}}]},
            {"type": "library", "name": "hyphenated", "licenses": [{"license": {"name": "public-domain"}}]},
            {"type": "library", "name": "definite", "licenses": [{"license": {"name": "The Public Domain"}}]},
            {"type": "library", "name": "dedicated", "licenses": [{"license": {"id": "CC0-1.0"}}]},
        ),
        include_all=True,
    )

    assert sorted(issue.component_name for issue in good) == ["dedicated", "definite", "hyphenated", "named"]
    assert warnings == []
    assert errors == []


def test_check_a_disjunction_settles_on_the_friendliest_category() -> None:
    # A component offered under "A OR B" may be taken under whichever half is easier to live with
    good, warnings, errors = licenses.check(
        sboms.with_components(
            {"type": "library", "name": "dual", "licenses": [{"expression": "Apache-2.0 OR GPL-3.0-only"}]},
        ),
        include_all=True,
    )

    assert [issue.component_name for issue in good] == ["dual"]
    assert warnings == []
    assert errors == []


def test_check_a_conjunction_settles_on_the_sternest_category() -> None:
    # "A AND B" binds the component to both, so the sterner half decides
    _good, _warnings, errors = licenses.check(
        sboms.with_components(
            {"type": "library", "name": "both", "licenses": [{"expression": "Apache-2.0 AND GPL-3.0-only"}]},
        ),
    )

    assert [(issue.component_name, issue.category) for issue in errors] == [("both", models.licenses.Category.X)]


def test_check_category_b_is_an_error_in_a_source_release() -> None:
    # Category B may ship in a binary but not in source, so a source release turns the warning into an error
    _good, warnings, errors = licenses.check(
        sboms.with_components(
            {"type": "library", "name": "weak-copyleft", "licenses": [{"license": {"id": "EPL-2.0"}}]},
        ),
        is_source_release=True,
    )

    assert warnings == []
    assert [(issue.component_name, issue.category) for issue in errors] == [
        ("weak-copyleft", models.licenses.Category.B),
    ]


def test_check_reports_the_licence_as_the_component_declared_it() -> None:
    good, _warnings, _errors = licenses.check(
        sboms.with_components(
            {
                "type": "library",
                "name": "maven-style",
                "licenses": [{"license": {"name": "Apache License, Version 2.0"}}],
            },
        ),
        include_all=True,
    )

    assert [issue.license_expression for issue in good] == ["Apache License, Version 2.0"]


def test_license_names_resolve_to_exactly_one_identifier() -> None:
    names = [licenses._normalised_name(name) for name in constants.licenses.LICENSE_NAMES.values()]

    assert len(names) == len(set(names))
