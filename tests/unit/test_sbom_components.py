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

import atr.sbom.components as components
import tests.unit.sboms as sboms


def test_breakdown() -> None:
    breakdown = components.breakdown(
        sboms.build(
            {
                "metadata": {"component": {"type": "application", "name": "subject", "version": "9.9"}},
                "components": [
                    {"type": "library", "name": "beta", "version": "2.0"},
                    {"type": "application", "name": "gamma", "version": "3.0"},
                    {
                        "type": "library",
                        "name": "alpha",
                        "version": "1.0",
                        "licenses": [{"license": {"id": "Apache-2.0"}}],
                    },
                ],
            }
        )
    )

    assert breakdown.subject is not None
    assert (breakdown.subject.name, breakdown.subject.version) == ("subject", "9.9")
    # The subject is not one of the components it describes
    assert breakdown.total == 3
    assert [(group.component_type, [item.name for item in group.items]) for group in breakdown.groups] == [
        ("application", ["gamma"]),
        ("library", ["alpha", "beta"]),
    ]
    assert breakdown.groups[1].items[0].licenses == ["Apache-2.0"]

    # A BOM need not say what it is about
    assert components.breakdown(sboms.with_components({"type": "library", "name": "alpha"})).subject is None
