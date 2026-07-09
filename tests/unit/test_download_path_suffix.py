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

import atr.construct as construct
import atr.models.safe as safe
import atr.models.validation as validation


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
