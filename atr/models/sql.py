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

"""The data models to be persisted in the database."""

# NOTE: We can't use symbolic annotations here because sqlmodel doesn't support them
# https://github.com/fastapi/sqlmodel/issues/196
# https://github.com/fastapi/sqlmodel/pull/778/files

import dataclasses
import datetime
import enum
from typing import TYPE_CHECKING, Any, Final, Literal, Optional, TypeVar, overload

import pydantic
import sqlalchemy
import sqlalchemy.dialects.sqlite as sqlite
import sqlalchemy.event as event
import sqlalchemy.orm as orm
import sqlalchemy.sql.expression as expression
import sqlmodel

from . import results, safe, schema

if TYPE_CHECKING:
    from . import distribution

T = TypeVar("T")

sqlmodel.SQLModel.metadata = sqlalchemy.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

# Data classes


@dataclasses.dataclass(frozen=True)
class DistributionPlatformValue:
    name: str
    gh_slug: str
    template_url: str
    template_staging_url: str | None = None
    requires_owner_namespace: bool = False
    default_owner_namespace: str | None = None


# Enumerations


class CheckResultStatus(enum.StrEnum):
    BLOCKER = "blocker"
    EXCEPTION = "exception"
    FAILURE = "failure"
    SUCCESS = "success"
    WARNING = "warning"


class CheckResultStatusIgnore(enum.StrEnum):
    EXCEPTION = "exception"
    FAILURE = "failure"
    WARNING = "warning"

    @classmethod
    def from_form_field(cls, status: str) -> Optional["CheckResultStatusIgnore"]:
        match status:
            case "None":
                return None
            case "CheckResultStatusIgnore.EXCEPTION":
                return cls.EXCEPTION
            case "CheckResultStatusIgnore.FAILURE":
                return cls.FAILURE
            case "CheckResultStatusIgnore.WARNING":
                return cls.WARNING
            case _:
                raise ValueError(f"Invalid status: {status}")

    def to_form_field(self) -> str:
        return f"CheckResultStatusIgnore.{self.value.upper()}"


class DistributionPlatform(enum.Enum):
    ARTIFACT_HUB = DistributionPlatformValue(
        name="Artifact Hub",
        gh_slug="artifacthub",
        template_url="https://artifacthub.io/api/v1/packages/helm/{owner_namespace}/{package}/{version}",
        template_staging_url="https://staging.artifacthub.io/api/v1/packages/helm/{owner_namespace}/{package}/{version}",
        requires_owner_namespace=True,
    )
    DOCKER_HUB = DistributionPlatformValue(
        name="Docker Hub",
        gh_slug="dockerhub",
        template_url="https://hub.docker.com/v2/namespaces/{owner_namespace}/repositories/{package}/tags/{version}",
        # TODO: Need to use staging tags?
        # template_staging_url="https://hub.docker.com/v2/namespaces/{owner_namespace}/repositories/{package}/tags/{version}",
        default_owner_namespace="library",
    )
    # GITHUB = DistributionPlatformValue(
    #     name="GitHub",
    #     gh_slug="github",
    #     template_url="https://api.github.com/repos/{owner_namespace}/{package}/releases/tags/v{version}",
    #     # Combine with {"prerelease": true}
    #     template_staging_url="https://api.github.com/repos/{owner_namespace}/{package}/releases",
    #     requires_owner_namespace=True,
    # )
    MAVEN = DistributionPlatformValue(
        name="Maven Central",
        gh_slug="mavencentral",
        template_url="https://repo1.maven.org/maven2/{owner_namespace}/{package}/maven-metadata.xml",
        # Below is the old template using the maven search API - but the index isn't updated quickly enough for us
        # template_url="https://search.maven.org/solrsearch/select?q=g:{owner_namespace}+AND+a:{package}+AND+v:{version}&core=gav&rows=20&wt=json",
        template_staging_url="https://repository.apache.org:4443/repository/maven-staging/{owner_namespace}/{package}/maven-metadata.xml",
        # https://repository.apache.org/content/repositories/orgapachePROJECT-NNNN/
        # There's no JSON, but each individual package has maven-metadata.xml
        requires_owner_namespace=True,
    )
    NPM = DistributionPlatformValue(
        name="npm",
        gh_slug="npm",
        # TODO: Need to parse dist-tags
        template_url="https://registry.npmjs.org/{package}",
    )
    NPM_SCOPED = DistributionPlatformValue(
        name="npm (scoped)",
        gh_slug="npm",
        # TODO: Need to parse dist-tags
        template_url="https://registry.npmjs.org/@{owner_namespace}/{package}",
        requires_owner_namespace=True,
    )
    PYPI = DistributionPlatformValue(
        name="PyPI",
        gh_slug="pypi",
        template_url="https://pypi.org/pypi/{package}/{version}/json",
        template_staging_url="https://test.pypi.org/pypi/{package}/{version}/json",
    )


class LicenseCheckMode(enum.StrEnum):
    BOTH = "Both"
    LIGHTWEIGHT = "Lightweight"
    RAT = "RAT"


class ProjectStatus(enum.StrEnum):
    ACTIVE = "active"
    DORMANT = "dormant"
    RETIRED = "retired"
    STANDING = "standing"


class QuarantineStatus(enum.Enum):
    STAGING = "STAGING"
    PENDING = "PENDING"
    FAILED = "FAILED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class ReleasePhase(enum.StrEnum):
    # TODO: Rename these to the UI names?
    # COMPOSE, VOTE, FINISH, "DISTRIBUTE"
    # Compose a draft
    # Vote on a candidate
    # Finish a preview
    # Distribute a (finished) release
    # Step 1: The candidate files are added from external sources and checked by ATR
    RELEASE_CANDIDATE_DRAFT = "release_candidate_draft"
    # Step 2: The project members are voting on the candidate release
    RELEASE_CANDIDATE = "release_candidate"
    # Step 3: The release files are being put in place
    RELEASE_PREVIEW = "release_preview"
    # Step 4: The release has been announced
    RELEASE = "release"


class TaskStatus(enum.StrEnum):
    """Status of a task in the task queue."""

    QUEUED = "queued"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskType(enum.StrEnum):
    COMPARE_SOURCE_TREES = "compare_source_trees"
    DISTRIBUTION_STATUS = "distribution_status"
    DISTRIBUTION_WORKFLOW = "distribution_workflow"
    HASHING_CHECK = "hashing_check"
    KEYS_IMPORT_FILE = "keys_import_file"
    LICENSE_FILES = "license_files"
    LICENSE_HEADERS = "license_headers"
    MESSAGE_SEND = "message_send"
    METADATA_UPDATE = "metadata_update"
    PATHS_CHECK = "paths_check"
    QUARANTINE_VALIDATE = "quarantine_validate"
    RAT_CHECK = "rat_check"
    SBOM_AUGMENT = "sbom_augment"
    SBOM_CONVERT = "sbom_convert"
    SBOM_GENERATE_CYCLONEDX = "sbom_generate_cyclonedx"
    SBOM_OSV_SCAN = "sbom_osv_scan"
    SBOM_QS_SCORE = "sbom_qs_score"
    SBOM_TOOL_SCORE = "sbom_tool_score"
    SIGNATURE_CHECK = "signature_check"
    SVN_IMPORT_FILES = "svn_import_files"
    TARGZ_INTEGRITY = "targz_integrity"
    TARGZ_STRUCTURE = "targz_structure"
    VOTE_INITIATE = "vote_initiate"
    WORKFLOW_STATUS = "workflow_status"
    ZIPFORMAT_INTEGRITY = "zipformat_integrity"
    ZIPFORMAT_STRUCTURE = "zipformat_structure"


