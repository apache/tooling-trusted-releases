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

import pytest

import atr.models.distribution as models_distribution
import atr.models.sql as sql
import atr.shared.distribution as distribution


def _data(platform: sql.DistributionPlatform) -> models_distribution.Data:
    return models_distribution.Data.model_validate(
        {"platform": platform, "package": "mypackage", "version": "1.0.0", "details": False}
    )


def test_template_url_rejects_non_stageable_platform_for_staging() -> None:
    with pytest.raises(distribution.PlatformNotStageableError):
        distribution._template_url(_data(sql.DistributionPlatform.DOCKER_HUB), staging=True)


def test_template_url_returns_staging_url_for_stageable_platform() -> None:
    url = distribution._template_url(_data(sql.DistributionPlatform.PYPI), staging=True)
    assert isinstance(url, str)


def test_template_url_allows_any_platform_for_production() -> None:
    url = distribution._template_url(_data(sql.DistributionPlatform.DOCKER_HUB), staging=False)
    assert isinstance(url, str)


def test_platform_not_stageable_error_is_a_distribution_error() -> None:
    assert issubclass(distribution.PlatformNotStageableError, distribution.DistributionError)
