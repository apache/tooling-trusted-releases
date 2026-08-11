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

import atr.config as config
import atr.db as db
import atr.mail as mail
import atr.models.args as args
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.util as util

type Context = Literal["announce", "announce_subject", "checklist", "finish_vote", "vote", "vote_subject"]

# The list that hears about every release, whether ATR made it or the watcher spotted it
_RELEASES_LIST_ADDRESS: Final[str] = "releases@tooling.apache.org"


class _AnnounceSubjectValues(TypedDict):
    PROJECT_NAME: str
    VERSION: str


class _AnnounceValues(TypedDict):
    BUG_DATABASE: str
    COMMITTEE: str
    DOWNLOAD_PAGE: str
    DOWNLOAD_URL: str
    HOMEPAGE: str
    LIFECYCLE_PAGE: str
    MAILING_LISTS: str
    PODLING_DISCLAIMER: str
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
    COMMIT: str
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


ANNOUNCE_SUBJECT_VARIABLE_NAMES: Final[frozenset[str]] = frozenset(_AnnounceSubjectValues.__required_keys__)
ANNOUNCE_VARIABLE_NAMES: Final[frozenset[str]] = frozenset(_AnnounceValues.__required_keys__)
CHECKLIST_VARIABLE_NAMES: Final[frozenset[str]] = frozenset(_ChecklistValues.__required_keys__)
FINISH_VOTE_VARIABLE_NAMES: Final[frozenset[str]] = frozenset(_FinishVoteValues.__required_keys__)
VOTE_SUBJECT_VARIABLE_NAMES: Final[frozenset[str]] = frozenset(_VoteSubjectValues.__required_keys__)
VOTE_VARIABLE_NAMES: Final[frozenset[str]] = frozenset(_VoteValues.__required_keys__)

TEMPLATE_DESCRIPTIONS: Final[dict[str, str]] = {
    "ATR_TALLY": "Vote tally block - URL, ballots, counts",
    "BUG_DATABASE": "Bug database URL",
    "CHECKLIST_URL": "URL to the release checklist",
    "COMMIT": "Source commit hash, if recorded",
    "COMMITTEE": "Committee name",
    "DOWNLOAD_PAGE": "Download page URL",
    "DOWNLOAD_URL": "URL to download the release",
    "DURATION": "Vote duration in hours",
    "HOMEPAGE": "Homepage URL",
    "KEYS_FILE": "URL to the KEYS file",
    "LIFECYCLE_PAGE": "Lifecycle page URL",
    "MAILING_LISTS": "Mailing lists page URL",
    "OUTCOME": "Vote outcome - 'passed' or 'failed'",
    "PODLING_DISCLAIMER": "Podling incubation disclaimer",
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


START_VOTE_EXPEDITED_DEFAULT: Final[str] = """Hello {{COMMITTEE}},

I'd like to call an expedited vote on releasing the following artifacts as
Apache {{PROJECT_NAME}} {{VERSION}}. This vote is being conducted using an
Alpha version of the Apache Trusted Releases (ATR) platform.
Please report any bugs or issues to the ASF Tooling team.

The release candidate page, including downloads, can be found at:

  {{REVIEW_URL}}

The release artifacts are signed with one or more OpenPGP keys from:

  {{KEYS_FILE}}

Please review the release candidate and cast your vote on ATR at the URL
above. Votes are recorded by ATR, so replies to this email are not counted.

This is an expedited vote. There is no minimum voting period, and a PMC
member will resolve it manually once it has the required binding +1 votes.

{{RELEASE_CHECKLIST}}
Thanks,
{{YOUR_FULL_NAME}} ({{YOUR_ASF_ID}})
"""


@dataclasses.dataclass
class AnnounceReleaseOptions:
    asfuid: str
    fullname: str
    project_key: safe.ProjectKey
    version_key: safe.VersionKey
    revision_number: safe.RevisionNumber


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
    async with db.session() as data:
        release = await data.release(
            project_key=str(options.project_key),
            version=str(options.version_key),
            _project=True,
            _committee=True,
            _project_release_policy=True,
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
    download_relpath = paths.committee_dist_relpath(committee, effective_download_path_suffix(release))
    download_url = f"{paths.downloads_url(download_relpath)}/"

    values: _AnnounceValues = {
        "BUG_DATABASE": project.bug_database or "",
        "COMMITTEE": committee.display_name,
        "DOWNLOAD_PAGE": project.download_page or "",
        "DOWNLOAD_URL": download_url,
        "HOMEPAGE": project.homepage or "",
        "LIFECYCLE_PAGE": project.lifecycle_page or "",
        "MAILING_LISTS": project.mailing_lists or "",
        "PODLING_DISCLAIMER": _podling_disclaimer(project, committee),
        "PROJECT_NAME": project_display_name,
        "PROJECT_KEY": project.key,
        "REPOSITORY": "\n".join(project.repositories),
        "REVISION": revision_number,
        "TAG": revision_tag,
        "VERSION": str(options.version_key),
        "YOUR_ASF_ID": options.asfuid,
        "YOUR_FULL_NAME": options.fullname,
    }
    subject_values: _AnnounceSubjectValues = {
        "PROJECT_NAME": project_display_name,
        "VERSION": str(options.version_key),
    }
    subject = _substitute(subject, subject_values, "announce_subject")
    body = _substitute(body, values, "announce")
    return subject, body


async def announce_release_subject_default(project_key: safe.ProjectKey) -> str:
    async with db.session() as data:
        project = await data.project(
            key=str(project_key), status=sql.ProjectStatus.ACTIVE, _release_policy=True
        ).demand(RuntimeError(f"Project {project_key} not found"))

    return project.policy_announce_release_subject


def announce_subject_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(ANNOUNCE_SUBJECT_VARIABLE_NAMES)]


def announce_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(ANNOUNCE_VARIABLE_NAMES)]


