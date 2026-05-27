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

import dataclasses
import datetime
import hashlib
import re
from collections.abc import Mapping
from typing import Final, Literal, TypedDict, overload

import aiofiles.os
import quart

import atr.config as config
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.util as util

type Context = Literal["announce", "announce_subject", "checklist", "finish_vote", "vote", "vote_subject"]


class _AnnounceSubjectValues(TypedDict):
    PROJECT_NAME: str
    VERSION: str


class _AnnounceValues(TypedDict):
    BUG_DATABASE: str
    COMMITTEE: str
    DISCLAIMER: str
    DOWNLOAD_PAGE: str
    DOWNLOAD_URL: str
    HOMEPAGE: str
    LIFECYCLE_PAGE: str
    MAILING_LISTS: str
    PROJECT_NAME: str
    PROJECT_KEY: str
    REPOSITORY: str
    REVISION: str
    TAG: str
    VERSION: str
    YOUR_ASF_ID: str
    YOUR_FULL_NAME: str


class _ChecklistValues(TypedDict):
    COMMITTEE: str
    HOMEPAGE: str
    PROJECT_NAME: str
    PROJECT_KEY: str
    REVIEW_URL: str
    REVISION: str
    SHORT_DESCRIPTION: str
    TAG: str
    VERSION: str


class _FinishVoteValues(TypedDict):
    ATR_TALLY: str
    COMMITTEE: str
    OUTCOME: str
    PROJECT_NAME: str
    VERSION: str
    YOUR_ASF_ID: str
    YOUR_FULL_NAME: str


class _VoteSubjectValues(TypedDict):
    COMMITTEE: str
    PROJECT_NAME: str
    REVISION: str
    TAG: str
    VERSION: str
    VOTE_ENDS_UTC: str


class _VoteValues(TypedDict):
    BUG_DATABASE: str
    CHECKLIST_URL: str
    COMMITTEE: str
    DURATION: str
    HOMEPAGE: str
    KEYS_FILE: str
    PROJECT_NAME: str
    PROJECT_KEY: str
    RELEASE_CHECKLIST: str
    REPOSITORY: str
    REVIEW_URL: str
    REVISION: str
    TAG: str
    VERSION: str
    YOUR_ASF_ID: str
    YOUR_FULL_NAME: str


TEMPLATE_DESCRIPTIONS: Final[dict[str, str]] = {
    "ATR_TALLY": "Vote tally block - URL, ballots, counts",
    "BUG_DATABASE": "Bug database URL",
    "CHECKLIST_URL": "URL to the release checklist",
    "COMMITTEE": "Committee name",
    "DISCLAIMER": "Podling incubation disclaimer",
    "DOWNLOAD_PAGE": "Download page URL",
    "DOWNLOAD_URL": "URL to download the release",
    "DURATION": "Vote duration in hours",
    "HOMEPAGE": "Homepage URL",
    "KEYS_FILE": "URL to the KEYS file",
    "LIFECYCLE_PAGE": "Lifecycle page URL",
    "MAILING_LISTS": "Mailing lists page URL",
    "OUTCOME": "Vote outcome - 'passed' or 'failed'",
    "PROJECT_NAME": "Project name",
    "PROJECT_KEY": "ATR key for the project",
    "RELEASE_CHECKLIST": "Release checklist content",
    "REPOSITORY": "Source repository URLs",
    "REVIEW_URL": "URL to review the release",
    "REVISION": "Revision number",
    "SHORT_DESCRIPTION": "Short project description",
    "TAG": "Revision tag, if set",
    "VERSION": "Version name",
    "VOTE_ENDS_UTC": "Vote end date and time in UTC",
    "YOUR_ASF_ID": "Your Apache UID",
    "YOUR_FULL_NAME": "Your full name",
}


@dataclasses.dataclass
class AnnounceReleaseOptions:
    asfuid: str
    fullname: str
    project_key: safe.ProjectKey
    version_key: safe.VersionKey
    revision_number: safe.RevisionNumber
    download_path_suffix: safe.RelPath | None = None


@dataclasses.dataclass
class StartVoteOptions:
    asfuid: str
    fullname: str
    project_key: safe.ProjectKey
    version_key: safe.VersionKey
    revision_number: safe.RevisionNumber
    vote_duration: int


async def announce_release_default(project_key: safe.ProjectKey) -> str:
    async with db.session() as data:
        project = await data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_key} not found"))

    return project.policy_announce_release_template


