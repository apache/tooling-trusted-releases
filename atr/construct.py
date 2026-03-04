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
from typing import Literal

import aiofiles.os
import quart

import atr.config as config
import atr.db as db
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.util as util

type Context = Literal["announce", "announce_subject", "checklist", "vote", "vote_subject"]

TEMPLATE_VARIABLES: list[tuple[str, str, set[Context]]] = [
    ("CHECKLIST_URL", "URL to the release checklist", {"vote"}),
    ("COMMITTEE", "Committee display name", {"announce", "checklist", "vote", "vote_subject"}),
    ("DOWNLOAD_URL", "URL to download the release", {"announce"}),
    ("DURATION", "Vote duration in hours", {"vote"}),
    ("KEYS_FILE", "URL to the KEYS file", {"vote"}),
    ("PROJECT", "Project display name", {"announce", "announce_subject", "checklist", "vote", "vote_subject"}),
    ("RELEASE_CHECKLIST", "Release checklist content", {"vote"}),
    ("REVIEW_URL", "URL to review the release", {"checklist", "vote"}),
    ("REVISION", "Revision number", {"announce", "checklist", "vote", "vote_subject"}),
    ("TAG", "Revision tag, if set", {"announce", "checklist", "vote", "vote_subject"}),
    ("VERSION", "Version name", {"announce", "announce_subject", "checklist", "vote", "vote_subject"}),
    ("VOTE_ENDS_UTC", "Vote end date and time in UTC", {"vote_subject"}),
    ("YOUR_ASF_ID", "Your Apache UID", {"announce", "vote"}),
    ("YOUR_FULL_NAME", "Your full name", {"announce", "vote"}),
]


@dataclasses.dataclass
class AnnounceReleaseOptions:
    asfuid: str
    fullname: str
    project_name: safe.ProjectName
    version_name: safe.VersionName
    revision_number: str


@dataclasses.dataclass
class StartVoteOptions:
    asfuid: str
    fullname: str
    project_name: safe.ProjectName
    version_name: safe.VersionName
    revision_number: str
    vote_duration: int


async def announce_release_default(project_name: safe.ProjectName) -> str:
    async with db.session() as data:
        project = await data.project(
            name=str(project_name), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_name} not found"))

    return project.policy_announce_release_template


async def announce_release_subject_and_body(
    subject: str, body: str, options: AnnounceReleaseOptions
) -> tuple[str, str]:
    # NOTE: The present module is imported by routes
    # Therefore this must be done here to avoid a circular import
    import atr.get as get

    try:
        host = quart.request.host
    except RuntimeError:
        host = config.get().APP_HOST

    async with db.session() as data:
        release = await data.release(
            project_name=str(options.project_name),
            version=str(options.version_name),
            _project=True,
            _committee=True,
            phase=sql.ReleasePhase.RELEASE_PREVIEW,
        ).demand(RuntimeError(f"Release {options.project_name} {options.version_name} not found"))
        if not release.committee:
            raise RuntimeError(f"Release {options.project_name} {options.version_name} has no committee")
        committee = release.committee

        revision = await data.revision(release_name=release.name, number=options.revision_number).get()
        revision_number = revision.number if revision else ""
        revision_tag = revision.tag if (revision and revision.tag) else ""

    project_display_name = release.project.short_display_name if release.project else str(options.project_name)

    routes_file_selected = get.file.selected
    download_path = util.as_url(
        routes_file_selected, project_name=str(options.project_name), version_name=str(options.version_name)
    )
    # TODO: This download_url should probably be for the proxy download directory, not the ATR view
    download_url = f"https://{host}{download_path}"

    # Perform substitutions in the subject
    subject = subject.replace("{{PROJECT}}", project_display_name)
    subject = subject.replace("{{VERSION}}", str(options.version_name))

    # Perform substitutions in the body
    body = body.replace("{{COMMITTEE}}", committee.display_name)
    body = body.replace("{{DOWNLOAD_URL}}", download_url)
    body = body.replace("{{PROJECT}}", project_display_name)
    body = body.replace("{{REVISION}}", revision_number)
    body = body.replace("{{TAG}}", revision_tag)
    body = body.replace("{{VERSION}}", str(options.version_name))
    body = body.replace("{{YOUR_ASF_ID}}", options.asfuid)
    body = body.replace("{{YOUR_FULL_NAME}}", options.fullname)

    return subject, body


async def announce_release_subject_default(project_name: safe.ProjectName) -> str:
    async with db.session() as data:
        project = await data.project(
            name=str(project_name), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_name} not found"))

    return project.policy_announce_release_subject


def announce_subject_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "announce_subject" in contexts]


def announce_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "announce" in contexts]


