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

import pathlib

import pytest

import atr.tasks.checks.rat as rat

# Archive WITHOUT .rat-excludes
# (It still uses the old, now removed, rat-excludes.txt convention)
TEST_ARCHIVE = pathlib.Path(__file__).parent.parent / "e2e" / "test_files" / "apache-test-0.2.tar.gz"

# Archive WITH .rat-excludes
TEST_ARCHIVE_WITH_RAT_EXCLUDES = (
    pathlib.Path(__file__).parent.parent.parent / "playwright" / "apache-test-0.2" / "apache-test-0.2.tar.gz"
)


@pytest.fixture
def rat_available() -> tuple[bool, bool]:
    # TODO: Make this work properly in CI
    java_ok = rat._synchronous_check_java_installed() is None
    _, jar_error = rat._synchronous_check_jar_exists(rat._CONFIG.APACHE_RAT_JAR_PATH)
    jar_ok = jar_error is None
    return (java_ok, jar_ok)


def test_check_includes_command(rat_available: tuple[bool, bool]):
    _skip_if_unavailable(rat_available)
    result = rat._synchronous(str(TEST_ARCHIVE), [])
    assert len(result.command) > 0
    assert "java" in result.command
    assert "-jar" in result.command
    assert "--" in result.command
    assert "." in result.command


def test_check_includes_excludes_source_none(rat_available: tuple[bool, bool]):
    _skip_if_unavailable(rat_available)
    result = rat._synchronous(str(TEST_ARCHIVE), [])
    assert result.excludes_source == "none"


def test_check_includes_excludes_source_policy(rat_available: tuple[bool, bool]):
    _skip_if_unavailable(rat_available)
    result = rat._synchronous(str(TEST_ARCHIVE), ["*.py"])
    assert result.excludes_source == "policy"


def test_excludes_archive_ignores_policy_when_file_exists(rat_available: tuple[bool, bool]):
    """When archive has .rat-excludes, ignore policy patterns even if provided."""
    _skip_if_unavailable(rat_available)
    result = rat._synchronous(str(TEST_ARCHIVE_WITH_RAT_EXCLUDES), ["*.py", "*.txt"])
    assert result.excludes_source == "archive"
    # Should NOT use the RAT policy file
    assert rat._POLICY_EXCLUDES_FILENAME not in result.command


def test_excludes_archive_uses_rat_excludes_file(rat_available: tuple[bool, bool]):
    """When archive has .rat-excludes, use it and set source to archive."""
    _skip_if_unavailable(rat_available)
    result = rat._synchronous(str(TEST_ARCHIVE_WITH_RAT_EXCLUDES), [])
    assert result.excludes_source == "archive"
    assert "--input-exclude-file" in result.command
    # Should use the RAT excludes file, not the RAT policy file
    idx = result.command.index("--input-exclude-file")
    assert result.command[idx + 1] == rat._RAT_EXCLUDES_FILENAME


def test_excludes_none_has_no_exclude_file(rat_available: tuple[bool, bool]):
    """When neither archive nor policy, no exclude file should be used."""
    _skip_if_unavailable(rat_available)
    result = rat._synchronous(str(TEST_ARCHIVE), [])
    assert result.excludes_source == "none"
    assert "--input-exclude-file" not in result.command
    # Should have neither excludes file in command
    assert rat._RAT_EXCLUDES_FILENAME not in result.command
    assert rat._POLICY_EXCLUDES_FILENAME not in result.command


def test_excludes_policy_uses_atr_rat_excludes(rat_available: tuple[bool, bool]):
    """When no archive .rat-excludes but policy exists, use policy file."""
    _skip_if_unavailable(rat_available)
    # The second argument to rat._synchronous is a list of exclusions from policy
    result = rat._synchronous(str(TEST_ARCHIVE), ["*.py"])
    assert result.excludes_source == "policy"
    assert "--input-exclude-file" in result.command
    # Should use the RAT policy file, not the RAT excludes file
    idx = result.command.index("--input-exclude-file")
    assert result.command[idx + 1] == rat._POLICY_EXCLUDES_FILENAME
    # Should therefore NOT have the RAT excludes file in the command
    assert rat._RAT_EXCLUDES_FILENAME not in result.command


def test_sanitise_command_replaces_absolute_paths():
    command = [
        "java",
        "-jar",
        "/opt/tools/apache-rat-0.17.jar",
        "--output-file",
        "/fake/path/rat_verify_abc123/rat-report.xml",
        "--input-exclude",
        ".rat-excludes",
        "--",
        ".",
    ]
    result = rat._sanitise_command_for_storage(command)
    assert result[2] == "apache-rat-0.17.jar"
    assert result[4] == "rat-report.xml"
    assert result[6] == ".rat-excludes"


def _skip_if_unavailable(rat_available: tuple[bool, bool]) -> None:
    java_ok, jar_ok = rat_available
    if not java_ok:
        pytest.skip("Java not available")
    if not jar_ok:
        pytest.skip("RAT JAR not available")
