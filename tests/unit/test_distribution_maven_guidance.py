#
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
#

from atr.models import distribution, sql
from atr.shared import distribution as shared_distribution
from atr.storage.writers import distributions


def test_distribution_forms_explain_maven_group_id():
    for form_cls in (
        shared_distribution.DistributionAutomateForm,
        shared_distribution.DistributionRecordForm,
    ):
        documentation = form_cls.model_fields["owner_namespace"].json_schema_extra["documentation"]

        assert "Maven Central" in documentation
        assert "groupId" in documentation
        assert "on search.maven.org." in documentation


def test_maven_api_error_mentions_group_id():
    data = distribution.Data(
        platform=sql.DistributionPlatform.MAVEN,
        owner_namespace="org.apache.maven",
        package="maven",
        version="1.0.0",
        details=False,
    )

    error = distributions._distribution_api_error(data, RuntimeError("not found"))

    assert error.status == 502
    assert "Maven groupId" in str(error)
    assert "on https://search.maven.org." in str(error)
