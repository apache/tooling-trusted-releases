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

import atr.admin as admin
import atr.models.sql as sql
import atr.purl as purl


def test_distribution_import_split() -> None:
    expected = [
        sql.ExpectedDistribution(
            project_key="example",
            platform=sql.DistributionPlatform.MAVEN,
            owner_namespace="org.apache.example",
            package="example-core",
        ),
        # The same name on another platform is not the same package
        sql.ExpectedDistribution(
            project_key="example", platform=sql.DistributionPlatform.PYPI, owner_namespace="", package="example-api"
        ),
    ]
    pairs = [
        ("org.apache.example", "example-api"),
        ("org.apache.example", "example-core"),
        ("org.apache.example", "example_2.13"),
        ("org.apache.example", ".."),
    ]

    new_purls, already, unrecordable = admin._distribution_import_split("example", pairs, expected)

    assert new_purls == [
        "pkg:maven/org.apache.example/example-api@{{VERSION}}",
        "pkg:maven/org.apache.example/example_2.13@{{VERSION}}",
    ]
    assert already == ["pkg:maven/org.apache.example/example-core@{{VERSION}}"]
    assert len(unrecordable) == 1
    # What the preview offers is what the apply step reads back
    found = purl.expected_package(new_purls[0])
    assert (str(found.package), found.version_pattern) == ("example-api", "{{VERSION}}")
