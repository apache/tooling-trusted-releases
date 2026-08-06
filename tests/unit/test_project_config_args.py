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

import re

import pydantic
import pytest

import atr.models.api as api
import atr.models.calver as calver
import atr.models.sql as sql
import atr.storage.writers.project as project_writer


def test_project_config_accepts_announce_recipients_on_any_apache_address() -> None:
    args = _project_config(announce_recipients={"to": "announce@apache.org", "cc": ["dev@other.apache.org"]})

    assert args.policy is not None
    assert args.policy.announce_recipients is not None
    assert args.policy.announce_recipients.to == "announce@apache.org"


def test_project_config_accepts_vote_recipients_on_committee_domain() -> None:
    args = _project_config(vote_recipients={"to": "private@tooling.apache.org", "cc": ["dev@tooling.apache.org"]})

    assert args.policy is not None
    assert args.policy.vote_recipients is not None
    assert args.policy.vote_recipients.to == "private@tooling.apache.org"


def test_project_config_rejects_announce_recipient_off_foundation() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be an apache.org address")):
        _project_config(announce_recipients={"to": "someone@example.org"})


def test_project_config_rejects_vote_recipient_off_foundation() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be on 'tooling.apache.org'")):
        _project_config(vote_recipients={"to": "private@tooling.apache.org", "cc": ["someone@example.org"]})


def test_project_config_rejects_vote_recipient_on_another_committee() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be on 'tooling.apache.org'")):
        _project_config(vote_recipients={"to": "dev@other.apache.org"})


def test_project_config_accepts_foundation_security_contact() -> None:
    args = _project_config_project(security_contact="security@apache.org")

    assert args.project is not None
    assert args.project.security_contact == "security@apache.org"


def test_project_config_accepts_pmc_security_contact() -> None:
    args = _project_config_project(security_contact="security@tooling.apache.org")

    assert args.project is not None
    assert args.project.security_contact == "security@tooling.apache.org"


def test_project_config_rejects_arbitrary_security_contact() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be 'security@apache.org'")):
        _project_config_project(security_contact="security@evil.example.org")


def test_project_config_rejects_other_committee_security_contact() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("must be 'security@apache.org'")):
        _project_config_project(security_contact="security@other.apache.org")


def test_project_config_accepts_threat_model_links() -> None:
    args = _project_config_project(
        threat_model_link="https://example.apache.org/threats",
        threat_model_src_link="https://example.apache.org/threats.md",
    )

    assert args.project is not None
    assert args.project.threat_model_link is not None
    assert args.project.threat_model_src_link is not None


def test_project_config_accepts_download_path_suffix() -> None:
    args = _project_config(download_path_suffix="{{PROJECT_KEY}}-{{VERSION}}")

    assert args.policy is not None
    assert args.policy.download_path_suffix == "{{PROJECT_KEY}}-{{VERSION}}"


def test_project_config_accepts_calver_format_for_a_calver_project() -> None:
    args = _project_config_project(version_method="calver", calver_format="(YY.MM).N")

    assert args.project is not None
    assert args.project.calver_format == "(YY.MM).N"


def test_project_config_rejects_calver_format_for_another_version_method() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("applies only when version_method is 'calver'")):
        _project_config_project(version_method="semver", calver_format="(YY.MM).N")


def test_project_config_rejects_a_hand_written_cycle_match_beside_a_date_format() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("not both")):
        _project_config_project(calver_format="(YY.MM).N", cycle_match=r"^(\d+)\.")


def test_project_config_rejects_a_malformed_date_format() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("unmatched (")):
        _project_config_project(version_method="calver", calver_format="(YY.MM")


def test_calver_format_compiles_the_cycle_match() -> None:
    project = _project()

    _apply_version_scheme(project, version_method="calver", calver_format="(YY.MM).N")

    assert project.version_method == sql.VersionMethod.CALVER
    assert project.cycle_match == calver.cycle_regex("(YY.MM).N")


def test_leaving_calver_clears_the_date_format() -> None:
    project = _project(version_method=sql.VersionMethod.CALVER, calver_format="(YY.MM).N")

    _apply_version_scheme(project, version_method="semver", cycle_match=r"^(\d+)\.\d+\.\d+$")

    assert project.version_method == sql.VersionMethod.SEMVER
    assert project.calver_format is None


def test_editing_an_unrelated_field_leaves_a_stored_cycle_match_alone() -> None:
    project = _project(version_method=sql.VersionMethod.CALVER, calver_format="(YY.MM).N", cycle_match="^(2025)")

    _apply_version_scheme(project, branch_template="release-{cycle}")

    assert project.cycle_match == "^(2025)"


def test_project_config_accepts_repository_web_and_vcs_uris() -> None:
    args = _project_config_project(repositories=["https://github.com/apache/x", "git+ssh://git@host/x.git"])

    assert args.project is not None
    assert args.project.repositories == ["https://github.com/apache/x", "git+ssh://git@host/x.git"]


@pytest.mark.parametrize("uri", ["javascript:alert(1)", "data:text/html,pwned"])
def test_project_config_rejects_browser_executable_repository_uri(uri: str) -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("disallowed or missing scheme")):
        _project_config_project(repositories=[uri])


def test_project_config_accepts_web_standard_uri() -> None:
    args = _project_config_project(standards=["https://example.org/spec"])

    assert args.project is not None
    assert args.project.standards == ["https://example.org/spec"]


def test_project_config_rejects_vcs_scheme_for_standard() -> None:
    # A VCS locator is valid for a repository but a standard must be a web page
    with pytest.raises(pydantic.ValidationError, match=re.escape("disallowed or missing scheme")):
        _project_config_project(standards=["git+ssh://git@host/x.git"])


def _apply_version_scheme(project: sql.Project, **fields: object) -> None:
    args = api.ProjectConfigProjectArgs.model_validate(fields)
    project_writer._apply_version_scheme(project, args, args.model_fields_set)


def _project(**fields: object) -> sql.Project:
    return sql.Project(key="example", name="Apache Example", committee_key="tooling", **fields)


def _project_config(**policy: object) -> api.ProjectConfigArgs:
    return api.ProjectConfigArgs.model_validate(
        {"project_key": "tooling", "committee_key": "tooling", "policy": policy}
    )


def _project_config_project(**project: object) -> api.ProjectConfigArgs:
    return api.ProjectConfigArgs.model_validate(
        {"project_key": "tooling", "committee_key": "tooling", "project": project}
    )