async def announce_release_subject_and_body(
    subject: str, body: str, options: AnnounceReleaseOptions
) -> tuple[str, str]:
    try:
        host = quart.request.host
    except RuntimeError:
        host = config.get().APP_HOST

    async with db.session() as data:
        release = await data.release(
            project_key=str(options.project_key),
            version=str(options.version_key),
            _project=True,
            _committee=True,
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
        ).demand(RuntimeError(f"Release {options.project_key} {options.version_key} not found"))
        if not release.committee:
            raise RuntimeError(f"Release {options.project_key} {options.version_key} has no committee")
        committee = release.committee

        revision = await data.revision(release_key=release.key, number=str(options.revision_number)).get()
        revision_number = revision.number if revision else ""
        revision_tag = revision.tag if (revision and revision.tag) else ""

    project = release.project
    project_display_name = project.short_display_name if project else str(options.project_key)
    download_url = paths.committee_downloads_dist_url(committee, host)
    if options.download_path_suffix is not None:
        download_url += f"/{options.download_path_suffix!s}"
    download_url += "/"

    values: _AnnounceValues = {
        "BUG_DATABASE": project.bug_database or "",
        "COMMITTEE": committee.display_name,
        "DISCLAIMER": _podling_disclaimer(project, committee),
        "DOWNLOAD_PAGE": project.download_page or "",
        "DOWNLOAD_URL": download_url,
        "HOMEPAGE": project.homepage or "",
        "LIFECYCLE_PAGE": project.lifecycle_page or "",
        "MAILING_LISTS": project.mailing_lists or "",
        "PROJECT_NAME": project_display_name,
        "PROJECT_KEY": project.key,
        "REPOSITORY": "\n".join(project.repositories),
        "REVISION": revision_number,
        "TAG": revision_tag,
        "VERSION": str(options.version_key),
        "YOUR_ASF_ID": options.asfuid,
        "YOUR_FULL_NAME": options.fullname,
    }
    subject = _substitute(subject, values, "announce_subject")
    body = _substitute(body, values, "announce")
    return subject, body


async def announce_release_subject_default(project_key: safe.ProjectKey) -> str:
    async with db.session() as data:
        project = await data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_key} not found"))

    return project.policy_announce_release_subject


def announce_subject_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(_AnnounceSubjectValues.__required_keys__)]


def announce_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(_AnnounceValues.__required_keys__)]


def checklist_body(
    markdown: str,
    project: sql.Project,
    version_key: safe.VersionKey,
    committee: sql.Committee,
    revision: sql.Revision | None,
) -> str:
    import atr.get.vote as vote

    try:
        host = quart.request.host
    except RuntimeError:
        host = config.get().APP_HOST

    revision_number = revision.number if revision else ""
    revision_tag = revision.tag if (revision and revision.tag) else ""
    review_path = util.as_url(vote.selected, project_key=project.key, version_key=version_key)
    review_url = f"https://{host}{review_path}"

    values: _ChecklistValues = {
        "COMMITTEE": committee.display_name,
        "HOMEPAGE": project.homepage or "",
        "PROJECT_NAME": project.short_display_name,
        "PROJECT_KEY": project.key,
        "REVIEW_URL": review_url,
        "REVISION": revision_number,
        "SHORT_DESCRIPTION": project.short_description or "",
        "TAG": revision_tag,
        "VERSION": str(version_key),
    }
    return _substitute(markdown, values, "checklist")


def checklist_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(_ChecklistValues.__required_keys__)]


def finish_vote_body(body: str, values: _FinishVoteValues) -> str:
    return re.sub(r"\n{3,}", "\n\n", _substitute(body, values, "finish_vote"))


def finish_vote_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(_FinishVoteValues.__required_keys__)]


async def start_vote_default(project_key: safe.ProjectKey) -> str:
    async with db.session() as data:
        project = await data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_key} not found"))

    return project.policy_start_vote_template


