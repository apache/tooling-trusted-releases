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
    ("DISCLAIMER", "Podling incubation disclaimer", {"announce"}),
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

    project_display_name = release.project.short_display_name if release.project else str(options.project_key)
    download_url = paths.committee_downloads_url(host, committee)
    if options.download_path_suffix is not None:
        download_url += f"/{options.download_path_suffix!s}"
    download_url += "/"

    # Perform substitutions in the subject
    subject = subject.replace("{{PROJECT}}", project_display_name)
    subject = subject.replace("{{VERSION}}", str(options.version_key))

    # Perform substitutions in the body
    body = body.replace("{{COMMITTEE}}", committee.display_name)
    body = body.replace("{{DISCLAIMER}}", _podling_disclaimer(release.project, committee))
    body = body.replace("{{DOWNLOAD_URL}}", download_url)
    body = body.replace("{{PROJECT}}", project_display_name)
    body = body.replace("{{REVISION}}", revision_number)
    body = body.replace("{{TAG}}", revision_tag)
    body = body.replace("{{VERSION}}", str(options.version_key))
    body = body.replace("{{YOUR_ASF_ID}}", options.asfuid)
    body = body.replace("{{YOUR_FULL_NAME}}", options.fullname)

    return subject, body


async def announce_release_subject_default(project_key: safe.ProjectKey) -> str:
    async with db.session() as data:
        project = await data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_key} not found"))

    return project.policy_announce_release_subject


def announce_subject_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "announce_subject" in contexts]


def announce_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "announce" in contexts]


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

    markdown = markdown.replace("{{COMMITTEE}}", committee.display_name)
    markdown = markdown.replace("{{PROJECT}}", project.short_display_name)
    markdown = markdown.replace("{{REVIEW_URL}}", review_url)
    markdown = markdown.replace("{{REVISION}}", revision_number)
    markdown = markdown.replace("{{TAG}}", revision_tag)
    markdown = markdown.replace("{{VERSION}}", str(version_key))
    return markdown


def checklist_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "checklist" in contexts]


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
    project_display_name = release.project.short_display_name if release.project else str(options.project_key)
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

    if checklist_content and release.project:
        checklist_content = checklist_body(
            checklist_content,
            project=release.project,
            version_key=options.version_key,
            committee=committee,
            revision=revision,
        )

    # Perform substitutions in the subject
    subject = subject.replace("{{COMMITTEE}}", committee.display_name)
    subject = subject.replace("{{PROJECT}}", str(project_display_name))
    subject = subject.replace("{{REVISION}}", revision_number)
    subject = subject.replace("{{TAG}}", revision_tag)
    subject = subject.replace("{{VERSION}}", str(options.version_key))
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
    body = body.replace("{{VERSION}}", str(options.version_key))
    body = body.replace("{{YOUR_ASF_ID}}", options.asfuid)
    body = body.replace("{{YOUR_FULL_NAME}}", options.fullname)

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
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "vote_subject" in contexts]


def vote_template_variables() -> list[tuple[str, str]]:
    return [(name, desc) for (name, desc, contexts) in TEMPLATE_VARIABLES if "vote" in contexts]


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
