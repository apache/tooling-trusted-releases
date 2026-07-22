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

import strictyaml

import atr.get.projects as projects
import atr.models.sql as sql


class _FakeProject:
    def __init__(self, recipients=None, **fields):
        defaults = {
            "key": None,
            "committee_key": None,
            "name": None,
            "description": None,
            "short_description": None,
            "homepage": None,
            "lifecycle_page": None,
            "download_page": None,
            "bug_database": None,
            "mailing_lists": None,
            "security_contact": None,
            "threat_model_link": None,
            "threat_model_src_link": None,
            "repositories": [],
            "standards": [],
            "categories": None,
            "programming_languages": None,
            "release_policy": None,
            "update_type": sql.UpdateType.MANUAL,
        }
        defaults.update(fields)
        for key, value in defaults.items():
            setattr(self, key, value)
        self._recipients = recipients or {}

    def policy_recipients(self, action):
        entry = self._recipients.get(action, {})
        return entry.get("to", ""), list(entry.get("cc", [])), list(entry.get("bcc", []))


class _FakePolicy:
    def __init__(self, **fields):
        defaults = {
            "license_check_mode": sql.LicenseCheckMode.BOTH,
            "binary_artifact_paths": [],
            "source_artifact_paths": [],
            "source_excludes_lightweight": [],
            "source_excludes_rat": [],
            "file_tag_mappings": {},
            "vote_mode": sql.VoteMode.EMAIL,
            "min_hours": None,
            "release_checklist": "",
            "vote_comment_template": "",
            "start_vote_subject": "",
            "start_vote_template": "",
            "finish_vote_template": "",
            "announce_release_subject": "",
            "announce_release_template": "",
            "github_repository_name": "",
            "github_repository_branch": "",
            "github_compose_workflow_path": [],
            "github_vote_workflow_path": [],
            "github_finish_workflow_path": [],
            "download_path_suffix": "",
        }
        defaults.update(fields)
        for key, value in defaults.items():
            setattr(self, key, value)


def test_export_includes_download_path_suffix_when_set() -> None:
    project = _FakeProject(
        key="example",
        committee_key="tooling",
        name="Apache Example",
        release_policy=_FakePolicy(download_path_suffix="{{PROJECT_KEY}}-{{VERSION}}"),
    )

    policy = strictyaml.load(projects._asf_yaml_export(project)).data["project"]["policy"]

    assert policy["download_path_suffix"] == "{{PROJECT_KEY}}-{{VERSION}}"


def test_export_includes_only_recipient_keys_that_are_set() -> None:
    project = _FakeProject(
        key="example",
        committee_key="tooling",
        name="Apache Example",
        recipients={sql.RecipientAction.VOTE: {"to": "private@example.apache.org"}},
        release_policy=_FakePolicy(),
    )

    policy = strictyaml.load(projects._asf_yaml_export(project)).data["project"]["policy"]

    assert policy == {"vote_recipients": {"to": "private@example.apache.org"}}


def test_export_includes_set_policy_fields() -> None:
    project = _FakeProject(
        key="example",
        committee_key="tooling",
        name="Apache Example",
        recipients={
            sql.RecipientAction.VOTE: {"to": "private@example.apache.org"},
            sql.RecipientAction.ANNOUNCE: {"to": "announce@example.apache.org"},
        },
        release_policy=_FakePolicy(
            license_check_mode=sql.LicenseCheckMode.RAT,
            source_excludes_rat=["*.md"],
            file_tag_mappings={"docs": ["*.md"]},
            vote_mode=sql.VoteMode.MANUAL,
            min_hours=48,
            start_vote_subject="[VOTE] Custom {{VERSION}}",
            announce_release_subject="[ANNOUNCE] Custom {{VERSION}}",
            github_repository_name="apache/example",
            github_compose_workflow_path=[".github/workflows/compose.yml"],
        ),
    )

    policy = strictyaml.load(projects._asf_yaml_export(project)).data["project"]["policy"]

    # strictyaml reads scalars back as strings, so ints and bools round-trip as text
    assert policy == {
        "license_check_mode": "RAT",
        "source_excludes_rat": ["*.md"],
        "file_tag_mappings": {"docs": ["*.md"]},
        "vote_recipients": {"to": "private@example.apache.org"},
        "vote_mode": "manual",
        "min_hours": "48",
        "start_vote_subject": "[VOTE] Custom {{VERSION}}",
        "announce_recipients": {"to": "announce@example.apache.org"},
        "announce_release_subject": "[ANNOUNCE] Custom {{VERSION}}",
        "github_repository_name": "apache/example",
        "github_compose_workflow_path": [".github/workflows/compose.yml"],
    }