def checklist_body(
    markdown: str,
    project: sql.Project,
    version_key: safe.VersionKey,
    committee: sql.Committee,
    revision: sql.Revision | None,
) -> str:
    import atr.get.vote as vote

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
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(CHECKLIST_VARIABLE_NAMES)]


def finish_vote_body(body: str, values: _FinishVoteValues) -> str:
    return re.sub(r"\n{3,}", "\n\n", _substitute(body, values, "finish_vote"))


def finish_vote_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(FINISH_VOTE_VARIABLE_NAMES)]


def release_notification(
    committee: sql.Committee,
    project: sql.Project,
    version: str,
    released: datetime.datetime,
    detected: bool = False,
) -> args.Send:
    # A detected release is one the watcher found published in the dist area rather than
    # one ATR made itself, so it's named as such and the body says where it came from
    host = config.get().APP_HOST
    catalogue_url = f"https://{host}/catalog/{project.key}"

    if detected:
        subject = f"Detected release: {project.short_display_name} {version}"
        provenance = "This release was detected in the distribution area; it was not published through ATR.\n\n"
    else:
        subject = f"{committee.display_name} Released {project.short_display_name} {version}"
        provenance = ""
    body = (
        f"{committee.display_name} has released {project.short_display_name} {version}.\n\n"
        f"{provenance}"
        f"Committee: {committee.display_name}\n"
        f"Project: {project.short_display_name}\n"
        f"Version: {version}\n"
        f"Released: {util.format_datetime(released)}\n\n"
        f"The release artifacts and their download links are catalogued at:\n\n"
        f"  {catalogue_url}\n"
    )
    return args.Send(
        email_sender=mail.NOREPLY_EMAIL_ADDRESS,
        email_to=_RELEASES_LIST_ADDRESS,
        subject=subject,
        body=body,
        in_reply_to=None,
        footer_category=mail.MailFooterCategory.AUTO,
    )


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
    body_values: _VoteValues = {
        "BUG_DATABASE": project.bug_database or "",
        "CHECKLIST_URL": checklist_url,
        "COMMIT": release.commit_hash or "",
        "COMMITTEE": committee.display_name,
        "DURATION": str(options.vote_duration or "an unlimited number of"),
        "HOMEPAGE": project.homepage or "",
        "KEYS_FILE": paths.committee_keys_url(committee),
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


def effective_download_path_suffix(release: sql.Release) -> safe.RelPath | None:
    """The suffix a release publishes to: an explicit per-release override if set,
    otherwise the project policy default. Both are templates, resolved for this release."""
    override = release.download_path_suffix
    if override == "":
        # An empty override is a deliberate choice of the distribution root
        return None
    template = override if (override is not None) else release.project.policy_download_path_suffix
    return resolve_download_path_suffix(
        template=template,
        project_key=release.project.key,
        version=release.version,
        # A top level project shares its key with its committee
        is_top_level=(release.project.key == release.project.committee_key),
    )


def resolve_download_path_suffix(
    *, template: str, project_key: str, version: str, is_top_level: bool
) -> safe.RelPath | None:
    resolved = template.strip()
    if resolved:
        resolved = resolved.replace("{{MAJOR_VERSION}}", version.partition(".")[0])
        resolved = resolved.replace("{{PROJECT_KEY}}", project_key).replace("{{VERSION}}", version)
        return safe.RelPath(resolved)
    if is_top_level:
        return None
    return safe.RelPath(f"{project_key}-{version}")


def template_hash(template: str) -> str:
    """Compute a hash of a template for verification."""
    return hashlib.sha256(template.encode()).hexdigest()


def unknown_template_variables(text: str, names: frozenset[str]) -> list[str]:
    found = re.findall(r"\{\{([A-Za-z0-9_]+)\}\}", text)
    return sorted({name for name in found if name not in names})


def validate_template_variables(value: str, names: frozenset[str]) -> str:
    unknown = unknown_template_variables(value, names)
    if unknown:
        noun = util.plural(len(unknown), "variable", include_count=False)
        raise ValueError(f"Unknown template {noun}: {', '.join(unknown)}")
    return value


def vote_subject_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(VOTE_SUBJECT_VARIABLE_NAMES)]


def vote_template_variables() -> list[tuple[str, str]]:
    return [(name, TEMPLATE_DESCRIPTIONS[name]) for name in sorted(VOTE_VARIABLE_NAMES)]


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
