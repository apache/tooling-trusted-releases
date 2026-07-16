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