async def start_vote_subject_and_body(subject: str, body: str, options: StartVoteOptions) -> tuple[str, str]:
    import atr.get.checklist as checklist
    import atr.get.vote as vote

    async with db.session() as data:
        # Do not limit by phase, as it may be at RELEASE_CANDIDATE already
        release = await data.release(
            project_key=str(options.project_key),
            version=str(options.version_key),
            _project=True,
            _committee=True,
        ).demand(RuntimeError(f"Release {options.project_key} {options.version_key} not found"))
        if not release.committee:
            raise RuntimeError(f"Release {options.project_key} {options.version_key} has no committee")
        committee = release.committee

        revision = await data.revision(release_key=release.key, number=str(options.revision_number)).get()
        revision_number = revision.number if revision else ""
        revision_tag = revision.tag if (revision and revision.tag) else ""

    try:
        host = quart.request.host
    except RuntimeError:
        host = config.get().APP_HOST

    checklist_path = util.as_url(
        checklist.selected, project_key=str(options.project_key), version_key=str(options.version_key)
    )
    checklist_url = f"https://{host}{checklist_path}"
    review_path = util.as_url(vote.selected, project_key=str(options.project_key), version_key=str(options.version_key))
    review_url = f"https://{host}{review_path}"
    project = release.project
    project_display_name = project.short_display_name if project else str(options.project_key)
    vote_end = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=options.vote_duration)
    vote_end_str = f"{vote_end.day} {vote_end.strftime('%b %H:%M')} UTC"

    # NOTE: The /downloads/ directory is served by the proxy front end, not by ATR
    # Therefore there is no route handler, so we have to construct the URL manually
    keys_file = None
    keys_file_path = paths.committee_downloads_dir(committee) / "KEYS"
    if await aiofiles.os.path.isfile(keys_file_path):
        keys_file = f"{paths.committee_downloads_url(host, committee)}/KEYS"

    checklist_content = ""
    async with db.session() as data:
        release_policy = await db.get_project_release_policy(data, options.project_key)
        if release_policy:
            checklist_content = release_policy.release_checklist or ""

    if checklist_content and project:
        checklist_content = checklist_body(
            checklist_content,
            project=project,
            version_key=options.version_key,
            committee=committee,
            revision=revision,
        )

    subject_values: _VoteSubjectValues = {
        "COMMITTEE": committee.display_name,
        "PROJECT_NAME": project_display_name,
        "REVISION": revision_number,
        "TAG": revision_tag,
        "VERSION": str(options.version_key),
        "VOTE_ENDS_UTC": vote_end_str,
    }
    # TODO: Handle the DURATION == 0 case
    body_values: _VoteValues = {
        "BUG_DATABASE": project.bug_database or "",
        "CHECKLIST_URL": checklist_url,
        "COMMITTEE": committee.display_name,
        "DURATION": str(options.vote_duration),
        "HOMEPAGE": project.homepage or "",
        "KEYS_FILE": keys_file or "(Sorry, the KEYS file is missing!)",
        "PROJECT_NAME": project_display_name,
        "PROJECT_KEY": project.key,
        "RELEASE_CHECKLIST": checklist_content,
        "REPOSITORY": "\n".join(project.repositories),
        "REVIEW_URL": review_url,
        "REVISION": revision_number,
        "TAG": revision_tag,
        "VERSION": str(options.version_key),
        "YOUR_ASF_ID": options.asfuid,
        "YOUR_FULL_NAME": options.fullname,
    }
    subject = _substitute(subject, subject_values, "vote_subject")
    body = _substitute(body, body_values, "vote")
    return subject, body


async def start_vote_subject_default(project_key: safe.ProjectKey) -> str:
    async with db.session() as data:
        project = await data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_key} not found"))

    return project.policy_start_vote_subject


def template_hash(template: str) -> str:
    """Compute a hash of a template for verification."""
    return hashlib.sha256(template.encode()).hexdigest()


def vote_subject_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(_VoteSubjectValues.__required_keys__)]


def vote_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(_VoteValues.__required_keys__)]


def _podling_disclaimer(project: sql.Project, committee: sql.Committee) -> str:
    if not committee.is_podling:
        return ""
    project_name = project.name or str(project.key)
    return (
        f"\nDISCLAIMER: Apache {project_name} is an effort undergoing "
        "incubation at The Apache Software Foundation (ASF), sponsored "
        "by the Apache Incubator. Incubation is required of all newly "
        "accepted projects until a further review indicates that the "
        "infrastructure, communications, and decision making process "
        "have stabilized in a manner consistent with other successful "
        "ASF projects. While incubation status is not necessarily a "
        "reflection of the completeness or stability of the code, it "
        "does indicate that the project has yet to be fully endorsed "
        "by the ASF.\n"
    )


@overload
def _substitute(text: str, values: _AnnounceSubjectValues, context: Literal["announce_subject"]) -> str: ...
@overload
def _substitute(text: str, values: _AnnounceValues, context: Literal["announce"]) -> str: ...
@overload
def _substitute(text: str, values: _ChecklistValues, context: Literal["checklist"]) -> str: ...
@overload
def _substitute(text: str, values: _FinishVoteValues, context: Literal["finish_vote"]) -> str: ...
@overload
def _substitute(text: str, values: _VoteSubjectValues, context: Literal["vote_subject"]) -> str: ...
@overload
def _substitute(text: str, values: _VoteValues, context: Literal["vote"]) -> str: ...
def _substitute(text: str, values: Mapping[str, object], context: Context) -> str:
    _ = context  # marks as unused for pyright - we're using the value to pick the right overload
    if not values:
        return text
    names = "|".join(re.escape(name) for name in values)
    pattern = re.compile(r"\{\{(" + names + r")\}\}")
    return pattern.sub(lambda match: str(values[match.group(1)]), text)