class UserRole(enum.StrEnum):
    COMMITTEE_MEMBER = "committee_member"
    RELEASE_MANAGER = "release_manager"
    COMMITTER = "committer"
    VISITOR = "visitor"
    ASF_MEMBER = "asf_member"
    SYSADMIN = "sysadmin"


# Pydantic models


def pydantic_example(value: Any) -> dict[Literal["json_schema_extra"], dict[str, Any]]:
    return {"json_schema_extra": {"example": value}}


class QuarantineFileEntryV1(schema.Strict):
    version: Literal[1] = 1
    rel_path: str
    size_bytes: int
    content_hash: str
    errors: list[str] = schema.factory(list)


class VoteEntry(schema.Strict):
    result: bool = schema.Field(alias="result", **pydantic_example(True))
    summary: str = schema.Field(alias="summary", **pydantic_example("This is a summary"))
    binding_votes: int = schema.Field(alias="binding_votes", **pydantic_example(10))
    community_votes: int = schema.Field(alias="community_votes", **pydantic_example(10))
    start: datetime.datetime = schema.Field(
        alias="start", **pydantic_example(datetime.datetime(2025, 5, 5, 1, 2, 3, tzinfo=datetime.UTC))
    )
    end: datetime.datetime = schema.Field(
        alias="end", **pydantic_example(datetime.datetime(2025, 5, 7, 1, 2, 3, tzinfo=datetime.UTC))
    )


# Type decorators
# TODO: Possibly move these to a separate module
# That way, we can more easily track Alembic's dependence on them


class UTCDateTime(sqlalchemy.types.TypeDecorator):
    """
    A custom column type to store datetime in sqlite.

    As sqlite does not have timezone support, we ensure that all datetimes stored
    within sqlite are converted to UTC. When retrieved, the datetimes are constructed
    as offset-aware datetime with UTC as their timezone.
    """

    impl = sqlalchemy.types.TIMESTAMP(timezone=True)

    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value:
            if not isinstance(value, datetime.datetime):
                raise ValueError(f"Unexpected value type {type(value)}")

            if value.tzinfo is None:
                raise ValueError("Unexpected offset-naive datetime")

            # store the datetime in UTC in sqlite as it does not support timezones
            return value.astimezone(datetime.UTC)
        else:
            return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime.datetime):
            return value.replace(tzinfo=datetime.UTC)
        else:
            return value


class SafeJSON(sqlalchemy.types.TypeDecorator):
    """JSON column that serialises SafeType and StatePath values.

    Use instead of sqlalchemy.JSON whenever the stored value may contain
    atr.models.safe.SafeType instances (which are not JSON-serialisable by
    the standard library encoder) or atr.models.safe.StatePath instances
    (which include a managed root that must survive the round-trip).
    """

    impl = sqlalchemy.JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return _safe_json_encode(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return _safe_json_decode(value)


def _safe_json_encode(value: Any) -> Any:
    """Recursively convert SafeType/StatePath instances to JSON-serialisable form."""
    from . import safe

    if isinstance(value, safe.StatePath):
        return {"__type__": "StatePath", "path": str(value.path), "root": str(value.root)}
    if isinstance(value, safe.SafeType):
        return str(value)
    if isinstance(value, dict):
        for k in value:
            if not isinstance(k, str):
                raise TypeError(f"Dict key must be str, got {type(k).__name__!r}: {k!r}")
        return {k: _safe_json_encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_json_encode(v) for v in value]
    return value


def _safe_json_decode(value: Any) -> Any:
    """
    Reconstruct StatePath instances from tagged dicts produced by _safe_json_encode.
    Other types are handled cleanly by Pydantic so just return the value
    """
    import pathlib

    from . import safe

    if isinstance(value, dict):
        if value.get("__type__") == "StatePath":
            return safe.StatePath(pathlib.Path(value["path"]), pathlib.Path(value["root"]))
        return {k: _safe_json_decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_json_decode(v) for v in value]
    return value


class ResultsJSON(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return value
        raise ValueError("Unsupported value for Results column")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return results.ResultsAdapter.validate_python(value)
        except pydantic.ValidationError:
            # TODO: Should we make this more strict?
            return None


_QUARANTINE_FILE_METADATA_ADAPTER: Final = pydantic.TypeAdapter(list[QuarantineFileEntryV1])


class QuarantineFileMetadataJSON(sqlalchemy.types.TypeDecorator):
    impl = sqlalchemy.JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _QUARANTINE_FILE_METADATA_ADAPTER.dump_python(value, mode="json")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return _QUARANTINE_FILE_METADATA_ADAPTER.validate_python(value)


# SQL models


def example(value: Any) -> dict[Literal["schema_extra"], dict[str, Any]]:
    return {"schema_extra": {"json_schema_extra": {"examples": [value]}}}


# SQL models with no dependencies


# KeyLink:
class KeyLink(sqlmodel.SQLModel, table=True):
    committee_key: str = sqlmodel.Field(foreign_key="committee.key", primary_key=True)
    key_fingerprint: str = sqlmodel.Field(foreign_key="publicsigningkey.fingerprint", primary_key=True)


# PersonalAccessToken:
class PersonalAccessToken(sqlmodel.SQLModel, table=True):
    id: int | None = sqlmodel.Field(default=None, primary_key=True)
    asfuid: str = sqlmodel.Field(index=True)
    token_hash: str = sqlmodel.Field(unique=True)
    created: datetime.datetime = sqlmodel.Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
    )
    expires: datetime.datetime = sqlmodel.Field(sa_column=sqlalchemy.Column(UTCDateTime, nullable=False))
    last_used: datetime.datetime | None = sqlmodel.Field(default=None, sa_column=sqlalchemy.Column(UTCDateTime))
    label: str | None = None


# RevisionCounter:
class RevisionCounter(sqlmodel.SQLModel, table=True):
    release_key: str = sqlmodel.Field(primary_key=True)
    last_allocated_number: int = sqlmodel.Field(default=0)


# SSHKey:
class SSHKey(sqlmodel.SQLModel, table=True):
    fingerprint: str = sqlmodel.Field(primary_key=True)
    key: str
    asf_uid: str


# Task:
class Task(sqlmodel.SQLModel, table=True):
    """A task in the task queue."""

    id: int = sqlmodel.Field(default=None, primary_key=True)
    status: TaskStatus = sqlmodel.Field(default=TaskStatus.QUEUED, index=True)
    task_type: TaskType
    task_args: Any = sqlmodel.Field(sa_column=sqlalchemy.Column(SafeJSON))
    inputs_hash: str | None = sqlmodel.Field(
        default=None,
        **example("blake3:7f83b1657ff1fc..."),
        unique=True,
        index=True,
    )
    asf_uid: str
    added: datetime.datetime = sqlmodel.Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sqlalchemy.Column(UTCDateTime, index=True, nullable=False),
    )
    scheduled: datetime.datetime | None = sqlmodel.Field(
        default=None,
        sa_column=sqlalchemy.Column(UTCDateTime, index=True),
    )
    started: datetime.datetime | None = sqlmodel.Field(
        default=None,
        sa_column=sqlalchemy.Column(UTCDateTime),
    )
    pid: int | None = None
    completed: datetime.datetime | None = sqlmodel.Field(
        default=None,
        sa_column=sqlalchemy.Column(UTCDateTime),
    )
    result: results.Results | None = sqlmodel.Field(default=None, sa_column=sqlalchemy.Column(ResultsJSON))
    error: str | None = None

    workflow: "WorkflowStatus" = sqlmodel.Relationship(back_populates="task")

    # Used for check tasks
    # We don't put these in task_args because we want to query them efficiently
    project_key: str | None = sqlmodel.Field(default=None, foreign_key="project.key")
    version_key: str | None = sqlmodel.Field(default=None, index=True)
    revision_number: str | None = sqlmodel.Field(default=None, index=True)
    primary_rel_path: str | None = sqlmodel.Field(default=None, index=True)

    def model_post_init(self, _context):
        if isinstance(self.task_type, str):
            self.task_type = TaskType(self.task_type)

        if isinstance(self.status, str):
            self.status = TaskStatus(self.status)

        if isinstance(self.added, str):
            self.added = datetime.datetime.fromisoformat(self.added.rstrip("Z"))

        if isinstance(self.started, str):
            self.started = datetime.datetime.fromisoformat(self.started.rstrip("Z"))

        if isinstance(self.completed, str):
            self.completed = datetime.datetime.fromisoformat(self.completed.rstrip("Z"))

    @property
    def safe_primary_rel_path(self) -> safe.RelPath | None:
        """Get the typesafe validated relative path for the task, if set."""
        return safe.RelPath(self.primary_rel_path) if self.primary_rel_path else None

    # Create an index on status and added for efficient task claiming
    __table_args__ = (
        sqlalchemy.Index("ix_task_status_added", "status", "added"),
        # Ensure valid status transitions:
        # - QUEUED can transition to ACTIVE
        # - ACTIVE can transition to COMPLETED or FAILED
        # - COMPLETED and FAILED are terminal states
        sqlalchemy.CheckConstraint(
            """
            (
                -- Initial state is always valid
                status = 'QUEUED'
                -- QUEUED -> ACTIVE requires setting started time and pid
                OR (status = 'ACTIVE' AND started IS NOT NULL AND pid IS NOT NULL)
                -- ACTIVE -> COMPLETED requires setting completed time and result
                OR (status = 'COMPLETED' AND completed IS NOT NULL AND result IS NOT NULL)
                -- ACTIVE -> FAILED requires setting completed time and error (result optional)
                OR (status = 'FAILED' AND completed IS NOT NULL AND error IS NOT NULL)
            )
            """,
            name="valid_task_status_transitions",
        ),
    )


