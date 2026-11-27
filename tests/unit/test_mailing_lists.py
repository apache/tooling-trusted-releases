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


import json
import pathlib
import types
import unittest.mock as mock

import pytest
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.datasources.apache as apache
import atr.db as db
import atr.db.interaction as interaction
import atr.get.projects as projects
import atr.models.sql as sql
import atr.util as util

_COMMITTEE_DEV_LISTS = interaction.committee_dev_lists


@pytest.fixture
async def data(monkeypatch):
    monkeypatch.setattr(interaction, "committee_dev_lists", _COMMITTEE_DEV_LISTS)
    engine = sqlalchemy.ext.asyncio.create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(sqlmodel.SQLModel.metadata.create_all)
    factory = sqlalchemy.ext.asyncio.async_sessionmaker(engine, class_=db.Session, expire_on_commit=False)
    monkeypatch.setattr(db, "_global_atr_sessionmaker", factory)
    async with factory() as session:
        yield session
    await engine.dispose()


def test_announce_recipients_keep_fixed_and_project_choices(monkeypatch):
    committee = sql.Committee(key="maven", mail_addresses=["users@maven.apache.org"])
    project = sql.Project(key="maven", committee=committee)
    to, cc, bcc = [f"{name}-users@maven.apache.org" for name in ["doxia", "scm", "wagon"]]
    project.release_policy = sql.ReleasePolicy(recipient_defaults={"announce": {"to": to, "cc": [cc], "bcc": [bcc]}})
    monkeypatch.setattr(util.config, "get", lambda: types.SimpleNamespace(ATR_STATUS="BETA"))
    expected = ["announce@apache.org", "users@maven.apache.org", "private@maven.apache.org", to, cc, bcc]
    assert util.permitted_announce_recipients("alice", committee, settings=project) == expected
    html = str(projects._recipient_grid_widget(project, project, sql.RecipientAction.ANNOUNCE, readonly=False))
    for address in expected:
        assert address in html
    monkeypatch.setattr(util.config, "get", lambda: types.SimpleNamespace(ATR_STATUS="ALPHA"))
    assert util.permitted_announce_recipients("alice", committee, settings=project) == [
        util.USER_TESTS_ADDRESS,
        "alice@apache.org",
    ]


@pytest.mark.parametrize(
    "key, domain, names, expected",
    [
        ("httpcomponents", "hc.apache.org", ["dev", "httpclient-users", "commits"], ["dev"]),
        ("db", "db.apache.org", ["derby-user", "httpclient-users", "commits"], []),
        ("maven", "maven.apache.org", ["users", "commits", "doxia-users"], ["users"]),
        (
            "example",
            "example.apache.org",
            ["announce", "dev", "user", "users", "issues"],
            ["announce", "dev", "user", "users"],
        ),
        ("httpcomponents", "hc.apache.org", ["commits"], []),
    ],
)
async def test_fetch_filters_names_including_zero_activity(monkeypatch, key, domain, names, expected):
    _mock_feed(monkeypatch, {"login": {}, "versions": {}, "lists": {domain: dict.fromkeys(names, 0)}})
    result = await apache.get_mailing_lists_data()
    committee = sql.Committee(key=key)
    apache._update_mail_addresses(committee, domain, result)
    addresses = [f"{name}@{domain}" for name in expected]
    assert committee.mail_addresses == addresses
    assert committee.list_domain == domain
    recipients = util.configurable_recipients(
        sql.RecipientAction.ANNOUNCE, domain, is_podling=False, mail_addresses=committee.mail_addresses
    )
    monkeypatch.setattr(util.config, "get", lambda: types.SimpleNamespace(ATR_STATUS="BETA"))
    assert util.permitted_announce_recipients("alice", committee) == recipients
    assert recipients == ["announce@apache.org", *addresses, f"private@{domain}"]


async def test_metadata_refresh_forgets_a_mail_domain_whimsy_no_longer_gives(data):
    data.add(sql.Committee(key="httpcomponents", keys_mode=sql.KeysMode.MANUAL, mail_domain="hc.apache.org"))
    await data.commit()
    ldap_raw = _fixture("ldap_projects")
    entry = ldap_raw["projects"].pop("tooling")
    entry["pmc"] = True
    ldap_raw["projects"] = {"httpcomponents": entry}

    await apache._update_committees(data, apache.LDAPProjectsData.model_validate(ldap_raw), {}, {}, None)

    committee = await data.committee(key="httpcomponents").demand(AssertionError("Missing"))
    assert committee.mail_domain is None
    assert committee.list_domain == "httpcomponents.apache.org"


