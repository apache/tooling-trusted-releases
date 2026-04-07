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

from typing import Annotated, Any

import pydantic

from . import mail, safe, schema


class ConvertCycloneDX(schema.Strict):
    """Arguments for the task to convert an artifact to a CycloneDX SBOM."""

    artifact_path: safe.StatePath = schema.description("Absolute path to the artifact")
    output_path: safe.StatePath = schema.description("Absolute path where the generated SBOM JSON should be written")
    revision: safe.RevisionNumber = schema.description("Revision number")


class DistributionWorkflow(schema.Strict):
    """Arguments for the task to start a GitHub Actions distribution workflow."""

    namespace: str = schema.description("Namespace to distribute to")
    package: str = schema.description("Package to distribute")
    version: str = schema.description("Version to distribute")
    staging: bool = schema.description("Whether this is a staging distribution")
    project_key: str = schema.description("Project name in ATR")
    version_key: str = schema.description("Version name in ATR")
    phase: str = schema.description("Release phase in ATR")
    asf_uid: str = schema.description("ASF UID of the user triggering the workflow")
    committee_key: str = schema.description("Committee name in ATR")
    platform: str = schema.description("Distribution platform")
    arguments: dict[str, str] = schema.description("Workflow arguments")
    name: str = schema.description("Name of the run")


class DistributionStatusCheckArgs(schema.Strict):
    """Arguments for the task to re-check distribution statuses."""

    next_schedule_seconds: int = pydantic.Field(default=0, description="The next scheduled time")
    asf_uid: str = schema.description("ASF UID of the user triggering the workflow")


class FileArgs(schema.Strict):
    """Arguments for SBOM file processing tasks."""

    project_key: safe.ProjectKey = schema.description("Project name")
    version_key: safe.VersionKey = schema.description("Version name")
    revision_number: safe.RevisionNumber = schema.description("Revision number")
    file_path: safe.RelPath = schema.description("Relative path to the SBOM file")
    asf_uid: str | None = None


class ScoreArgs(FileArgs):
    """Arguments for SBOM file scoring tasks."""

    previous_release_version: safe.VersionKey | None = schema.description("Previous release version")


class GenerateCycloneDX(schema.Strict):
    """Arguments for the task to generate a CycloneDX SBOM from an artifact."""

    artifact_path: safe.StatePath = schema.description("Absolute path to the artifact")
    output_path: safe.StatePath = schema.description("Absolute path where the generated SBOM JSON should be written")


class ImportFile(schema.Strict):
    """Import a KEYS file from a draft release candidate revision."""

    asf_uid: str
    project_key: safe.ProjectKey
    version_key: safe.VersionKey


class Initiate(schema.Strict):
    """Arguments for the task to start a vote."""

    release_key: str = schema.description("The name of the release to vote on")
    email_to: pydantic.EmailStr = schema.description("The mailing list To address")
    vote_duration: int = schema.description("Duration of the vote in hours")
    initiator_id: str = schema.description("ASF ID of the vote initiator")
    initiator_fullname: str = schema.description("Full name of the vote initiator")
    subject: str = schema.description("Subject line for the vote email")
    body: str = schema.description("Body content for the vote email")
    email_cc: list[pydantic.EmailStr] = schema.factory(list)
    email_bcc: list[pydantic.EmailStr] = schema.factory(list)


class MaintenanceArgs(schema.Strict):
    """Arguments for the task to perform scheduled maintenance."""

    asf_uid: str = schema.description("The ASF UID of the user triggering the maintenance")
    next_schedule_seconds: int = pydantic.Field(default=0, description="The next scheduled time")


class QuarantineArchiveEntry(schema.Strict):
    """An archive entry in a quarantine validation task."""

    rel_path: str
    content_hash: str


class QuarantineValidate(schema.Strict):
    """Arguments for the task to validate a quarantined upload."""

    quarantined_id: int
    archives: list[QuarantineArchiveEntry]


def _ensure_footer_enum(value: Any) -> mail.MailFooterCategory | None:
    if isinstance(value, mail.MailFooterCategory):
        return value
    if isinstance(value, str):
        return mail.MailFooterCategory(value)
    else:
        return None


class Send(schema.Strict):
    """Arguments for the task to send an email."""

    email_sender: pydantic.EmailStr = schema.description("The email address of the sender")
    email_to: pydantic.EmailStr = schema.description("The email To address")
    subject: str = schema.description("The subject of the email")
    body: str = schema.description("The body of the email")
    in_reply_to: str | None = schema.description("The message ID of the email to reply to")
    email_cc: list[pydantic.EmailStr] = schema.factory(list)
    email_bcc: list[pydantic.EmailStr] = schema.factory(list)
    footer_category: Annotated[mail.MailFooterCategory, pydantic.BeforeValidator(_ensure_footer_enum)] = (
        schema.description("The category of email footer to include")
    )


class SvnImport(schema.Strict):
    """Arguments for the task to import files from SVN."""

    svn_url: safe.RelPath
    revision: str
    target_subdirectory: str | None
    project_key: safe.ProjectKey
    version_key: safe.VersionKey
    asf_uid: str


class Update(schema.Strict):
    """Arguments for the task to update metadata from remote data sources."""

    asf_uid: str = schema.description("The ASF UID of the user triggering the update")
    next_schedule_seconds: int = pydantic.Field(default=0, description="The next scheduled time")


class WorkflowStatusCheck(schema.Strict):
    """Arguments for the task to check the status of a GitHub Actions workflow."""

    run_id: int | None = schema.description("Run ID")
    next_schedule_seconds: int = pydantic.Field(default=0, description="The next scheduled time")
    asf_uid: str = schema.description("ASF UID of the user triggering the workflow")