def test_export_minimal_project_omits_empty_blocks() -> None:
    project = _FakeProject(key="trusted-releases", committee_key="tooling", name="Apache Trusted Releases")

    loaded = strictyaml.load(projects._asf_yaml_export(project)).data

    assert loaded == {
        "project": {
            "metadata": {"key": "trusted-releases", "committee": "tooling", "name": "Apache Trusted Releases"},
            "features": {"atr_sync": "true"},
        }
    }


def test_export_omits_default_policy_fields() -> None:
    project = _FakeProject(
        key="example",
        committee_key="tooling",
        name="Apache Example",
        release_policy=_FakePolicy(),
    )

    loaded = strictyaml.load(projects._asf_yaml_export(project)).data["project"]

    # A policy row with nothing set away from defaults shouldn't add a policy block
    assert "policy" not in loaded


def test_export_reproduces_every_field() -> None:
    project = _FakeProject(
        key="trusted-releases",
        committee_key="tooling",
        name="Apache Trusted Releases",
        short_description="A platform for making official ASF software releases.",
        homepage="https://tooling.apache.org/trusted-releases.html",
        lifecycle_page="https://tooling.apache.org/trusted-releases.html",
        download_page="https://github.com/apache/tooling-trusted-releases",
        bug_database="https://github.com/apache/tooling-trusted-releases/issues",
        mailing_lists="https://tooling.apache.org/volunteer.html",
        security_contact="security@tooling.apache.org",
        threat_model_link="https://tooling.apache.org/security/threat-model",
        threat_model_src_link="https://tooling.apache.org/security/threat-model.md",
        repositories=["git+ssh://git@github.com:apache/tooling-trusted-releases.git"],
        standards=["https://owasp.org/www-project-application-security-verification-standard/"],
        categories="build-management",
        programming_languages="python",
        recipients={
            sql.RecipientAction.VOTE: {"to": "private@tooling.apache.org", "cc": ["dev@tooling.apache.org"]},
            sql.RecipientAction.ANNOUNCE: {"to": "announce@apache.org"},
        },
        release_policy=_FakePolicy(),
    )

    loaded = strictyaml.load(projects._asf_yaml_export(project)).data

    assert loaded == {
        "project": {
            "metadata": {
                "key": "trusted-releases",
                "committee": "tooling",
                "name": "Apache Trusted Releases",
                "short_description": "A platform for making official ASF software releases.",
                "homepage": "https://tooling.apache.org/trusted-releases.html",
                "lifecycle_page": "https://tooling.apache.org/trusted-releases.html",
                "download_page": "https://github.com/apache/tooling-trusted-releases",
                "bug_database": "https://github.com/apache/tooling-trusted-releases/issues",
                "mailing_lists": "https://tooling.apache.org/volunteer.html",
                "security_contact": "security@tooling.apache.org",
                "threat_model_link": "https://tooling.apache.org/security/threat-model",
                "threat_model_src_link": "https://tooling.apache.org/security/threat-model.md",
                "repositories": ["git+ssh://git@github.com:apache/tooling-trusted-releases.git"],
                "standards": ["https://owasp.org/www-project-application-security-verification-standard/"],
                "categories": ["build-management"],
                "programming_languages": ["python"],
            },
            "policy": {
                "vote_recipients": {"to": "private@tooling.apache.org", "cc": ["dev@tooling.apache.org"]},
                "announce_recipients": {"to": "announce@apache.org"},
            },
            "features": {"atr_sync": "true"},
        }
    }


def test_export_splits_comma_joined_columns_into_sequences() -> None:
    project = _FakeProject(
        key="example",
        committee_key="tooling",
        name="Apache Example",
        categories="data, storage",
        programming_languages="c,python",
    )

    metadata = strictyaml.load(projects._asf_yaml_export(project)).data["project"]["metadata"]

    assert metadata["categories"] == ["data", "storage"]
    assert metadata["programming_languages"] == ["c", "python"]
