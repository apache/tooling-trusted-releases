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

import datetime

import pytest

import atr.construct as construct
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation


def _release(*, override: str | None, policy: str, project_key: str, committee_key: str, version: str) -> sql.Release:
    release_policy = sql.ReleasePolicy(download_path_suffix=policy)
    project = sql.Project(key=project_key, committee_key=committee_key, release_policy=release_policy)
    return sql.Release(
        project_key=project_key,
        project=project,
        version=version,
        download_path_suffix=override,
        phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        created=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
    )


def test_empty_template_subproject_uses_key_and_version() -> None:
    result = construct.resolve_download_path_suffix(
        template="", project_key="tomcat-native", version="2.0.0", is_top_level=False
    )

    assert result == safe.RelPath("tomcat-native-2.0.0")


def test_empty_template_top_level_publishes_to_root() -> None:
    result = construct.resolve_download_path_suffix(
        template="", project_key="tomcat", version="10.1.0", is_top_level=True
    )

    assert result is None


def test_explicit_template_wins_for_top_level_project() -> None:
    result = construct.resolve_download_path_suffix(
        template="{{VERSION}}", project_key="tomcat", version="10.1.0", is_top_level=True
    )

    assert result == safe.RelPath("10.1.0")


def test_template_expands_major_version() -> None:
    result = construct.resolve_download_path_suffix(
        template="maven-{{MAJOR_VERSION}}/{{VERSION}}",
        project_key="maven",
        version="5.0.0-rc-5",
        is_top_level=True,
    )

    assert result == safe.RelPath("maven-5/5.0.0-rc-5")


def test_template_expands_placeholders() -> None:
    result = construct.resolve_download_path_suffix(
        template="binaries/{{PROJECT_KEY}}-{{VERSION}}",
        project_key="tomcat-native",
        version="2.0.0",
        is_top_level=False,
    )

    assert result == safe.RelPath("binaries/tomcat-native-2.0.0")


def test_effective_override_wins_over_policy() -> None:
    release = _release(
        override="custom/path", policy="{{VERSION}}", project_key="tomcat", committee_key="tomcat", version="10.1.0"
    )

    assert construct.effective_download_path_suffix(release) == safe.RelPath("custom/path")


def test_effective_template_override_resolves_placeholders() -> None:
    release = _release(
        override="rc/{{VERSION}}", policy="ignored", project_key="tomcat", committee_key="tomcat", version="10.1.0"
    )

    assert construct.effective_download_path_suffix(release) == safe.RelPath("rc/10.1.0")


def test_effective_empty_override_publishes_to_root() -> None:
    release = _release(
        override="", policy="{{VERSION}}", project_key="tomcat-native", committee_key="tomcat", version="2.0.0"
    )

    assert construct.effective_download_path_suffix(release) is None


def test_effective_without_override_falls_back_to_policy() -> None:
    release = _release(
        override=None,
        policy="maven-{{MAJOR_VERSION}}/{{VERSION}}",
        project_key="maven",
        committee_key="maven",
        version="5.0.0-rc-5",
    )

    assert construct.effective_download_path_suffix(release) == safe.RelPath("maven-5/5.0.0-rc-5")


def test_effective_without_override_uses_subproject_default() -> None:
    release = _release(override=None, policy="", project_key="tomcat-native", committee_key="tomcat", version="2.0.0")

    assert construct.effective_download_path_suffix(release) == safe.RelPath("tomcat-native-2.0.0")


def test_effective_without_override_top_level_publishes_to_root() -> None:
    release = _release(override=None, policy="", project_key="tomcat", committee_key="tomcat", version="10.1.0")

    assert construct.effective_download_path_suffix(release) is None


def test_validate_download_path_suffix_accepts_empty() -> None:
    validation.validate_download_path_suffix("")


def test_validate_download_path_suffix_accepts_major_version() -> None:
    validation.validate_download_path_suffix("maven-{{MAJOR_VERSION}}/{{VERSION}}")


def test_validate_download_path_suffix_accepts_template() -> None:
    validation.validate_download_path_suffix("binaries/{{PROJECT_KEY}}-{{VERSION}}")


def test_validate_download_path_suffix_rejects_absolute() -> None:
    with pytest.raises(ValueError, match="valid path"):
        validation.validate_download_path_suffix("/{{VERSION}}")


def test_validate_download_path_suffix_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="valid path"):
        validation.validate_download_path_suffix("../{{VERSION}}")