# TextValue:
class TextValue(sqlmodel.SQLModel, table=True):
    # Composite primary key, automatically handled by SQLModel
    ns: str = sqlmodel.Field(primary_key=True, index=True)
    key: str = sqlmodel.Field(primary_key=True, index=True)
    value: str = sqlmodel.Field()


# WorkflowSSHKey:
class WorkflowSSHKey(sqlmodel.SQLModel, table=True):
    fingerprint: str = sqlmodel.Field(primary_key=True, index=True)
    key: str = sqlmodel.Field()
    project_key: str = sqlmodel.Field(index=True)
    asf_uid: str = sqlmodel.Field(index=True)
    github_uid: str = sqlmodel.Field(index=True)
    github_nid: int = sqlmodel.Field(index=True)
    github_payload: dict[str, Any] = sqlmodel.Field(
        default_factory=dict, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    expires: int = sqlmodel.Field()


# SQL core models


# Committee: Committee Project PublicSigningKey
class Committee(sqlmodel.SQLModel, table=True):
    key: str = sqlmodel.Field(unique=True, primary_key=True, **example("example"))
    name: str | None = sqlmodel.Field(default=None, **example("Example"))
    # True only if this is an incubator podling with a PPMC
    is_podling: bool = sqlmodel.Field(default=False)

    # 1-M: Committee -> [Committee]
    # M-1: Committee -> Committee
    child_committees: list["Committee"] = sqlmodel.Relationship(
        sa_relationship_kwargs=dict(
            backref=orm.backref("parent_committee", remote_side="Committee.key"),
        ),
    )

    # M-1: Committee -> Committee
    # 1-M: Committee -> [Committee]
    parent_committee_key: str | None = sqlmodel.Field(default=None, foreign_key="committee.key")
    # parent_committee: Optional["Committee"]

    # 1-M: Committee -> [Project]
    # M-1: Project -> Committee
    projects: list["Project"] = sqlmodel.Relationship(back_populates="committee")

    committee_members: list[str] = sqlmodel.Field(
        default_factory=list,
        sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False),
        **example(["sbp", "arm", "wave"]),
    )
    committers: list[str] = sqlmodel.Field(
        default_factory=list,
        sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False),
        **example(["sbp", "arm", "wave"]),
    )
    release_managers: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False), **example(["wave"])
    )

    # M-M: Committee -> [PublicSigningKey]
    # M-M: PublicSigningKey -> [Committee]
    public_signing_keys: list["PublicSigningKey"] = sqlmodel.Relationship(
        back_populates="committees", link_model=KeyLink
    )

    @property
    def display_name(self) -> str:
        """Get the display name for the committee."""
        name = self.name or self.key.title()
        return f"{name} (Incubating)" if self.is_podling else name


def see_also(arg: Any) -> None:
    pass


