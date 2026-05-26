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

"""Apache specific data-sources."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Annotated, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

import pydantic
import sqlalchemy.dialects.sqlite as sqlite
import sqlmodel

import atr.config as config
import atr.db as db
import atr.ldap as ldap
import atr.log as log
import atr.models.helpers as helpers
import atr.models.safe as safe
import atr.models.schema as schema
import atr.models.sql as sql
import atr.util as util

_WHIMSY_COMMITTEE_INFO_URL: Final[str] = "https://whimsy.apache.org/public/committee-info.json"
_WHIMSY_COMMITTEE_RETIRED_URL: Final[str] = "https://whimsy.apache.org/public/committee-retired.json"
_WHIMSY_PEOPLE_URL: Final[str] = "https://whimsy.apache.org/public/public_ldap_people.json"
_WHIMSY_PROJECTS_URL: Final[str] = "https://whimsy.apache.org/public/public_ldap_projects.json"
_PROJECTS_COMMITTEE_URL: Final[str] = "https://projects.apache.org/json/foundation/committees.json"
_PROJECTS_PROJECTS_URL: Final[str] = "https://projects.apache.org/json/foundation/projects.json"
_PROJECTS_PODLINGS_URL: Final[str] = "https://projects.apache.org/json/foundation/podlings.json"
_PROJECTS_GROUPS_URL: Final[str] = "https://projects.apache.org/json/foundation/groups.json"


class RosterCountDetails(schema.Strict):
    members: int
    owners: int


class LDAPProjectsData(schema.Strict):
    last_timestamp: str = schema.alias("lastTimestamp")
    project_count: int
    roster_counts: dict[str, RosterCountDetails]
    projects: Annotated[list[LDAPProject], helpers.DictToList(key="name")]

    @property
    def last_time(self) -> datetime.datetime:
        return datetime.datetime.strptime(self.last_timestamp, "%Y%m%d%H%M%S%z")


class LDAPProject(schema.Strict):
    name: str
    create_timestamp: str = schema.alias("createTimestamp")
    modify_timestamp: str = schema.alias("modifyTimestamp")
    member_count: int
    owner_count: int
    members: list[str]
    owners: list[str]
    pmc: bool = False
    podling: str | None = None


class User(schema.Strict):
    id: str
    name: str
    date: str | None = None


class Committee(schema.Strict):
    chair: str
    charter: str | None = None
    established: str
    group: str
    homepage: str
    id: str
    name: str
    rdf: str | None = None
    reporting: int | None = None
    roster: Annotated[list[User], helpers.DictToList(key="id")]
    shortdesc: str


class WhimsyCommittee(schema.Strict):
    name: str
    display_name: str
    site: str | None
    description: str | None
    mail_list: str
    established: str | None
    report: list[str]
    chair: Annotated[list[User], helpers.DictToList(key="id")]
    roster_count: int
    roster: Annotated[list[User], helpers.DictToList(key="id")]
    pmc: bool


class WhimsyCommitteeData(schema.Strict):
    last_updated: str
    committee_count: int
    pmc_count: int
    roster_counts: dict[str, int] = schema.factory(dict)
    officers: dict[str, Any] = schema.factory(dict)
    board: dict[str, Any] = schema.factory(dict)
    committees: Annotated[list[WhimsyCommittee], helpers.DictToList(key="name")]
    next_board_meetings: dict[str, Any] = schema.alias_opt("nextBoardMeetings")


class RetiredCommittee(schema.Strict):
    name: str
    display_name: str
    retired: str
    description: str | None


class RetiredCommitteeData(schema.Strict):
    last_updated: str
    retired_count: int
    retired: Annotated[list[RetiredCommittee], helpers.DictToList(key="name")]


class PodlingStatus(schema.Strict):
    description: str
    homepage: str
    name: str = schema.alias("name")
    pmc: str
    podling: bool
    started: str
    champion: str | None = None
    retiring: bool | None = None
    resolution: str | None = None


class PodlingsData(helpers.DictRoot[PodlingStatus]):
    pass


class GroupsData(helpers.DictRoot[list[str]]):
    pass


class LDAPPersonEntry(schema.Subset):
    name: str


class LDAPPeopleData(schema.Subset):
    people_count: int
    people: dict[str, LDAPPersonEntry]


class MaintainerInfo(schema.Strict):
    mbox: str | None = None
    name: str | None = None
    homepage: str | None = None
    mbox_sha1sum: str | None = None
    nick: str | None = None
    same_as: str | None = schema.alias_opt("sameAs")


class PersonInfo(schema.Strict):
    name: str | None = None
    homepage: str | None = None
    mbox: str | None = None


class ChairInfo(schema.Strict):
    person: PersonInfo | None = schema.alias_opt("Person")


class HelperInfo(schema.Strict):
    name: str | None = None
    homepage: str | None = None


class OnlineAccountInfo(schema.Strict):
    account_service_homepage: str | None = schema.alias_opt("accountServiceHomepage")
    account_name: str | None = schema.alias_opt("accountName")
    account_profile_page: str | None = schema.alias_opt("accountProfilePage")


class AccountInfo(schema.Strict):
    online_account: OnlineAccountInfo | None = schema.alias_opt("OnlineAccount")


class ImplementsInfo(schema.Strict):
    body: str | None = None
    id: str | None = None
    resource: str | None = None
    title: str | None = None
    url: str | None = None


class Release(schema.Strict):
    created: str | None = None
    name: str
    revision: str | None = None
    file_release: str | None = schema.alias_opt("file-release")
    description: str | None = None
    branch: str | None = None


class ProjectStatus(schema.Strict):
    category: list[str] = schema.factory(list)
    created: str | None = None
    description: str | None = None
    programming_language: list[str] = schema.Field(alias="programming-language", default_factory=list)
    doap: str | None = None
    homepage: str
    name: str
    pmc: str | None
    shortdesc: str | None = None
    repository: list[str | dict] = schema.factory(list)
    release: list[Release] = schema.factory(list)
    bug_database: str | None = schema.alias_opt("bug-database")
    download_page: str | None = schema.alias_opt("download-page")
    license: str | None = None
    mailing_list: str | None = schema.alias_opt("mailing-list")
    maintainer: list[MaintainerInfo] = schema.factory(list)
    implements: list[ImplementsInfo] = schema.factory(list)
    same_as: str | None = schema.alias_opt("sameAs")
    developer: list[MaintainerInfo] = schema.factory(list)
    modified: str | None = None
    chair: ChairInfo | None = None
    charter: str | None = None
    vendor: str | None = None
    helper: list[HelperInfo] = schema.factory(list)
    member: list[MaintainerInfo] = schema.factory(list)
    shortname: str | None = None
    wiki: str | None = None
    account: AccountInfo | None = None
    platform: str | None = None

    @pydantic.field_validator("category", "programming_language", mode="before")
    @classmethod
    def _coerce_to_list(cls, v: object) -> list[str]:
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            return [v] if v else []
        return []


class ProjectsData(helpers.DictRoot[ProjectStatus]):
    pass


async def get_committee_data() -> dict[str, Committee]:
    """Returns the list of committees from projects.a.o."""

    async with util.create_secure_session() as session:
        async with session.get(_PROJECTS_COMMITTEE_URL) as response:
            response.raise_for_status()
            data: list = await response.json()

    return {c.get("id"): Committee.model_validate(c) for c in data}


async def get_whimsy_committee_data() -> WhimsyCommitteeData:
    """Returns the list of currently active committees."""

    async with util.create_secure_session() as session:
        async with session.get(_WHIMSY_COMMITTEE_INFO_URL) as response:
            response.raise_for_status()
            data = await response.json()

    return WhimsyCommitteeData.model_validate(data)


async def get_current_podlings_data() -> PodlingsData:
    """Returns the list of current podlings."""

    async with util.create_secure_session() as session:
        async with session.get(_PROJECTS_PODLINGS_URL) as response:
            response.raise_for_status()
            data = await response.json()
    return PodlingsData.model_validate(data)


async def get_groups_data() -> GroupsData:
    """Returns LDAP Groups with their members."""

    async with util.create_secure_session() as session:
        async with session.get(_PROJECTS_GROUPS_URL) as response:
            response.raise_for_status()
            data = await response.json()
    return GroupsData.model_validate(data)


async def get_ldap_projects_data() -> LDAPProjectsData:
    async with util.create_secure_session() as session:
        async with session.get(_WHIMSY_PROJECTS_URL) as response:
            response.raise_for_status()
            data = await response.json()

    return LDAPProjectsData.model_validate(data)


async def get_people_data() -> LDAPPeopleData:
    """Returns the full roster of ASF accounts with display names."""

    async with util.create_secure_session() as session:
        async with session.get(_WHIMSY_PEOPLE_URL) as response:
            response.raise_for_status()
            data = await response.json()

    return LDAPPeopleData.model_validate(data)


async def get_projects_data() -> ProjectsData:
    """Returns the list of projects."""

    async with util.create_secure_session() as session:
        async with session.get(_PROJECTS_PROJECTS_URL) as response:
            response.raise_for_status()
            data = await response.json()
    return ProjectsData.model_validate(data)


async def get_retired_committee_data() -> RetiredCommitteeData:
    """Returns the list of retired committees."""

    async with util.create_secure_session() as session:
        async with session.get(_WHIMSY_COMMITTEE_RETIRED_URL) as response:
            response.raise_for_status()
            data = await response.json()

    return RetiredCommitteeData.model_validate(data)


async def update_metadata() -> tuple[int, int]:
    """Update metadata from remote data sources."""

    ldap_projects = await get_ldap_projects_data()
    people = await get_people_data()
    projects = await get_projects_data()
    podlings_data = await get_current_podlings_data()
    whimsy_committees = await get_whimsy_committee_data()
    committees = await get_committee_data()

    ldap_projects_by_name: Mapping[str, LDAPProject] = {p.name: p for p in ldap_projects.projects}
    whimsy_committees_by_name: Mapping[str, WhimsyCommittee] = {c.name: c for c in whimsy_committees.committees}

    added_count = 0
    updated_count = 0

    async with db.session() as data:
        async with data.begin():
            await _update_people(data, people)

            added, updated = await _update_committees(data, ldap_projects, whimsy_committees_by_name, committees)
            added_count += added
            updated_count += updated

            added, updated = await _update_podlings(data, podlings_data, ldap_projects_by_name)
            added_count += added
            updated_count += updated

            added, updated = await _update_projects(data, projects)
            added_count += added
            updated_count += updated

            added, updated = await _update_tooling(data)
            added_count += added
            updated_count += updated

            added, updated = await _process_undiscovered(data)
            added_count += added
            updated_count += updated

    return added_count, updated_count


def _coerce_https(url: str | None) -> str | None:
    if type(url) is str:
        if "http://" in url:
            return url.replace("http", "https")
        return url
    return None


def _doap_repository_urls(items: list[str | dict]) -> list[str]:
    urls: list[str] = []
    for item in items:
        if isinstance(item, str):
            urls.append(str(_coerce_https(item)))
        elif isinstance(item, dict):
            location = item.get("location")
            if isinstance(location, str):
                urls.append(str(_coerce_https(location)))
    return urls


async def _process_undiscovered(data: db.Session) -> tuple[int, int]:
    added_count = 0
    updated_count = 0

    via = sql.validate_instrumented_attribute
    committees_without_projects = await db.Query(
        data, sqlmodel.select(sql.Committee).where(~via(sql.Committee.projects).any())
    ).all()
    # For all committees that have no associated projects
    for committee in committees_without_projects:
        if committee.key == "incubator":
            continue
        log.warning(f"Missing top level project for committee {committee.key}")
        # If a committee is missing, the following code can be activated to fix it
        # But ideally the fix should be in the upstream data source
        # project = sql.Project(
        #     name=committee.name,
        #     full_name=committee.full_name,
        #     committee=committee,
        # )
        # data.add(project)
        # added_count += 1

    return added_count, updated_count


def _project_status(pmc: sql.Committee, project_key: str, project_status: ProjectStatus) -> sql.ProjectStatus:
    if pmc.key == "attic":
        # This must come first, because attic is also a standing committee
        return sql.ProjectStatus.RETIRED
    elif ("_dormant_" in project_key) or str(project_status.name).endswith("(Dormant)"):
        return sql.ProjectStatus.DORMANT
    elif util.committee_is_standing(pmc.key):
        return sql.ProjectStatus.STANDING
    return sql.ProjectStatus.ACTIVE


async def _update_committees(
    data: db.Session,
    ldap_projects: LDAPProjectsData,
    whimsy_committees_by_name: Mapping[str, WhimsyCommittee],
    committees: dict[str, Committee],
) -> tuple[int, int]:
    added_count = 0
    updated_count = 0

    # First create PMC committees
    for project in ldap_projects.projects:
        name = project.name
        # Skip non-PMC committees
        if project.pmc is not True:
            continue

        # Get or create PMC
        committee = await data.committee(key=name).get()
        if not committee:
            committee = sql.Committee(key=name)
            data.add(committee)
            added_count += 1
        else:
            updated_count += 1

        committee.committee_members = project.owners
        committee.committers = project.members
        # We create PMCs for now
        committee.is_podling = False
        whimsy_info = whimsy_committees_by_name.get(name)
        if whimsy_info:
            committee.name = whimsy_info.display_name
        committee_info = committees.get(name)
        if committee_info:
            committee.charter = committee_info.charter

        committee.updated = datetime.datetime.now(datetime.UTC)
        committee.updated_by = "bootstrap"
        updated_count += 1

    return added_count, updated_count


async def _update_people(data: db.Session, people: LDAPPeopleData) -> None:
    # Upsert all ASF accounts eagerly so roster lookups always find a name.
    # Batched to stay under SQLite's bind-parameter limit (~32766).
    rows = [
        {"asfuid": uid, "name": entry.name, "preferences": sql.UserPreferencesEntry()}
        for uid, entry in people.people.items()
    ]
    for i in range(0, len(rows), 5000):
        stmt = sqlite.insert(sql.User).values(rows[i : i + 5000])
        stmt = stmt.on_conflict_do_update(
            index_elements=["asfuid"],
            set_={"name": stmt.excluded.name},
        )
        await data.execute(stmt)


async def _update_podlings(
    data: db.Session, podlings_data: PodlingsData, ldap_projects_by_name: Mapping[str, LDAPProject]
) -> tuple[int, int]:
    added_count = 0
    updated_count = 0

    # Then add PPMCs and their associated project (podlings)
    for podling_name, podling_data in podlings_data:
        # Get or create PPMC
        ppmc = await data.committee(key=podling_name).get()
        if not ppmc:
            ppmc = sql.Committee(key=podling_name, is_podling=True)
            data.add(ppmc)
            added_count += 1
        else:
            updated_count += 1

        # We create a PPMC
        ppmc.is_podling = True
        ppmc.name = podling_data.name.removesuffix("(Incubating)").removeprefix("Apache").strip()
        podling_project = ldap_projects_by_name.get(podling_name)
        if podling_project is not None:
            ppmc.committee_members = podling_project.owners
            ppmc.committers = podling_project.members
        else:
            log.warning(f"could not find ldap data for podling {podling_name}")

        ppmc.updated = datetime.datetime.now(datetime.UTC)
        ppmc.updated_by = "bootstrap"

        podling = await data.project(key=podling_name).get()
        if not podling:
            # Create the associated podling project
            podling = sql.Project(key=podling_name, name=podling_data.name, committee=ppmc)
            data.add(podling)
            added_count += 1
        else:
            updated_count += 1

        podling.name = podling_data.name.removesuffix(" (Incubating)")
        podling.committee = ppmc
        podling.updated = datetime.datetime.now(datetime.UTC)
        podling.updated_by = "bootstrap"
        # TODO: Why did the type checkers not detect this?
        # podling.is_podling = True

    return added_count, updated_count


async def _update_projects(data: db.Session, projects: ProjectsData) -> tuple[int, int]:
    added_count = 0
    updated_count = 0

    # Add projects and associate them with the right PMC
    for project_key, project_status in projects.items():
        # FIXME: this is a quick workaround for inconsistent data wrt webservices PMC / projects
        #        the PMC seems to be identified by the key ws, but the associated projects use webservices
        if project_key.startswith("webservices-"):
            project_key = project_key.replace("webservices-", "ws-")
            project_status.pmc = "ws"
        # Fixup data from accumulo-fluo_recipes
        if "_" in project_key:
            project_key = project_key.replace("_", "-")

        # TODO: Annotator is in both projects and ldap_projects
        # The projects version is called "incubator-annotator", with "incubator" as its pmc
        # This is not detected by us as incubating, because we create those above
        # ("Create the associated podling project")
        # Since the Annotator project is in ldap_projects, we can just skip it here
        # Originally reported in https://github.com/apache/tooling-trusted-releases/issues/35
        # Ideally it would be removed from the upstream data source, which is:
        # https://projects.apache.org/json/foundation/projects.json
        if project_key == "incubator-annotator":
            continue

        if project_status.pmc is None:
            log.warning(f"project {project_key} has no PMC, skipping")
            continue

        pmc = await data.committee(key=project_status.pmc).get()
        if not pmc:
            log.warning(f"could not find PMC for project {project_key}: {project_status.pmc}")
            continue

        project_model = await data.project(key=project_key).get()
        if project_model:
            continue

        # Check whether the project is retired, whether temporarily or otherwise
        status = _project_status(pmc, project_key, project_status)
        project_model = sql.Project(key=project_key, committee=pmc, status=status)
        data.add(project_model)
        added_count += 1

        # Pass the project name through the validator
        safe.ProjectKey(project_model.key)
        project_model.name = str(project_status.name)
        project_model.categories = ", ".join(project_status.category) or None
        project_model.description = project_status.description
        project_model.programming_languages = ", ".join(project_status.programming_language) or None

        project_model.short_description = project_status.shortdesc
        project_model.homepage = _coerce_https(project_status.homepage)
        project_model.download_page = _coerce_https(project_status.download_page)
        project_model.bug_database = _coerce_https(project_status.bug_database)
        project_model.mailing_lists = _coerce_https(project_status.mailing_list)
        project_model.repositories = _doap_repository_urls(project_status.repository)
        # Not coercing standards URLs as these are outside ASF control
        project_model.standards = [str(impl.url) for impl in project_status.implements if impl.url]
        project_model.updated = datetime.datetime.now(datetime.UTC)
        project_model.updated_by = "bootstrap"

    return added_count, updated_count


async def _update_tooling(data: db.Session) -> tuple[int, int]:
    added_count = 0
    updated_count = 0

    # Tooling is not a committee
    # We add a special entry for Tooling, pretending to be a PMC, for debugging and testing
    tooling_committee = await data.committee(key="tooling").get()
    if not tooling_committee:
        tooling_committee = sql.Committee(key="tooling", name="Tooling")
        data.add(tooling_committee)
        tooling_project = sql.Project(key="tooling", name="Apache Tooling", committee=tooling_committee)
        data.add(tooling_project)
        added_count += 1
    else:
        updated_count += 1

    additional = config.get().TOOLING_USERS_ADDITIONAL
    if additional:
        extra = set(additional.split(","))
    else:
        extra = set()

    # Update Tooling PMC data
    tooling_users = list(await ldap.fetch_tooling_users(extra))
    tooling_committee.committee_members = tooling_users
    tooling_committee.committers = tooling_users
    tooling_committee.release_managers = tooling_users
    tooling_committee.is_podling = False
    tooling_committee.updated = datetime.datetime.now(datetime.UTC)
    tooling_committee.updated_by = "bootstrap"

    return added_count, updated_count
