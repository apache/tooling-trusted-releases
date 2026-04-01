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

"""Tests for vote email formatting and binding vote determination."""

import pytest
from pytest import MonkeyPatch

import atr.storage.writers.vote as vote_writer
import atr.user as user


class MockApp:
    def __init__(self) -> None:
        self.extensions: dict[str, object] = {}


class MockConfig:
    def __init__(self, admin_users_additional: str = "") -> None:
        self.ADMIN_USERS_ADDITIONAL = admin_users_additional


class MockCommittee:
    def __init__(
        self,
        name: str = "testproject",
        display_name: str = "Test Project",
        committee_members: list[str] | None = None,
        committers: list[str] | None = None,
    ):
        self.name = name
        self.display_name = display_name
        self.committee_members = committee_members or []
        self.committers = committers or []


@pytest.fixture
def mock_app(monkeypatch: MonkeyPatch) -> MockApp:
    app = MockApp()
    monkeypatch.setattr("asfquart.APP", app)
    return app


def test_admin_not_on_pmc_has_non_binding_vote(mock_app: MockApp, monkeypatch: MonkeyPatch) -> None:
    """Admins who are not PMC members do not get binding votes."""
    user._get_additional_admin_users.cache_clear()
    monkeypatch.setattr("atr.config.get", lambda: MockConfig())
    mock_app.extensions["admins"] = frozenset({"admin_user"})

    assert user.is_admin("admin_user") is True

    committee = MockCommittee(
        committee_members=["alice", "bob"],
        committers=["alice", "bob", "charlie"],
    )
    # admin_user is not on this PMC
    is_pmc_member = user.is_committee_member(committee, "admin_user")  # type: ignore[arg-type]
    assert is_pmc_member is False

    # Binding is determined ONLY by PMC membership, not admin status
    is_binding = is_pmc_member
    assert is_binding is False


def test_binding_vote_body_format() -> None:
    """Binding votes include '(binding)' in the email body."""
    body = vote_writer.format_vote_email_body(
        vote="+1",
        asf_uid="pmcmember",
        fullname="PMC Member",
        is_binding=True,
    )
    assert body == "+1 (binding) (pmcmember) PMC Member"


def test_binding_vote_with_comment() -> None:
    """Binding votes with comments include the comment and signature."""
    body = vote_writer.format_vote_email_body(
        vote="+1",
        asf_uid="pmcmember",
        fullname="PMC Member",
        is_binding=True,
        comment="Verified signatures and checksums. Tests pass.",
    )
    expected = (
        "+1 (binding) (pmcmember) PMC Member\n\n"
        "Verified signatures and checksums. Tests pass.\n\n"
        "-- \nPMC Member (pmcmember)"
    )
    assert body == expected


def test_committer_non_pmc_has_non_binding_vote() -> None:
    """Committers who are not PMC members have non-binding votes."""
    committee = MockCommittee(
        committee_members=["alice", "bob", "charlie"],
        committers=["alice", "bob", "charlie", "dave", "eve"],
    )
    is_binding = user.is_committee_member(committee, "dave")  # type: ignore[arg-type]
    assert is_binding is False


def test_empty_comment_no_signature() -> None:
    """Empty comments do not add a signature."""
    body = vote_writer.format_vote_email_body(
        vote="+1",
        asf_uid="pmcmember",
        fullname="PMC Member",
        is_binding=True,
        comment="",
    )
    assert body == "+1 (binding) (pmcmember) PMC Member"
    assert "-- \n" not in body


def test_negative_binding_vote_body_format() -> None:
    """Negative binding votes are formatted correctly."""
    body = vote_writer.format_vote_email_body(
        vote="-1",
        asf_uid="pmcmember",
        fullname="PMC Member",
        is_binding=True,
    )
    assert body == "-1 (binding) (pmcmember) PMC Member"


def test_negative_vote_with_comment() -> None:
    """Negative votes with comments are formatted correctly."""
    body = vote_writer.format_vote_email_body(
        vote="-1",
        asf_uid="reviewer",
        fullname="Careful Reviewer",
        is_binding=True,
        comment="Found a license issue in the dependencies.",
    )
    expected = (
        "-1 (binding) (reviewer) Careful Reviewer\n\n"
        "Found a license issue in the dependencies.\n\n"
        "-- \nCareful Reviewer (reviewer)"
    )
    assert body == expected


def test_non_binding_vote_body_format() -> None:
    """Non-binding votes do not include '(binding)' in the email body."""
    body = vote_writer.format_vote_email_body(
        vote="+1",
        asf_uid="contributor",
        fullname="A Contributor",
        is_binding=False,
    )
    assert body == "+1 (contributor) A Contributor"


def test_non_binding_vote_with_comment() -> None:
    """Non-binding votes with comments include the comment and signature."""
    body = vote_writer.format_vote_email_body(
        vote="+1",
        asf_uid="contributor",
        fullname="A Contributor",
        is_binding=False,
        comment="Looks good to me!",
    )
    expected = "+1 (contributor) A Contributor\n\nLooks good to me!\n\n-- \nA Contributor (contributor)"
    assert body == expected


def test_non_committer_has_non_binding_vote() -> None:
    """Non-committers have non-binding votes."""
    committee = MockCommittee(
        committee_members=["alice", "bob", "charlie"],
        committers=["alice", "bob", "charlie", "dave", "eve"],
    )
    is_binding = user.is_committee_member(committee, "frank")  # type: ignore[arg-type]
    assert is_binding is False


def test_none_committee_returns_false() -> None:
    """None committee returns False for membership."""
    is_binding = user.is_committee_member(None, "anyone")
    assert is_binding is False


def test_pmc_member_has_binding_vote() -> None:
    """PMC members have binding votes."""
    committee = MockCommittee(
        committee_members=["alice", "bob", "charlie"],
        committers=["alice", "bob", "charlie", "dave", "eve"],
    )
    is_binding = user.is_committee_member(committee, "alice")  # type: ignore[arg-type]
    assert is_binding is True


def test_zero_binding_vote_body_format() -> None:
    """Zero binding votes are formatted correctly."""
    body = vote_writer.format_vote_email_body(
        vote="0",
        asf_uid="voter",
        fullname="Abstaining Voter",
        is_binding=True,
    )
    assert body == "0 (binding) (voter) Abstaining Voter"


def test_zero_non_binding_vote_body_format() -> None:
    """Zero non-binding votes are formatted correctly."""
    body = vote_writer.format_vote_email_body(
        vote="0",
        asf_uid="voter",
        fullname="Abstaining Voter",
        is_binding=False,
    )
    assert body == "0 (voter) Abstaining Voter"