def checklist_body(
    markdown: str,
    project: sql.Project,
    version_name: safe.VersionName,
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
    review_path = util.as_url(vote.selected, project_name=project.name, version_name=version_name)
    review_url = f"https://{host}{review_path}"

    markdown = markdown.replace("{{COMMITTEE}}", committee.display_name)
    markdown = markdown.replace("{{PROJECT}}", project.short_display_name)
    markdown = markdown.replace("{{REVIEW_URL}}", review_url)
    markdown = markdown.replace("{{REVISION}}", revision_number)
    markdown = markdown.replace("{{TAG}}", revision_tag)
    markdown = markdown.replace("{{VERSION}}", str(version_name))
    return markdown


def checklist_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "checklist" in contexts]


async def start_vote_default(project_name: safe.ProjectName) -> str:
    async with db.session() as data:
        project = await data.project(
            name=str(project_name), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_name} not found"))

    return project.policy_start_vote_template


async def start_vote_subject_and_body(subject: str, body: str, options: StartVoteOptions) -> tuple[str, str]:
    import atr.get.checklist as checklist
    import atr.get.vote as vote

    async with db.session() as data:
        # Do not limit by phase, as it may be at RELEASE_CANDIDATE already
        release = await data.release(
            project_name=str(options.project_name),
            version=str(options.version_name),
            _project=True,
            _committee=True,
        ).demand(RuntimeError(f"Release {options.project_name} {options.version_name} not found"))
        if not release.committee:
            raise RuntimeError(f"Release {options.project_name} {options.version_name} has no committee")
        committee = release.committee

        revision = await data.revision(release_name=release.name, number=options.revision_number).get()
        revision_number = revision.number if revision else ""
        revision_tag = revision.tag if (revision and revision.tag) else ""

    try:
        host = quart.request.host
    except RuntimeError:
        host = config.get().APP_HOST

    checklist_path = util.as_url(
        checklist.selected, project_name=str(options.project_name), version_name=str(options.version_name)
    )
    checklist_url = f"https://{host}{checklist_path}"
    review_path = util.as_url(
        vote.selected, project_name=str(options.project_name), version_name=str(options.version_name)
    )
    review_url = f"https://{host}{review_path}"
    project_display_name = release.project.short_display_name if release.project else str(options.project_name)
    vote_end = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=options.vote_duration)
    vote_end_str = f"{vote_end.day} {vote_end.strftime('%b %H:%M')} UTC"

    # NOTE: The /downloads/ directory is served by the proxy front end, not by ATR
    # Therefore there is no route handler, so we have to construct the URL manually
    keys_file = None
    if committee.is_podling:
        keys_file_path = paths.get_downloads_dir() / "incubator" / committee.name / "KEYS"
        if await aiofiles.os.path.isfile(keys_file_path):
            keys_file = f"https://{host}/downloads/incubator/{committee.name}/KEYS"
    else:
        keys_file_path = paths.get_downloads_dir() / committee.name / "KEYS"
        if await aiofiles.os.path.isfile(keys_file_path):
            keys_file = f"https://{host}/downloads/{committee.name}/KEYS"

    checklist_content = ""
    async with db.session() as data:
        release_policy = await db.get_project_release_policy(data, options.project_name)
        if release_policy:
            checklist_content = release_policy.release_checklist or ""

    if checklist_content and release.project:
        checklist_content = checklist_body(
            checklist_content,
            project=release.project,
            version_name=options.version_name,
            committee=committee,
            revision=revision,
        )

    # Perform substitutions in the subject
    subject = subject.replace("{{COMMITTEE}}", committee.display_name)
    subject = subject.replace("{{PROJECT}}", str(project_display_name))
    subject = subject.replace("{{REVISION}}", revision_number)
    subject = subject.replace("{{TAG}}", revision_tag)
    subject = subject.replace("{{VERSION}}", str(options.version_name))
    subject = subject.replace("{{VOTE_ENDS_UTC}}", vote_end_str)

    # Perform substitutions in the body
    # TODO: Handle the DURATION == 0 case
    body = body.replace("{{CHECKLIST_URL}}", checklist_url)
    body = body.replace("{{COMMITTEE}}", committee.display_name)
    body = body.replace("{{DURATION}}", str(options.vote_duration))
    body = body.replace("{{KEYS_FILE}}", keys_file or "(Sorry, the KEYS file is missing!)")
    body = body.replace("{{PROJECT}}", str(project_display_name))
    body = body.replace("{{RELEASE_CHECKLIST}}", checklist_content)
    body = body.replace("{{REVIEW_URL}}", review_url)
    body = body.replace("{{REVISION}}", revision_number)
    body = body.replace("{{TAG}}", revision_tag)
    body = body.replace("{{VERSION}}", str(options.version_name))
    body = body.replace("{{YOUR_ASF_ID}}", options.asfuid)
    body = body.replace("{{YOUR_FULL_NAME}}", options.fullname)

    return subject, body


async def start_vote_subject_default(project_name: safe.ProjectName) -> str:
    async with db.session() as data:
        project = await data.project(
            name=str(project_name), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_name} not found"))

    return project.policy_start_vote_subject


def template_hash(template: str) -> str:
    """Compute a hash of a template for verification."""
    return hashlib.sha256(template.encode()).hexdigest()


def vote_subject_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "vote_subject" in contexts]


def vote_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "vote" in contexts]