@pytest.mark.parametrize("available", [False, True])
async def test_metadata_refresh_updates_roster_and_lists(data, monkeypatch, available):
    committee = sql.Committee(key="httpcomponents", keys_mode=sql.KeysMode.MANUAL)
    committee.mail_addresses = ["dev@old.apache.org"]
    committee.committee_members = ["old-member"]
    data.add_all([committee, sql.Committee(key="tooling", keys_mode=sql.KeysMode.MANUAL)])
    await data.commit()
    ldap_raw = _fixture("ldap_projects")
    entry = ldap_raw["projects"].pop("tooling")
    entry["pmc"] = True
    ldap_raw["projects"]["httpcomponents"] = entry
    whimsy_raw = _fixture("committees")
    entry = whimsy_raw["committees"].pop("tooling")
    entry["mail_list"] = "hc"
    whimsy_raw["committees"]["httpcomponents"] = entry
    feeds = {
        "get_ldap_projects_data": apache.LDAPProjectsData.model_validate(ldap_raw),
        "get_people_data": apache.LDAPPeopleData(people_count=0, people={}),
        "get_current_podlings_data": apache.PodlingsData.model_validate(_fixture("podlings")),
        "get_whimsy_committee_data": apache.WhimsyCommitteeData.model_validate(whimsy_raw),
        "get_committee_data": {},
        "get_retired_committee_data": apache.RetiredCommitteeData.model_validate(_fixture("retired_committees")),
        "get_whimsy_podlings_data": apache.WhimsyPodlingsData.model_validate(_fixture("whimsy_podlings")),
    }
    for name, result in feeds.items():
        monkeypatch.setattr(apache, name, mock.AsyncMock(return_value=result))
    monkeypatch.setattr(apache.config, "get", lambda: types.SimpleNamespace(TOOLING_USERS_ADDITIONAL=""))
    lists = {
        "amoro.apache.org": {"dev": 0},
        "hc.apache.org": {"dev": 0, "httpclient-users": 5},
        "tooling.apache.org": {"dev": 0},
    }
    _mock_feed(monkeypatch, {"lists": lists if available else {}})
    await apache.update_metadata()
    await data.refresh(committee)
    assert committee.committee_members == ["wave", "sbp", "tn"]
    assert committee.mail_addresses == (["dev@hc.apache.org"] if available else ["dev@old.apache.org"])
    for key in ["amoro", "tooling"]:
        updated = await data.committee(key=key).get()
        assert updated is not None
        assert updated.mail_addresses == ([f"dev@{key}.apache.org"] if available else [])


@pytest.mark.parametrize(
    ("action", "addresses", "problem"),
    [
        (sql.RecipientAction.VOTE, ["dev@db.apache.org", "jdo-dev@db.apache.org"], None),
        # Private lists aren't reported, so the committee's own is allowed
        (sql.RecipientAction.VOTE, ["private@db.apache.org"], None),
        (sql.RecipientAction.VOTE, ["Dev@DB.apache.org", ""], None),
        (sql.RecipientAction.VOTE, ["jdo-devel@db.apache.org"], "jdo-devel@db.apache.org is not a mailing list"),
        (sql.RecipientAction.VOTE, ["dev@maven.apache.org"], "must be on 'db.apache.org'"),
        (sql.RecipientAction.ANNOUNCE, ["announce@apache.org", "dev@maven.apache.org"], None),
        (sql.RecipientAction.ANNOUNCE, ["private@maven.apache.org"], "private@maven.apache.org is not a mailing list"),
        (sql.RecipientAction.ANNOUNCE, ["someone@example.org"], "someone@example.org"),
    ],
)
async def test_recipients_have_to_be_lists_that_exist(data, action, addresses, problem):
    for domain, label in [
        ("apache.org", "announce"),
        ("db.apache.org", "dev"),
        ("db.apache.org", "jdo-dev"),
        ("maven.apache.org", "dev"),
    ]:
        data.add(sql.MailingList(domain=domain, label=label))
    await data.commit()

    found = await interaction.recipients_problem("db.apache.org", action, addresses, data)

    if problem is None:
        assert found is None
    else:
        assert (found is not None) and (problem in found)


async def test_recipients_are_not_checked_against_lists_before_any_are_stored(data):
    found = await interaction.recipients_problem(
        "db.apache.org", sql.RecipientAction.VOTE, ["jdo-devel@db.apache.org"], data
    )

    assert found is None


