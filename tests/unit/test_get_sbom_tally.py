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

import atr.get.sbom as sbom
import atr.sbom.models as models


def test_license_tally_counts_new_and_changed_within_each_category() -> None:
    old = [_issue("stable", "MIT", models.licenses.Category.B)]
    items = [
        _issue("fresh", "GPL-3.0-only", models.licenses.Category.X),
        _issue("stable", "Apache-2.0", models.licenses.Category.B),
    ]

    tally = {row[0]: row for row in sbom._license_tally(items, old)}

    # Category, count, new, changed
    assert tally[models.licenses.Category.X][:4] == (models.licenses.Category.X, 1, 1, 0)
    assert tally[models.licenses.Category.B][:4] == (models.licenses.Category.B, 1, 0, 1)


def test_license_tally_omits_comparison_counts_without_a_previous_release() -> None:
    items = [_issue("fresh", "GPL-3.0-only", models.licenses.Category.X)]

    (row,) = sbom._license_tally(items, None)

    assert row[2] is None
    assert row[3] is None


def _issue(name: str, expression: str, category: models.licenses.Category) -> models.licenses.Issue:
    return models.licenses.Issue(
        component_name=name,
        component_version="1.0",
        license_expression=expression,
        category=category,
    )
