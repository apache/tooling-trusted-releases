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

import atr.models.distribution as distribution
import atr.models.safe as safe
import atr.models.sql as sql
import atr.shared.distribution as shared_distribution


def _data(platform: sql.DistributionPlatform, *, owner_namespace: str | None = None) -> distribution.Data:
    return distribution.Data(
        platform=platform,
        owner_namespace=safe.Alphanumeric(owner_namespace) if owner_namespace else None,
        package=safe.Alphanumeric("example"),
        version=safe.VersionKey("1.0.0"),
        details=False,
    )


# --- _template_url: enum is now the single source of truth ---


@pytest.mark.parametrize(
    "platform, owner_namespace",
    [
        (sql.DistributionPlatform.ARTIFACT_HUB, "asfowner"),
        (sql.DistributionPlatform.MAVEN, "orgapacheexample"),
        (sql.DistributionPlatform.PYPI, None),
    ],
)
def test_template_url_returns_staging_url_when_declared(
    platform: sql.DistributionPlatform, owner_namespace: str | None
) -> None:
    dd = _data(platform, owner_namespace=owner_namespace)
    template_url = shared_distribution._template_url(dd, staging=True)
    assert template_url == platform.value.template_staging_url
    assert template_url is not None


@pytest.mark.parametrize(
    "platform",
    [
        sql.DistributionPlatform.DOCKER_HUB,
        sql.DistributionPlatform.NPM,
        sql.DistributionPlatform.NPM_SCOPED,
    ],
)
def test_template_url_raises_when_platform_has_no_staging_url(platform: sql.DistributionPlatform) -> None:
    dd = _data(platform, owner_namespace="example")
    with pytest.raises(RuntimeError) as excinfo:
        shared_distribution._template_url(dd, staging=True)
    # The improved error message names the offending platform so adding a
    # new platform without a staging URL is debuggable. See issue #751.
    assert platform.value.name in str(excinfo.value)


def test_template_url_does_not_use_a_hardcoded_whitelist() -> None:
    # Regression test for the issue identified in #751: when a platform's
    # enum value declares a template_staging_url, _template_url must return
    # it without requiring a parallel allowlist to be edited in lockstep.
    for platform in sql.DistributionPlatform:
        if platform.value.template_staging_url is None:
            continue
        dd = _data(platform, owner_namespace=platform.value.default_owner_namespace or "example")
        assert shared_distribution._template_url(dd, staging=True) == platform.value.template_staging_url


def test_template_url_returns_production_url_when_staging_false() -> None:
    for platform in sql.DistributionPlatform:
        dd = _data(platform, owner_namespace=platform.value.default_owner_namespace or "example")
        assert shared_distribution._template_url(dd, staging=False) == platform.value.template_url


# --- Distribution.staging_revision_key column ---


def test_distribution_staging_revision_key_defaults_to_none() -> None:
    dist = sql.Distribution(
        release_key="example-1.0.0",
        platform=sql.DistributionPlatform.PYPI,
        owner_namespace="",
        package="example",
        version="1.0.0",
    )
    assert dist.staging_revision_key is None


def test_distribution_accepts_staging_revision_key() -> None:
    dist = sql.Distribution(
        release_key="example-1.0.0",
        platform=sql.DistributionPlatform.PYPI,
        owner_namespace="",
        package="example",
        version="1.0.0",
        staging=True,
        staging_revision_key="example-1.0.0 00002",
    )
    assert dist.staging_revision_key == "example-1.0.0 00002"