# Project: Project Committee Release DistributionChannel ReleasePolicy
class Project(sqlmodel.SQLModel, table=True):
    key: str = sqlmodel.Field(primary_key=True, unique=True, **example("example"))
    # TODO: Ideally full_name would be unique for str only, but that's complex
    # We always include "Apache" in the full_name
    name: str | None = sqlmodel.Field(default=None, **example("Apache Example"))

    status: ProjectStatus = sqlmodel.Field(default=ProjectStatus.ACTIVE, **example(ProjectStatus.ACTIVE))

    # M-1: Project -> Project
    # 1-M: (Project.child_project is missing, would be Project -> [Project])
    super_project_key: str | None = sqlmodel.Field(default=None, foreign_key="project.key")
    # NOTE: Neither "Project" | None nor "Project | None" works
    super_project: Optional["Project"] = sqlmodel.Relationship()

    description: str | None = sqlmodel.Field(default=None, **example("Example is a simple example project"))
    category: str | None = sqlmodel.Field(default=None, **example("data,storage"))
    programming_languages: str | None = sqlmodel.Field(default=None, **example("c,python"))

    # M-1: Project -> Committee
    # 1-M: Committee -> [Project]
    committee_key: str | None = sqlmodel.Field(default=None, foreign_key="committee.key", **example("example"))
    committee: Committee | None = sqlmodel.Relationship(back_populates="projects")
    see_also(Committee.projects)

    # 1-M: Project -> [Release]
    # M-1: Release -> Project
    # see_also(Release.project)
    releases: list["Release"] = sqlmodel.Relationship(back_populates="project")

    # # 1-M: Project -> [DistributionChannel]
    # # M-1: DistributionChannel -> Project
    # distribution_channels: list["DistributionChannel"] = sqlmodel.Relationship(back_populates="project")

    # 1-1: Project -C-> ReleasePolicy
    # 1-1: ReleasePolicy -> Project
    release_policy_id: int | None = sqlmodel.Field(default=None, foreign_key="releasepolicy.id", ondelete="CASCADE")
    release_policy: Optional["ReleasePolicy"] = sqlmodel.Relationship(
        cascade_delete=True, sa_relationship_kwargs={"cascade": "all, delete-orphan", "single_parent": True}
    )

    created: datetime.datetime = sqlmodel.Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
        **example(datetime.datetime(2025, 5, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    created_by: str | None = sqlmodel.Field(default=None, **example("user"))

    @property
    def display_name(self) -> str:
        """Get the display name for the Project."""
        base = self.name or str(self.key)
        if self.committee and self.committee.is_podling:
            return f"{base} (Incubating)"
        return base

    @property
    def safe_key(self) -> safe.ProjectKey:
        """Get the typesafe validated name for the Project"""
        return safe.ProjectKey(self.key)

    @property
    def short_display_name(self) -> str:
        """Get the short display name for the Project."""
        return self.display_name.removeprefix("Apache ")

    @property
    def policy_announce_release_default(self) -> str:
        return """\
The Apache {{COMMITTEE}} project team is pleased to announce the
release of {{PROJECT}} {{VERSION}}.

This is a stable release available for production use.

Downloads are available from the following URL:

{{DOWNLOAD_URL}}

On behalf of the Apache {{COMMITTEE}} project team,

{{YOUR_FULL_NAME}} ({{YOUR_ASF_ID}})
"""

    @property
    def policy_announce_release_subject_default(self) -> str:
        return "[ANNOUNCE] {{PROJECT}} {{VERSION}} released"

    @property
    def policy_start_vote_default(self) -> str:
        return """Hello {{COMMITTEE}},

I'd like to call a vote on releasing the following artifacts as
Apache {{PROJECT}} {{VERSION}}. This vote is being conducted using an
Alpha version of the Apache Trusted Releases (ATR) platform.
Please report any bugs or issues to the ASF Tooling team.

The release candidate page, including downloads, can be found at:

  {{REVIEW_URL}}

The release artifacts are signed with one or more OpenPGP keys from:

  {{KEYS_FILE}}

Please review the release candidate and vote accordingly.

[ ] +1 Release this package
[ ] +0 Abstain
[ ] -1 Do not release this package (please provide specific comments)

You can vote on ATR at the URL above, or manually by replying to this email.

The vote is open for {{DURATION}} hours.

{{RELEASE_CHECKLIST}}
Thanks,
{{YOUR_FULL_NAME}} ({{YOUR_ASF_ID}})
"""

    @property
    def policy_start_vote_subject_default(self) -> str:
        return "[VOTE] Release {{PROJECT}} {{VERSION}}"

    @property
    def policy_default_min_hours(self) -> int:
        return 72

    @property
    def policy_announce_release_subject(self) -> str:
        if ((policy := self.release_policy) is None) or (policy.announce_release_subject == ""):
            return self.policy_announce_release_subject_default
        return policy.announce_release_subject

    @property
    def policy_announce_release_template(self) -> str:
        if ((policy := self.release_policy) is None) or (policy.announce_release_template == ""):
            return self.policy_announce_release_default
        return policy.announce_release_template

    @property
    def policy_mailto_addresses(self) -> list[str]:
        if ((policy := self.release_policy) is None) or (not policy.mailto_addresses):
            if self.committee is not None:
                return [f"dev@{self.committee.key}.apache.org", f"private@{self.committee.key}.apache.org"]
            else:
                # TODO: Or raise an error?
                return [f"dev@{self.key}.apache.org", f"private@{self.key}.apache.org"]
        return policy.mailto_addresses

    @property
    def policy_manual_vote(self) -> bool:
        if (policy := self.release_policy) is None:
            return False
        return policy.manual_vote

    @property
    def policy_min_hours(self) -> int:
        if ((policy := self.release_policy) is None) or (policy.min_hours is None):
            # TODO: Not sure what the default should be
            return self.policy_default_min_hours
        return policy.min_hours

    @property
    def policy_release_checklist(self) -> str:
        if ((policy := self.release_policy) is None) or (policy.release_checklist == ""):
            return ""
        return policy.release_checklist

    @property
    def policy_vote_comment_template(self) -> str:
        if ((policy := self.release_policy) is None) or (policy.vote_comment_template == ""):
            return ""
        return policy.vote_comment_template

    @property
    def policy_start_vote_subject(self) -> str:
        if ((policy := self.release_policy) is None) or (policy.start_vote_subject == ""):
            return self.policy_start_vote_subject_default
        return policy.start_vote_subject

    @property
    def policy_start_vote_template(self) -> str:
        if ((policy := self.release_policy) is None) or (policy.start_vote_template == ""):
            return self.policy_start_vote_default
        return policy.start_vote_template

    @property
    def policy_binary_artifact_paths(self) -> list[str]:
        if (policy := self.release_policy) is None:
            return []
        # TODO: The type of policy.binary_artifact_paths is list[str]
        # But the production server has None values
        return policy.binary_artifact_paths or []

    @property
    def policy_source_artifact_paths(self) -> list[str]:
        if (policy := self.release_policy) is None:
            return []
        # TODO: The type of policy.source_artifact_paths is list[str]
        # But the production server has None values
        return policy.source_artifact_paths or []

    @property
    def policy_license_check_mode(self) -> LicenseCheckMode:
        if (policy := self.release_policy) is None:
            return LicenseCheckMode.BOTH
        return policy.license_check_mode

    @property
    def policy_source_excludes_lightweight(self) -> list[str]:
        if (policy := self.release_policy) is None:
            return []
        return policy.source_excludes_lightweight or []

    @property
    def policy_source_excludes_rat(self) -> list[str]:
        if (policy := self.release_policy) is None:
            return []
        return policy.source_excludes_rat or []

    @property
    def policy_tagging_spec(self) -> dict[str, Any] | None:
        if (policy := self.release_policy) is None:
            return None
        return policy.file_tag_mappings

    @property
    def policy_github_repository_name(self) -> str:
        if (policy := self.release_policy) is None:
            return ""
        return policy.github_repository_name

    @property
    def policy_github_repository_branch(self) -> str:
        if (policy := self.release_policy) is None:
            return ""
        return policy.github_repository_branch

    @property
    def policy_github_compose_workflow_path(self) -> list[str]:
        if (policy := self.release_policy) is None:
            return []
        return policy.github_compose_workflow_path or []

    @property
    def policy_file_tag_mappings(self) -> dict[str, Any]:
        if (policy := self.release_policy) is None:
            return {}
        return policy.file_tag_mappings or {}

    @property
    def policy_github_vote_workflow_path(self) -> list[str]:
        if (policy := self.release_policy) is None:
            return []
        return policy.github_vote_workflow_path or []

    @property
    def policy_github_finish_workflow_path(self) -> list[str]:
        if (policy := self.release_policy) is None:
            return []
        return policy.github_finish_workflow_path or []

    @property
    def policy_preserve_download_files(self) -> bool:
        if (policy := self.release_policy) is None:
            return False
        return policy.preserve_download_files


# Release: Project ReleasePolicy Revision CheckResult
class Release(sqlmodel.SQLModel, table=True):
    # model_config = compat.SQLModelConfig(extra="forbid", from_attributes=True)

    # We guarantee that "{project.key}-{version}" is unique
    # Therefore we can use that for the key
    key: str = sqlmodel.Field(default="", primary_key=True, unique=True, **example("example-0.0.1"))
    phase: ReleasePhase = sqlmodel.Field(**example(ReleasePhase.RELEASE_CANDIDATE_DRAFT))
    created: datetime.datetime = sqlmodel.Field(
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
        **example(datetime.datetime(2025, 5, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    released: datetime.datetime | None = sqlmodel.Field(
        default=None,
        sa_column=sqlalchemy.Column(UTCDateTime),
        **example(datetime.datetime(2025, 6, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )

    check_cache_key: str | None = sqlmodel.Field(default=None, **example("ef0ccb0a-3514-4b65-abcd-879850349f74"))

    # M-1: Release -> Project
    # 1-M: Project -> [Release]
    project_key: str = sqlmodel.Field(foreign_key="project.key", **example("example"))
    project: Project = sqlmodel.Relationship(back_populates="releases")
    see_also(Project.releases)

    package_managers: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False), **example([])
    )
    # TODO: Not all releases have a version
    # We could either make this str | None, or we could require version to be set on packages only
    # For example, Apache Airflow Providers do not have an overall version
    # They have one version per package, i.e. per provider
    version: str = sqlmodel.Field(**example("0.0.1"))
    sboms: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False), **example([])
    )

    # 1-1: Release -C-> ReleasePolicy
    # 1-1: ReleasePolicy -> Release
    release_policy_id: int | None = sqlmodel.Field(default=None, foreign_key="releasepolicy.id")
    release_policy: Optional["ReleasePolicy"] = sqlmodel.Relationship(
        cascade_delete=True, sa_relationship_kwargs={"cascade": "all, delete-orphan", "single_parent": True}
    )

    # VoteEntry is a Pydantic model, not a SQL model
    votes: list[VoteEntry] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    vote_manual: bool = sqlmodel.Field(default=False, **example(False))
    vote_started: datetime.datetime | None = sqlmodel.Field(
        default=None,
        sa_column=sqlalchemy.Column(UTCDateTime),
        **example(datetime.datetime(2025, 5, 5, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    vote_resolved: datetime.datetime | None = sqlmodel.Field(
        default=None,
        sa_column=sqlalchemy.Column(UTCDateTime),
        **example(datetime.datetime(2025, 5, 7, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    podling_thread_id: str | None = sqlmodel.Field(default=None, **example("hmk1lpwnnxn5zsbp8gwh7115h2qm7jrh"))

    # 1-M: Release -C-> [Revision]
    # M-1: Revision -> Release
    revisions: list["Revision"] = sqlmodel.Relationship(
        back_populates="release",
        sa_relationship_kwargs={
            "order_by": "Revision.seq",
            "foreign_keys": "[Revision.release_key]",
            "cascade": "all, delete-orphan",
        },
    )

    # 1-M: Release -C-> [CheckResult]
    # M-1: CheckResult -> Release
    check_results: list["CheckResult"] = sqlmodel.Relationship(
        back_populates="release", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

    # 1-M: Release -> [Distribution]
    # M-1: Distribution -> Release
    distributions: list["Distribution"] = sqlmodel.Relationship(
        back_populates="release", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )

    # The combination of key and version must be unique
    __table_args__ = (sqlmodel.UniqueConstraint("project_key", "version", name="unique_project_version"),)

    @property
    def committee(self) -> Committee | None:
        """Get the committee for the release."""
        project = self.project
        # Type checker is sure that it can not be None
        # if project is None:
        #     return None
        return project.committee

    @property
    def safe_latest_revision_number(self) -> safe.RevisionNumber:
        """Get the typesafe validated name for the Release"""
        return safe.RevisionNumber(self.unwrap_revision_number)

    @property
    def safe_key(self) -> safe.ReleaseKey:
        """Get the typesafe validated name for the Release"""
        return safe.ReleaseKey(self.key)

    @property
    def safe_project_key(self) -> safe.ProjectKey:
        """Get the typesafe validated name for the release project"""
        return safe.ProjectKey(self.project_key)

    @property
    def safe_version_key(self) -> safe.VersionKey:
        """Get the typesafe validated name for the release version"""
        return safe.VersionKey(self.version)

    @property
    def short_display_name(self) -> str:
        """Get the short display name for the release."""
        return f"{self.project.short_display_name} {self.version}"

    @property
    def unwrap_revision_number(self) -> str:
        """Get the revision number for the release, or raise an exception."""
        number = self.latest_revision_number
        if number is None:
            raise ValueError("Release has no revisions")
        return number

    # TODO: How do we give an example for this?
    @pydantic.computed_field
    @property
    def latest_revision_number(self) -> str | None:
        """Get the latest revision number for the release."""
        # The session must still be active for this to work
        number = getattr(self, "_latest_revision_number", None)
        if not (isinstance(number, str) or (number is None)):
            raise ValueError("Latest revision number is not a str or None")
        return number

    def model_post_init(self, _context):
        if isinstance(self.created, str):
            self.created = datetime.datetime.fromisoformat(self.created.rstrip("Z"))

        if isinstance(self.phase, str):
            self.phase = ReleasePhase(self.phase)

    # NOTE: This does not work
    # But it we set it with Release.latest_revision_number_query = ..., it might work
    # Not clear that we'd want to do that, though
    # @property
    # def latest_revision_number_query(self) -> expression.ScalarSelect[str]:
    #     return (
    #         sqlmodel.select(validate_instrumented_attribute(Revision.number))
    #         .where(validate_instrumented_attribute(Revision.release_key) == Release.name)
    #         .order_by(validate_instrumented_attribute(Revision.seq).desc())
    #         .limit(1)
    #         .scalar_subquery()
    #     )


# SQL models referencing Committee, Project, or Release


# CheckResult: Release
class CheckResult(sqlmodel.SQLModel, table=True):
    # TODO: We have default=None here with a field typed int, not int | None
    id: int = sqlmodel.Field(default=None, primary_key=True, **example(123))

    # M-1: CheckResult -> Release
    # 1-M: Release -C-> [CheckResult]
    release_key: str = sqlmodel.Field(
        foreign_key="release.key", ondelete="CASCADE", index=True, **example("example-0.0.1")
    )
    release: Release = sqlmodel.Relationship(back_populates="check_results")

    # We don't call this latest_revision_number, because it might not be the latest
    revision_number: str | None = sqlmodel.Field(default=None, index=True, **example("00005"))
    checker: str = sqlmodel.Field(**example("atr.tasks.checks.license.files"))
    checker_version: str | None = sqlmodel.Field(default=None, **example("2"))

    primary_rel_path: str | None = sqlmodel.Field(
        default=None, index=True, **example("apache-example-0.0.1-source.tar.gz")
    )
    member_rel_path: str | None = sqlmodel.Field(default=None, index=True, **example("apache-example-0.0.1/pom.xml"))
    created: datetime.datetime = sqlmodel.Field(
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
        **example(datetime.datetime(2025, 5, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    status: CheckResultStatus = sqlmodel.Field(default=CheckResultStatus.SUCCESS, **example(CheckResultStatus.SUCCESS))
    message: str = sqlmodel.Field(**example("sha512 matches for apache-example-0.0.1/pom.xml"))
    data: Any = sqlmodel.Field(
        sa_column=sqlalchemy.Column(sqlalchemy.JSON), **example({"expected": "...", "found": "..."})
    )
    inputs_hash: str | None = sqlmodel.Field(default=None, index=True, **example("blake3:7f83b1657ff1fc..."))
    cached: bool = sqlmodel.Field(default=False, **example(False))

    @property
    def safe_primary_rel_path(self) -> safe.RelPath | None:
        """Get the typesafe validated relative path for the check result, if set."""
        return safe.RelPath(self.primary_rel_path) if self.primary_rel_path else None


class CheckResultIgnore(sqlmodel.SQLModel, table=True):
    id: int = sqlmodel.Field(default=None, primary_key=True, **example(123))
    asf_uid: str = sqlmodel.Field(**example("user"))
    created: datetime.datetime = sqlmodel.Field(
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
        **example(datetime.datetime(2025, 5, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    project_key: str = sqlmodel.Field(foreign_key="project.key", **example("example"))
    release_glob: str | None = sqlmodel.Field(**example("example-0.0.*"))
    revision_number: str | None = sqlmodel.Field(**example("00001"))
    checker_glob: str | None = sqlmodel.Field(**example("atr.tasks.checks.license.files"))
    primary_rel_path_glob: str | None = sqlmodel.Field(**example("apache-example-0.0.1-*.tar.gz"))
    member_rel_path_glob: str | None = sqlmodel.Field(**example("apache-example-0.0.1/*.xml"))
    status: CheckResultStatusIgnore | None = sqlmodel.Field(
        default=None,
        **example(CheckResultStatusIgnore.FAILURE),
    )
    message_glob: str | None = sqlmodel.Field(**example("sha512 matches for apache-example-0.0.1/*.xml"))

    def model_post_init(self, _context):
        if isinstance(self.created, str):
            self.created = datetime.datetime.fromisoformat(self.created.rstrip("Z"))


# Distribution: Release
class Distribution(sqlmodel.SQLModel, table=True):
    release_key: str = sqlmodel.Field(foreign_key="release.key", ondelete="CASCADE", primary_key=True, index=True)
    release: Release = sqlmodel.Relationship(back_populates="distributions")
    platform: DistributionPlatform = sqlmodel.Field(primary_key=True, index=True)
    owner_namespace: str = sqlmodel.Field(primary_key=True, index=True, default="")
    package: str = sqlmodel.Field(primary_key=True, index=True)
    version: str = sqlmodel.Field(primary_key=True, index=True)
    staging: bool = sqlmodel.Field(default=False)
    pending: bool = sqlmodel.Field(default=False)
    retries: int = sqlmodel.Field(default=0)
    upload_date: datetime.datetime | None = sqlmodel.Field(default=None)
    api_url: str | None = sqlmodel.Field(default=None)
    web_url: str | None = sqlmodel.Field(default=None)
    created_by: str | None = sqlmodel.Field(default=None)
    # The API response can be huge, e.g. from npm
    # So we do not store it in the database
    # api_response: Any = sqlmodel.Field(sa_column=sqlalchemy.Column(sqlalchemy.JSON))

    def distribution_data(self, details: bool = False) -> "distribution.Data":
        """Get a distribution data object"""
        from . import distribution

        return distribution.Data(
            platform=self.platform,
            owner_namespace=safe.Alphanumeric(self.owner_namespace),
            package=safe.Alphanumeric(self.package),
            version=safe.VersionKey(self.version),
            details=details,
        )

    @property
    def identifier(self) -> str:
        def normal(text: str) -> str:
            return text.replace(" ", "_").lower()

        name = normal(self.platform.value.name)
        package = normal(self.package)
        version = normal(self.version)
        return f"{name}-{package}-{version}"

    @property
    def safe_release_key(self) -> safe.ReleaseKey:
        """Get the typesafe validated name for the distribution release"""
        return safe.ReleaseKey(self.release_key)

    @property
    def title(self) -> str:
        return f"{self.platform.value.name} {self.package} {self.version}"


# # DistributionChannel: Project
# class DistributionChannel(sqlmodel.SQLModel, table=True):
#     id: int = sqlmodel.Field(default=None, primary_key=True)
#     name: str = sqlmodel.Field(index=True, unique=True)
#     url: str
#     credentials: str
#     is_test: bool = sqlmodel.Field(default=False)
#     automation_endpoint: str
#
#     project_key: str = sqlmodel.Field(foreign_key="project.name")
#
#     # M-1: DistributionChannel -> Project
#     # 1-M: Project -> [DistributionChannel]
#     project: Project = sqlmodel.Relationship(back_populates="distribution_channels")
#     see_also(Project.distribution_channels)


# PublicSigningKey: Committee
class PublicSigningKey(sqlmodel.SQLModel, table=True):
    # The fingerprint must be stored as lowercase hex
    fingerprint: str = sqlmodel.Field(
        primary_key=True, unique=True, **example("0123456789abcdef0123456789abcdef01234567")
    )
    # The algorithm is an RFC 4880 algorithm ID
    algorithm: int = sqlmodel.Field(**example(1))
    # Key length in bits
    length: int = sqlmodel.Field(**example(4096))
    # Creation date
    created: datetime.datetime = sqlmodel.Field(
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
        **example(datetime.datetime(2025, 5, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    # Latest self signature
    latest_self_signature: datetime.datetime | None = sqlmodel.Field(
        default=None, sa_column=sqlalchemy.Column(UTCDateTime)
    )
    # Expiration date
    expires: datetime.datetime | None = sqlmodel.Field(default=None, sa_column=sqlalchemy.Column(UTCDateTime))
    # The primary UID declared in the key
    primary_declared_uid: str | None = sqlmodel.Field(**example("User <user@example.org>"))
    # The secondary UIDs declared in the key
    secondary_declared_uids: list[str] = sqlmodel.Field(
        default_factory=list,
        sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False),
        **example(["User <user@example.net>"]),
    )
    # The UID used by Apache, if available
    apache_uid: str | None = sqlmodel.Field(**example("user"))
    # The ASCII armored key
    ascii_armored_key: str = sqlmodel.Field(
        **example("-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n...\n-----END PGP PUBLIC KEY BLOCK-----\n")
    )

    # M-M: PublicSigningKey -> [Committee]
    # M-M: Committee -> [PublicSigningKey]
    committees: list[Committee] = sqlmodel.Relationship(back_populates="public_signing_keys", link_model=KeyLink)

    def model_post_init(self, _context):
        if isinstance(self.created, str):
            self.created = datetime.datetime.fromisoformat(self.created.rstrip("Z"))

        if isinstance(self.latest_self_signature, str):
            self.latest_self_signature = datetime.datetime.fromisoformat(self.latest_self_signature.rstrip("Z"))

        if isinstance(self.expires, str):
            self.expires = datetime.datetime.fromisoformat(self.expires.rstrip("Z"))


# Quarantined: Release
class Quarantined(sqlmodel.SQLModel, table=True):
    id: int | None = sqlmodel.Field(default=None, primary_key=True)

    # M-1: Quarantined -> Release
    release_key: str = sqlmodel.Field(
        foreign_key="release.key", ondelete="CASCADE", index=True, **example("example-0.0.1")
    )
    release: Release = sqlmodel.Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Quarantined.release_key]",
        },
    )

    asf_uid: str = sqlmodel.Field(**example("user"))
    prior_revision_key: str | None = sqlmodel.Field(default=None, **example("example-0.0.1 00005"))
    status: QuarantineStatus = sqlmodel.Field(
        default=QuarantineStatus.PENDING, index=True, **example(QuarantineStatus.PENDING)
    )
    token: str = sqlmodel.Field(**example("0123456789abcdef0123456789abcdef"))
    created: datetime.datetime = sqlmodel.Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
        **example(datetime.datetime(2025, 5, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    completed: datetime.datetime | None = sqlmodel.Field(
        default=None,
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=True),
        **example(datetime.datetime(2025, 5, 1, 1, 32, 3, tzinfo=datetime.UTC)),
    )
    file_metadata: list[QuarantineFileEntryV1] | None = sqlmodel.Field(
        default=None, sa_column=sqlalchemy.Column(QuarantineFileMetadataJSON)
    )
    use_check_cache: bool = sqlmodel.Field(default=True, **example(True))
    description: str | None = sqlmodel.Field(default=None, **example("Upload from web compose flow"))

    def model_post_init(self, _context):
        if isinstance(self.created, str):
            self.created = datetime.datetime.fromisoformat(self.created.rstrip("Z"))

        if isinstance(self.completed, str):
            self.completed = datetime.datetime.fromisoformat(self.completed.rstrip("Z"))

        if isinstance(self.status, str):
            self.status = QuarantineStatus(self.status)


# ReleaseFileState: Revision
class ReleaseFileState(sqlmodel.SQLModel, table=True):
    release_key: str = sqlmodel.Field(primary_key=True, **example("example-0.0.1"))
    path: str = sqlmodel.Field(primary_key=True, **example("apache-example-0.0.1-src.tar.gz"))
    since_revision_seq: int = sqlmodel.Field(primary_key=True, **example(1))
    present: bool = sqlmodel.Field(**example(True))
    content_hash: str | None = sqlmodel.Field(default=None, **example("blake3:7f83b1657ff1fc..."))
    classification: str | None = sqlmodel.Field(default=None, **example("source"))

    __table_args__ = (
        sqlalchemy.ForeignKeyConstraint(
            ["release_key", "since_revision_seq"],
            ["revision.release_key", "revision.seq"],
            ondelete="CASCADE",
        ),
        sqlalchemy.CheckConstraint(
            """
            (
                (present = 1 AND content_hash IS NOT NULL AND classification IS NOT NULL)
                OR
                (present = 0 AND content_hash IS NULL AND classification IS NULL)
            )
            """,
            name="valid_release_file_state",
        ),
    )


# ReleasePolicy: Project
class ReleasePolicy(sqlmodel.SQLModel, table=True):
    id: int = sqlmodel.Field(default=None, primary_key=True)
    mailto_addresses: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    manual_vote: bool = sqlmodel.Field(default=False)
    min_hours: int | None = sqlmodel.Field(default=None)
    release_checklist: str = sqlmodel.Field(default="")
    vote_comment_template: str = sqlmodel.Field(default="")
    start_vote_subject: str = sqlmodel.Field(default="")
    start_vote_template: str = sqlmodel.Field(default="")
    announce_release_subject: str = sqlmodel.Field(default="")
    announce_release_template: str = sqlmodel.Field(default="")
    binary_artifact_paths: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    source_artifact_paths: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    license_check_mode: LicenseCheckMode = sqlmodel.Field(default=LicenseCheckMode.BOTH)
    source_excludes_lightweight: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    source_excludes_rat: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    github_repository_name: str = sqlmodel.Field(default="")
    github_repository_branch: str = sqlmodel.Field(default="")
    github_compose_workflow_path: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    file_tag_mappings: dict[str, Any] = sqlmodel.Field(
        default_factory=dict, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    github_vote_workflow_path: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    github_finish_workflow_path: list[str] = sqlmodel.Field(
        default_factory=list, sa_column=sqlalchemy.Column(sqlalchemy.JSON, nullable=False)
    )
    preserve_download_files: bool = sqlmodel.Field(default=False)

    # 1-1: ReleasePolicy -> Project
    # 1-1: Project -C-> ReleasePolicy
    project: Project = sqlmodel.Relationship(back_populates="release_policy")

    def duplicate(self) -> "ReleasePolicy":
        # Cannot call this .copy because that's an existing BaseModel method
        return ReleasePolicy(
            mailto_addresses=list(self.mailto_addresses),
            manual_vote=self.manual_vote,
            min_hours=self.min_hours,
            release_checklist=self.release_checklist,
            vote_comment_template=self.vote_comment_template,
            start_vote_subject=self.start_vote_subject,
            start_vote_template=self.start_vote_template,
            announce_release_subject=self.announce_release_subject,
            announce_release_template=self.announce_release_template,
            binary_artifact_paths=list(self.binary_artifact_paths),
            source_artifact_paths=list(self.source_artifact_paths),
            license_check_mode=self.license_check_mode,
            source_excludes_lightweight=list(self.source_excludes_lightweight),
            source_excludes_rat=list(self.source_excludes_rat),
            github_repository_name=self.github_repository_name,
            github_repository_branch=self.github_repository_branch,
            github_compose_workflow_path=list(self.github_compose_workflow_path),
            github_vote_workflow_path=list(self.github_vote_workflow_path),
            github_finish_workflow_path=list(self.github_finish_workflow_path),
            preserve_download_files=self.preserve_download_files,
        )


# Revision: Release
class Revision(sqlmodel.SQLModel, table=True):
    key: str = sqlmodel.Field(default="", primary_key=True, unique=True, **example("example-0.0.1 00002"))

    # M-1: Revision -> Release
    # 1-M: Release -C-> [Revision]
    release_key: str | None = sqlmodel.Field(default=None, foreign_key="release.key", **example("example-0.0.1"))
    release: Release = sqlmodel.Relationship(
        back_populates="revisions",
        sa_relationship_kwargs={
            "foreign_keys": "[Revision.release_key]",
        },
    )

    seq: int = sqlmodel.Field(default=0, **example(1))
    # This was designed as a property, but it's better for it to be a column
    # That way, we can do dynamic Release.latest_revision_number construction easier
    number: str = sqlmodel.Field(default="", **example("00002"))
    asfuid: str = sqlmodel.Field(**example("user"))
    created: datetime.datetime = sqlmodel.Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
        sa_column=sqlalchemy.Column(UTCDateTime, nullable=False),
        **example(datetime.datetime(2025, 5, 1, 1, 2, 3, tzinfo=datetime.UTC)),
    )
    phase: ReleasePhase = sqlmodel.Field(**example(ReleasePhase.RELEASE_CANDIDATE_DRAFT))

    # 1-1: Revision -> Revision
    # 1-1: Revision -> Revision
    parent_key: str | None = sqlmodel.Field(default=None, foreign_key="revision.key", **example("example-0.0.1 00001"))
    parent: Optional["Revision"] = sqlmodel.Relationship(
        sa_relationship_kwargs=dict(
            remote_side=lambda: Revision.key,
            uselist=False,
            primaryjoin=lambda: Revision.parent_key == Revision.key,
            back_populates="child",
        )
    )

    # 1-1: Revision -> Revision
    # 1-1: Revision -> Revision
    child: Optional["Revision"] = sqlmodel.Relationship(back_populates="parent")

    description: str | None = sqlmodel.Field(default=None, **example("This is a description"))
    merge_base_revision_key: str | None = sqlmodel.Field(default=None, **example("example-0.0.1 00001"))
    tag: str | None = sqlmodel.Field(default=None, **example("rc1"))
    was_quarantined: bool = sqlmodel.Field(default=False, **example(False))

    @property
    def safe_number(self) -> safe.RevisionNumber:
        """Get the typesafe validated number for the revision"""
        return safe.RevisionNumber(self.number)

    def model_post_init(self, _context):
        if isinstance(self.created, str):
            self.created = datetime.datetime.fromisoformat(self.created.rstrip("Z"))

        if isinstance(self.phase, str):
            self.phase = ReleasePhase(self.phase)

    __table_args__ = (
        sqlmodel.UniqueConstraint("release_key", "seq", name="uq_revision_release_seq"),
        sqlmodel.UniqueConstraint("release_key", "number", name="uq_revision_release_number"),
    )


# WorkflowStatus:
class WorkflowStatus(sqlmodel.SQLModel, table=True):
    workflow_id: str = sqlmodel.Field(primary_key=True, index=True)
    run_id: int = sqlmodel.Field(primary_key=True, index=True)
    project_key: str = sqlmodel.Field(index=True)
    task_id: int | None = sqlmodel.Field(default=None, foreign_key="task.id", ondelete="SET NULL")
    task: Task = sqlmodel.Relationship(back_populates="workflow")
    status: str = sqlmodel.Field()
    message: str | None = sqlmodel.Field(default=None)


def revision_key(release_key: safe.ReleaseKey | str, number: str) -> str:
    return f"{release_key} {number}"


@event.listens_for(Revision, "before_insert")
def populate_revision_sequence_and_key(
    _mapper: orm.Mapper, connection: sqlalchemy.engine.Connection, revision: Revision
) -> None:
    # We require Revision.release_key to have been set
    if not revision.release_key:
        # Raise an exception
        # Otherwise, Revision.key would be "", Revision.seq 0, and Revision.number ""
        raise RuntimeError("Cannot populate revision sequence and key without release_key")

    # Allocate the next sequence number from the counter table
    # This ensures that sequence numbers are never reused, even after release deletion
    # Uses ON CONFLICT DO UPDATE with RETURNING
    upsert_stmt = (
        sqlite.insert(RevisionCounter)
        .values(release_key=revision.release_key, last_allocated_number=1)
        .on_conflict_do_update(
            index_elements=["release_key"],
            set_={"last_allocated_number": sqlalchemy.text("last_allocated_number + 1")},
        )
        .returning(sqlalchemy.literal_column("last_allocated_number"))
    )
    result = connection.execute(upsert_stmt)
    new_seq = result.scalar_one()

    revision.seq = new_seq
    revision.number = str(new_seq).zfill(5)
    revision.key = revision_key(revision.release_key, revision.number)

    # Find the actual parent for the parent_name foreign key
    # We cannot assume that the parent exists
    parent_stmt = (
        sqlmodel.select(validate_instrumented_attribute(Revision.key))
        .where(validate_instrumented_attribute(Revision.release_key) == revision.release_key)
        .order_by(sqlalchemy.desc(validate_instrumented_attribute(Revision.seq)))
        .limit(1)
    )
    parent_row = connection.execute(parent_stmt).fetchone()
    if parent_row is not None:
        revision.parent_key = parent_row[0]


@event.listens_for(Release, "before_insert")
def check_release_key(_mapper: orm.Mapper, _connection: sqlalchemy.Connection, release: Release) -> None:
    if release.key == "":
        # Quiet the type checker
        project_key = getattr(release, "project_key", None)
        version = getattr(release, "version", None)
        if (project_key is None) or (version is None):
            raise ValueError("Cannot generate release key without project_key and version")
        release.key = release_key(project_key, version)


def latest_revision_number_query(release_key: str | None = None) -> expression.ScalarSelect[str]:
    if release_key is None:
        query_release_key = Release.key
    else:
        query_release_key = release_key
    return (
        sqlmodel.select(validate_instrumented_attribute(Revision.number))
        .where(validate_instrumented_attribute(Revision.release_key) == query_release_key)
        .order_by(validate_instrumented_attribute(Revision.seq).desc())
        .limit(1)
        .scalar_subquery()
    )


@overload
def release_key(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> safe.ReleaseKey: ...


@overload
def release_key(project_key: str, version_key: str) -> str: ...


def release_key(project_key: safe.ProjectKey | str, version_key: safe.VersionKey | str) -> safe.ReleaseKey | str:
    """Return the release name for a given project and version."""
    key = f"{project_key}-{version_key}"
    if isinstance(project_key, safe.ProjectKey) and isinstance(version_key, safe.VersionKey):
        return safe.ReleaseKey(key)
    return key


def validate_instrumented_attribute(obj: Any) -> orm.InstrumentedAttribute:
    """Check if the given object is an InstrumentedAttribute."""
    if not isinstance(obj, orm.InstrumentedAttribute):
        raise ValueError(f"Object must be an orm.InstrumentedAttribute, got: {type(obj)}")
    return obj


RELEASE_LATEST_REVISION_NUMBER: Final = (
    sqlalchemy.select(validate_instrumented_attribute(Revision.number))
    .where(validate_instrumented_attribute(Revision.release_key) == Release.key)
    .order_by(validate_instrumented_attribute(Revision.seq).desc())
    .limit(1)
    .correlate_except(Revision)
    .scalar_subquery()
)


# https://github.com/fastapi/sqlmodel/issues/240#issuecomment-2074161775
Release._latest_revision_number = orm.column_property(RELEASE_LATEST_REVISION_NUMBER)
