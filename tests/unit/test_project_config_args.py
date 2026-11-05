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


def test_project_config_rejects_calver_format_without_version_method() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("applies only when version_method is 'calver'")):
        _project_config_project(calver_format="(YY.MM).N")


def test_project_config_rejects_calver_format_for_another_version_method() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("applies only when version_method is 'calver'")):
        _project_config_project(version_method="semver", calver_format="(YY.MM).N")


def test_project_config_rejects_a_hand_written_cycle_match_beside_a_date_format() -> None:
    with pytest.raises(pydantic.ValidationError, match=re.escape("not both")):
        _project_config_project(version_method="calver", calver_format="(YY.MM).N", cycle_match=r"^(\d+)\.")


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


def test_project_config_accepts_template_urls() -> None:
    url = "https://raw.githubusercontent.com/apache/example/main/vote.txt"
    args = _project_config(
        announce_release_template_url=url,
        start_vote_template_url=url,
        finish_vote_template_url=url,
        vote_comment_template_url=url,
    )

    assert args.policy is not None
    assert args.policy.start_vote_template_url == url
    assert args.policy.vote_comment_template_url == url


def test_project_config_rejects_template_url_on_other_host() -> None:
    with pytest.raises(pydantic.ValidationError, match="not allowed"):
        _project_config(start_vote_template_url="https://example.com/vote.txt")


def test_project_config_sync_mode_defaults_to_merge() -> None:
    assert _project_config().sync_mode == "merge"


def test_project_config_sync_mode_rejects_unknown_value() -> None:
    with pytest.raises(pydantic.ValidationError):
        api.ProjectConfigArgs.model_validate({"project_key": "tooling", "committee_key": "tooling", "sync_mode": "x"})


def test_full_sync_fills_omitted_policy_fields_with_defaults() -> None:
    args = api.ProjectConfigPolicyArgs.model_validate({"min_hours": 72})

    completed = project_writer._complete_policy_args(args)

    assert completed.min_hours == 72
    assert completed.vote_mode == sql.VoteMode.TRUSTED
    assert completed.source_artifact_paths == []
    # Recipients clear only if the field is marked as set, since None is already the default
    assert "vote_recipients" in completed.model_fields_set
    assert "announce_recipients" in completed.model_fields_set
    assert completed.vote_recipients is None


def test_full_sync_with_no_policy_block_resets_whole_policy() -> None:
    completed = project_writer._complete_policy_args(None)

    assert "release_checklist" in completed.model_fields_set


def test_full_sync_clears_omitted_project_fields_but_not_name() -> None:
    project = _project()
    args = api.ProjectConfigProjectArgs.model_validate({"description": "Kept"})

    completed = project_writer._complete_project_args(project, args)

    assert completed.description == "Kept"
    assert "name" not in completed.model_fields_set
    assert "homepage" in completed.model_fields_set
    assert completed.homepage is None
    assert "repositories" in completed.model_fields_set


def test_full_sync_leaves_default_version_scheme_alone() -> None:
    project = _project()
    args = api.ProjectConfigProjectArgs()

    assert project_writer._version_scheme_resets(project, args) == {}


def test_full_sync_resets_only_version_fields_that_differ() -> None:
    project = _project(version_pattern=r"\d+", branch_template="rel/{x}")
    args = api.ProjectConfigProjectArgs.model_validate({"branch_template": "rel/{x}"})

    assert project_writer._version_scheme_resets(project, args) == {"version_pattern": None}


def test_full_sync_moves_calver_project_back_to_simple() -> None:
    project = _project(version_method=sql.VersionMethod.CALVER, calver_format="YY.MM", cycle_match="x")
    args = api.ProjectConfigProjectArgs()

    resets = project_writer._version_scheme_resets(project, args)

    assert resets["version_method"] == sql.VersionMethod.SIMPLE
    assert resets["cycle_match"] is None
    assert "calver_format" not in resets


def test_full_sync_never_resets_cycle_match_for_calver_in_file() -> None:
    project = _project(version_method=sql.VersionMethod.CALVER, calver_format="YY.MM", cycle_match="x")
    args = api.ProjectConfigProjectArgs.model_validate({"version_method": "calver"})

    resets = project_writer._version_scheme_resets(project, args)

    assert "cycle_match" not in resets
    assert resets == {"calver_format": None}


def test_full_asf_yaml_update_is_marked_and_still_counts_as_synced() -> None:
    project = _project()

    project.mark_updated(by="system", update_type=sql.UpdateType.ASFYAML_FULL)

    assert project.is_synced_from_asf_yaml
    assert project.updated_by_display == ".asf.yaml (full)"


def test_manual_update_is_not_synced_from_asf_yaml() -> None:
    project = _project()

    project.mark_updated(by="alice", update_type=sql.UpdateType.MANUAL)

    assert not project.is_synced_from_asf_yaml
