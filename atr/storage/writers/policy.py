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

# Removing this will cause circular imports
from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING, Any, Final

import sqlmodel
import strictyaml
import strictyaml.ruamel.error as error

import atr.cycles as cycles
import atr.db as db
import atr.hashes as hashes
import atr.models as models
import atr.storage as storage
import atr.util as util

if TYPE_CHECKING:
    import atr.shared as shared

_NULLABLE_POLICY_FIELDS: Final = frozenset({"min_hours"})
_RECIPIENT_API_FIELDS: Final[dict[str, models.sql.RecipientAction]] = {
    "vote_recipients": models.sql.RecipientAction.VOTE,
    "announce_recipients": models.sql.RecipientAction.ANNOUNCE,
}
_TRUSTED_PUBLISHING_PATH_FIELDS: Final = frozenset(
    {
        "github_compose_workflow_path",
        "github_vote_workflow_path",
        "github_finish_workflow_path",
    }
)
_TRUSTED_PUBLISHING_FIELDS: Final = _TRUSTED_PUBLISHING_PATH_FIELDS | frozenset(
    {
        "github_repository_branch",
        "github_repository_name",
    }
)


class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ):
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        self.__asf_uid = write.authorisation.asf_uid


class FoundationCommitter(GeneralPublic):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationCommitter, data: db.Session):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid


class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key