async def test_recipients_follow_the_domain_a_committee_has_its_lists_on(data):
    data.add(sql.MailingList(domain="hc.apache.org", label="dev"))
    await data.commit()
    committee = sql.Committee(key="httpcomponents", mail_domain="hc.apache.org")
    vote = sql.RecipientAction.VOTE

    assert await interaction.recipients_problem(committee.list_domain, vote, ["dev@hc.apache.org"], data) is None
    assert await interaction.recipients_problem(committee.list_domain, vote, ["private@hc.apache.org"], data) is None
    found = await interaction.recipients_problem(committee.list_domain, vote, ["dev@httpcomponents.apache.org"], data)
    assert (found is not None) and ("must be on 'hc.apache.org'" in found)


async def test_recipients_of_a_committee_with_no_stored_lists_are_not_checked(data):
    data.add(sql.MailingList(domain="hc.apache.org", label="dev"))
    await data.commit()
    committee = sql.Committee(key="test")

    # A committee made for testing has no lists at lists.apache.org
    assert (
        await interaction.recipients_problem(
            committee.list_domain, sql.RecipientAction.VOTE, ["dev@test.apache.org"], data
        )
        is None
    )
    # That is only so for the committee's own domain
    found = await interaction.recipients_problem(
        committee.list_domain, sql.RecipientAction.ANNOUNCE, ["dev@tomcta.apache.org"], data
    )
    assert (found is not None) and ("is not a mailing list" in found)


async def test_vote_settings_offer_the_dev_lists_a_committee_has(data):
    for label in ("jdo-dev", "derby-dev", "jdo-user", "general"):
        data.add(sql.MailingList(domain="db.apache.org", label=label))
    data.add(sql.MailingList(domain="maven.apache.org", label="dev"))
    await data.commit()

    lists = await interaction.committee_dev_lists(sql.Committee(key="db"), data)

    assert lists == ["derby-dev@db.apache.org", "jdo-dev@db.apache.org"]
    assert util.configurable_recipients(
        sql.RecipientAction.VOTE, "db.apache.org", is_podling=False, dev_lists=lists
    ) == [*lists, "private@db.apache.org"]
    # With no lists known, the usual dev list is offered
    assert util.configurable_recipients(sql.RecipientAction.VOTE, "db.apache.org", is_podling=True) == [
        "dev@db.apache.org"
    ]


def test_votes_can_go_to_any_of_the_dev_lists_a_committee_has(monkeypatch):
    monkeypatch.setattr(util.config, "get", lambda: types.SimpleNamespace(ATR_STATUS="BETA"))
    lists = ["derby-dev@db.apache.org", "jdo-dev@db.apache.org"]

    permitted = util.permitted_voting_recipients("alice", "db.apache.org", dev_lists=lists)

    assert permitted == [*lists, "private@db.apache.org"]
    assert (
        util.permitted_podling_first_round_recipients("alice", "db.apache.org", is_podling=True, dev_lists=lists)
        == lists
    )


async def test_stored_lists_follow_the_feed_and_survive_a_failed_fetch(data):
    first = apache.MailingListsData(lists={"db.apache.org": {"dev": 3, "jdo-dev": 0}, "apache.org": {"announce": 1}})
    second = apache.MailingListsData(lists={"db.apache.org": {"dev": 3, "derby-dev": 2}})

    await apache._update_mailing_lists(data, first)
    await data.commit()
    await apache._update_mailing_lists(data, None)
    await data.commit()
    assert {(row.domain, row.label) for row in await data.mailing_list().all()} == {
        ("apache.org", "announce"),
        ("db.apache.org", "dev"),
        ("db.apache.org", "jdo-dev"),
    }

    await apache._update_mailing_lists(data, second)
    await data.commit()
    assert {(row.domain, row.label) for row in await data.mailing_list().all()} == {
        ("db.apache.org", "derby-dev"),
        ("db.apache.org", "dev"),
    }


def _fixture(name):
    return json.loads((pathlib.Path(__file__).parent / "datasources" / "testdata" / f"{name}.json").read_text())


def _mock_feed(monkeypatch, payload):
    response = mock.MagicMock()
    response.json = mock.AsyncMock(return_value=payload)
    response.__aenter__.return_value = response
    session = mock.MagicMock()
    session.get.return_value = response
    session.__aenter__.return_value = session
    monkeypatch.setattr(apache.util, "create_secure_session", mock.MagicMock(return_value=session))
