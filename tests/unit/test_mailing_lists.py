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
import atr.get.projects as projects
import atr.models.sql as sql
import atr.util as util


@pytest.fixture
async def data(monkeypatch):
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
    assert util.permitted_announce_recipients("alice", committee, project=project) == expected
    html = str(projects._recipient_grid_widget(project, sql.RecipientAction.ANNOUNCE, readonly=False))
    for address in expected:
        assert address in html
    monkeypatch.setattr(util.config, "get", lambda: types.SimpleNamespace(ATR_STATUS="ALPHA"))
    assert util.permitted_announce_recipients("alice", committee, project=project) == [
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
    recipients = util.configurable_recipients(
        sql.RecipientAction.ANNOUNCE, key, is_podling=False, mail_addresses=committee.mail_addresses
    )
    monkeypatch.setattr(util.config, "get", lambda: types.SimpleNamespace(ATR_STATUS="BETA"))
    assert util.permitted_announce_recipients("alice", committee) == recipients
    assert recipients == ["announce@apache.org", *addresses, f"private@{key}.apache.org"]


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