class CommitteeMember(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeMember,
        data: db.Session,
        committee_key: str,
    ):
        super().__init__(write, write_as, data, committee_key)
        self.__write = write
        self.__write_as = write_as
        self.__data = data
        asf_uid = write.authorisation.asf_uid
        if asf_uid is None:
            raise storage.AccessError("Not authorized", status=403)
        self.__asf_uid = asf_uid
        self.__committee_key = committee_key

    async def edit_compose(self, form: shared.projects.ComposePolicyForm) -> None:
        project_key = form.project_key
        _, release_policy = await self.__get_or_create_policy(project_key)

        try:
            schema = strictyaml.EmptyDict() | strictyaml.MapPattern(
                strictyaml.Str(),
                strictyaml.UniqueSeq(
                    strictyaml.Str(),
                ),
            )
            atr_tags_yaml = strictyaml.load(form.file_tag_mappings, schema)
        except (strictyaml.exceptions.YAMLValidationError, error.YAMLError):
            raise ValueError("Invalid file tag mappings")
        atr_tags_data = atr_tags_yaml.data
        if not isinstance(atr_tags_data, dict):
            raise ValueError("Invalid file tag mappings")
        atr_tags_dict: dict[str, list[str]] = atr_tags_data
        _validate_file_tag_mappings(atr_tags_dict)
        license_check_mode = form.license_check_mode
        if not isinstance(license_check_mode, models.sql.LicenseCheckMode):
            raise ValueError(f"Unsupported license check mode: {license_check_mode}")
        release_policy.license_check_mode = license_check_mode
        release_policy.source_excludes_lightweight = _split_lines_verbatim(form.source_excludes_lightweight)
        release_policy.source_excludes_rat = _split_lines_verbatim(form.source_excludes_rat)
        release_policy.file_tag_mappings = atr_tags_dict

        await self.__commit_and_log(str(project_key))

    async def edit_cycle_dates(self, form: shared.projects.EditCycleDatesForm) -> None:
        project_key = form.project_key
        cycle = await self.__data.project_cycle(cycle_key=form.cycle_key).demand(
            storage.AccessError(f"Cycle {form.cycle_key} not found")
        )
        if cycle.project_key != str(project_key):
            raise storage.AccessError(f"Cycle {form.cycle_key} does not belong to project {project_key}")

        via = models.sql.validate_instrumented_attribute
        now = datetime.datetime.now(datetime.UTC)
        # Each entry: form/column attribute name and the matching event type.
        date_fields: list[tuple[str, models.sql.LifecycleEventType]] = [
            ("eod", models.sql.LifecycleEventType.EOD),
            ("eos", models.sql.LifecycleEventType.EOS),
            ("eol", models.sql.LifecycleEventType.EOL),
        ]
        for attr, event_type in date_fields:
            new_value: datetime.date | None = getattr(form, attr)
            old_value: datetime.datetime | None = getattr(cycle, attr)
            old_date = old_value.date() if old_value is not None else None
            if new_value == old_date:
                continue
            if new_value is None:
                # Forward-only: clearing a once-set date is not supported in v1.
                raise ValueError(f"{attr} cannot be cleared once set. Set a new date instead.")

            # Changing an existing date pairs a withdraw of the prior event
            # with the new event, per the lifecycle event design. The cache
            # rule (most recent of kind X) means the cache picks the new
            # event regardless, but the withdraw row carries the audit signal
            # that the prior plan no longer applies.
            if old_value is not None:
                prior_event_id = (
                    await self.__data.execute(
                        sqlmodel.select(via(models.sql.LifecycleEvent.id))
                        .where(
                            via(models.sql.LifecycleEvent.cycle_key) == cycle.cycle_key,
                            via(models.sql.LifecycleEvent.event) == event_type,
                        )
                        .order_by(via(models.sql.LifecycleEvent.published).desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if prior_event_id is not None:
                    self.__data.add(
                        models.sql.LifecycleEvent(
                            project_key=cycle.project_key,
                            cycle_key=cycle.cycle_key,
                            version_key=None,
                            event=models.sql.LifecycleEventType.WITHDRAW,
                            effective=now,
                            published=now,
                            target_event_id=prior_event_id,
                        )
                    )

            new_datetime = datetime.datetime.combine(new_value, datetime.time.min, tzinfo=datetime.UTC)
            setattr(cycle, attr, new_datetime)
            self.__data.add(
                models.sql.LifecycleEvent(
                    project_key=cycle.project_key,
                    cycle_key=cycle.cycle_key,
                    version_key=None,
                    event=event_type,
                    effective=new_datetime,
                    published=now,
                )
            )

        if cycle.lts != form.lts:
            cycle.lts = form.lts

        await self.__commit_and_log(str(project_key))

    async def edit_policy(
        self,
        project_key: models.safe.ProjectKey,
        update: models.api.PolicyUpdateArgs,
    ) -> None:
        await self._edit_policy_no_commit(project_key, update)
        await self.__commit_and_log(str(project_key))

    async def _edit_policy_no_commit(
        self,
        project_key: models.safe.ProjectKey,
        update: models.api.PolicyUpdateArgs,
    ) -> None:
        # TODO: Ideally we would centralise the validation in this method
        project, release_policy = await self.__get_or_create_policy(project_key)
        excluded_fields = {"manual_vote", "project", "vote_mode"} | set(_RECIPIENT_API_FIELDS)
        fields_to_update = update.model_fields_set - excluded_fields
        normalised_values: dict[str, Any] = {}

        self.__set_policy_vote_mode_from_api(project, release_policy, update)

        for field, action in _RECIPIENT_API_FIELDS.items():
            if field in update.model_fields_set:
                recipients = getattr(update, field)
                if recipients is None:
                    _set_recipient_defaults(release_policy, action, "", [], [])
                else:
                    _set_recipient_defaults(
                        release_policy,
                        action,
                        str(recipients.to) if recipients.to else "",
                        [str(address) for address in recipients.cc],
                        [str(address) for address in recipients.bcc],
                    )

        for field in fields_to_update:
            value = getattr(update, field)
            if (value is None) and (field not in _NULLABLE_POLICY_FIELDS):
                raise ValueError(f"Field '{field}' does not accept null")
            normalised_values[field] = value

        if ("file_tag_mappings" in fields_to_update) and (update.file_tag_mappings is not None):
            _validate_file_tag_mappings(update.file_tag_mappings)

        if ("min_hours" in fields_to_update) and (update.min_hours is not None):
            models.validation.validate_policy_min_hours(update.min_hours)

        if fields_to_update & _TRUSTED_PUBLISHING_FIELDS:
            normalised_values.update(_normalise_trusted_publishing_update(release_policy, normalised_values))

        for field in fields_to_update:
            setattr(release_policy, field, normalised_values[field])

    async def edit_finish(self, form: shared.projects.FinishPolicyForm) -> None:
        project_key = form.project_key
        project, release_policy = await self.__get_or_create_policy(project_key)

        self.__set_announce_release_subject(form.announce_release_subject or "", project, release_policy)
        self.__set_announce_release_template(form.announce_release_template or "", project, release_policy)
        _set_recipient_defaults(
            release_policy, models.sql.RecipientAction.ANNOUNCE, form.email_to, form.email_cc, form.email_bcc
        )
        release_policy.preserve_download_files = form.preserve_download_files
        release_policy.auto_archive_prior_release = form.archive_prior_release

        await self.__commit_and_log(str(project_key))

    async def edit_trusted_publishing(self, form: shared.projects.TrustedPublishingPolicyForm) -> None:
        project_key = form.project_key
        _, release_policy = await self.__get_or_create_policy(project_key)

        release_policy.github_repository_name = form.github_repository_name.strip()
        release_policy.github_repository_branch = form.github_repository_branch.strip()
        release_policy.github_compose_workflow_path = _split_lines(form.github_compose_workflow_path)
        release_policy.github_vote_workflow_path = _split_lines(form.github_vote_workflow_path)
        release_policy.github_finish_workflow_path = _split_lines(form.github_finish_workflow_path)

        await self.__commit_and_log(str(project_key))

    async def edit_version_scheme(self, form: shared.projects.EditVersionSchemeForm) -> None:
        project_key = form.project_key
        project = await self.__data.project(key=str(project_key), status=models.sql.ProjectStatus.ACTIVE).demand(
            storage.AccessError(f"Project {project_key} not found")
        )

        # Validate cycle_match by trying to compile it. Empty becomes None
        # so we don't store empty strings in nullable columns.
        cycle_match = form.cycle_match.strip() or None
        if cycle_match is not None:
            try:
                compiled = re.compile(cycle_match)
            except re.error as exc:
                raise ValueError(f"Invalid cycle_match regex: {exc}") from exc
            if compiled.groups < 1:
                raise ValueError("cycle_match must contain at least one capture group")

        version_pattern = form.version_pattern.strip() or None
        if version_pattern is not None:
            try:
                re.compile(version_pattern)
            except re.error as exc:
                raise ValueError(f"Invalid version_pattern regex: {exc}") from exc

        try:
            version_method = models.sql.VersionMethod(form.version_method)
        except ValueError as exc:
            raise ValueError(f"Unsupported version method: {form.version_method}") from exc
        project.version_method = version_method
        project.version_pattern = version_pattern
        project.cycle_match = cycle_match
        project.branch_template = form.branch_template.strip() or None

        await cycles.reassign_release_cycles(self.__data, project)
        await self.__commit_and_log(str(project_key))

    async def edit_vote(self, form: shared.projects.VotePolicyForm) -> None:
        project_key = form.project_key
        project, release_policy = await self.__get_or_create_policy(project_key)
        vote_mode = form.vote_mode
        if (vote_mode == models.sql.VoteMode.MANUAL) and (project.committee and project.committee.is_podling):
            raise storage.AccessError("Manual voting is not allowed for podlings.", status=400)

        release_policy.vote_mode = vote_mode

        if release_policy.vote_mode in {models.sql.VoteMode.EMAIL, models.sql.VoteMode.TRUSTED}:
            _set_recipient_defaults(
                release_policy, models.sql.RecipientAction.VOTE, form.email_to, form.email_cc, form.email_bcc
            )
            self.__set_min_hours(form.min_hours, project, release_policy)
            release_policy.release_checklist = form.release_checklist or ""
            release_policy.vote_comment_template = form.vote_comment_template or ""
            self.__set_start_vote_subject(form.start_vote_subject or "", project, release_policy)
            self.__set_start_vote_template(form.start_vote_template or "", project, release_policy)
            self.__set_finish_vote_template(form.finish_vote_template or "", project, release_policy)
        elif release_policy.vote_mode != models.sql.VoteMode.MANUAL:
            raise ValueError(f"Unsupported vote mode: {release_policy.vote_mode}")

        await self.__commit_and_log(str(str(project_key)))

    async def __commit_and_log(self, project_key: str) -> None:
        project = await self.__data.project(key=project_key).get()
        if project:
            project.updated = datetime.datetime.now(datetime.UTC)
            project.updated_by = self.__asf_uid
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=project_key,
        )

    async def __get_or_create_policy(
        self, project_key: models.safe.ProjectKey
    ) -> tuple[models.sql.Project, models.sql.ReleasePolicy]:
        project = await self.__data.project(
            key=str(project_key), status=models.sql.ProjectStatus.ACTIVE, _release_policy=True, _committee=True
        ).demand(storage.AccessError(f"Project {project_key} not found", status=404))

        release_policy = project.release_policy
        if release_policy is None:
            release_policy = models.sql.ReleasePolicy(project=project)
            project.release_policy = release_policy
            self.__data.add(release_policy)

        return project, release_policy

    def __set_announce_release_subject(
        self,
        submitted_subject: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_subject = submitted_subject.strip()
        current_default_text = project.policy_announce_release_subject_default
        current_default_hash = hashes.compute_sha3_256(current_default_text.encode())
        submitted_hash = hashes.compute_sha3_256(submitted_subject.encode())

        if submitted_hash == current_default_hash:
            release_policy.announce_release_subject = ""
        else:
            release_policy.announce_release_subject = submitted_subject

    def __set_announce_release_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_template = submitted_template.replace("\r\n", "\n")
        current_default_text = project.policy_announce_release_default
        current_default_hash = hashes.compute_sha3_256(current_default_text.encode())
        submitted_hash = hashes.compute_sha3_256(submitted_template.encode())

        if submitted_hash == current_default_hash:
            release_policy.announce_release_template = ""
        else:
            release_policy.announce_release_template = submitted_template

    def __set_min_hours(
        self,
        submitted_min_hours: int,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        current_system_default = project.policy_default_min_hours

        if submitted_min_hours == current_system_default:
            release_policy.min_hours = None
        else:
            release_policy.min_hours = submitted_min_hours

    def __set_policy_vote_mode_from_api(
        self,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
        update: models.api.PolicyUpdateArgs,
    ) -> None:
        if "vote_mode" in update.model_fields_set:
            if update.vote_mode is None:
                raise ValueError("Field 'vote_mode' does not accept null")
            if (update.vote_mode == models.sql.VoteMode.MANUAL) and project.committee and project.committee.is_podling:
                raise storage.AccessError("Manual voting is not allowed for podlings.", status=400)
            release_policy.vote_mode = update.vote_mode
            return
        if "manual_vote" not in update.model_fields_set:
            return
        if update.manual_vote is None:
            raise ValueError("Field 'manual_vote' does not accept null")
        if update.manual_vote:
            if project.committee and project.committee.is_podling:
                raise storage.AccessError("Manual voting is not allowed for podlings.", status=400)
            release_policy.vote_mode = models.sql.VoteMode.MANUAL
        else:
            release_policy.vote_mode = models.sql.VoteMode.EMAIL

    def __set_start_vote_subject(
        self,
        submitted_subject: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_subject = submitted_subject.strip()
        current_default_text = project.policy_start_vote_subject_default
        current_default_hash = hashes.compute_sha3_256(current_default_text.encode())
        submitted_hash = hashes.compute_sha3_256(submitted_subject.encode())

        if submitted_hash == current_default_hash:
            release_policy.start_vote_subject = ""
        else:
            release_policy.start_vote_subject = submitted_subject

    def __set_start_vote_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_template = submitted_template.replace("\r\n", "\n")
        current_default_text = project.policy_start_vote_default
        current_default_hash = hashes.compute_sha3_256(current_default_text.encode())
        submitted_hash = hashes.compute_sha3_256(submitted_template.encode())

        if submitted_hash == current_default_hash:
            release_policy.start_vote_template = ""
        else:
            release_policy.start_vote_template = submitted_template

    def __set_finish_vote_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        submitted_template = submitted_template.replace("\r\n", "\n")
        current_default_text = project.policy_finish_vote_default
        current_default_hash = hashes.compute_sha3_256(current_default_text.encode())
        submitted_hash = hashes.compute_sha3_256(submitted_template.encode())

        if submitted_hash == current_default_hash:
            release_policy.finish_vote_template = ""
        else:
            release_policy.finish_vote_template = submitted_template


def _normalise_text_list(values: list[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]


def _normalise_text_value(value: str) -> str:
    return value.strip()


def _normalise_trusted_publishing_update(
    release_policy: models.sql.ReleasePolicy,
    values: dict[str, Any],
) -> dict[str, Any]:
    normalised_values: dict[str, Any] = {}

    github_repository_name = release_policy.github_repository_name
    if "github_repository_name" in values:
        github_repository_name = _normalise_text_value(values["github_repository_name"])
        normalised_values["github_repository_name"] = github_repository_name

    github_repository_branch = release_policy.github_repository_branch
    if "github_repository_branch" in values:
        github_repository_branch = _normalise_text_value(values["github_repository_branch"])
        normalised_values["github_repository_branch"] = github_repository_branch

    all_paths: list[str] = []
    for field in sorted(_TRUSTED_PUBLISHING_PATH_FIELDS):
        paths = getattr(release_policy, field)
        if field in values:
            paths = _normalise_text_list(values[field])
            normalised_values[field] = paths
        all_paths.extend(paths)

    util.validate_trusted_publishing_constraints(github_repository_name, github_repository_branch, all_paths)

    return normalised_values


def _set_recipient_defaults(
    release_policy: models.sql.ReleasePolicy,
    action: models.sql.RecipientAction,
    to: str,
    cc: list[str],
    bcc: list[str],
) -> None:
    defaults = dict(release_policy.recipient_defaults)
    if (not to) and (not cc) and (not bcc):
        defaults.pop(action.value, None)
    else:
        defaults[action.value] = {"to": to, "cc": list(cc), "bcc": list(bcc)}
    release_policy.recipient_defaults = defaults


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def _split_lines_verbatim(text: str) -> list[str]:
    # This still excludes empty lines
    return [line for line in text.split("\n") if line]


def _validate_file_tag_mappings(mappings: dict[str, list[str]]) -> None:
    for key, values in mappings.items():
        if ".." in key:
            raise ValueError("File tag mapping keys may not contain '..'")
        for value in values:
            if ".." in value:
                raise ValueError("File tag mapping values may not contain '..'")
