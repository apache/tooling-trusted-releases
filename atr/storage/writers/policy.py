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
from typing import TYPE_CHECKING, Any, Final

import sqlalchemy
import sqlmodel

import atr.catalog_site as catalog_site
import atr.construct as construct
import atr.cycles as cycles
import atr.db as db
import atr.models as models
import atr.storage as storage

if TYPE_CHECKING:
    import atr.shared as shared

_NULLABLE_POLICY_FIELDS: Final = frozenset({"min_hours"})
_RECIPIENT_API_FIELDS: Final[dict[str, models.sql.RecipientAction]] = {
    "vote_recipients": models.sql.RecipientAction.VOTE,
    "announce_recipients": models.sql.RecipientAction.ANNOUNCE,
}
# The release policy fields each form edits, which are the ones it can override for a cycle
_COMPOSE_FIELDS: Final = frozenset(
    {"license_check_mode", "rat_excludes_url", "source_excludes_lightweight", "source_excludes_rat"}
)
_FINISH_FIELDS: Final = frozenset(
    {
        "announce_release_subject",
        "announce_release_template",
        "announce_release_template_url",
        "auto_archive_prior_release",
        "download_path_suffix",
    }
)
_VOTE_FIELDS: Final = frozenset(
    {
        "finish_vote_template",
        "finish_vote_template_url",
        "min_hours",
        "release_checklist",
        "start_vote_subject",
        "start_vote_template",
        "start_vote_template_url",
        "vote_comment_template",
        "vote_comment_template_url",
    }
)
# Policy fields that carry email templates, paired with the variables each one accepts
_TEMPLATE_VARIABLE_NAMES: Final[dict[str, frozenset[str]]] = {
    "announce_release_subject": construct.ANNOUNCE_SUBJECT_VARIABLE_NAMES,
    "announce_release_template": construct.ANNOUNCE_VARIABLE_NAMES,
    "release_checklist": construct.CHECKLIST_VARIABLE_NAMES,
    "start_vote_subject": construct.VOTE_SUBJECT_VARIABLE_NAMES,
    "start_vote_template": construct.VOTE_VARIABLE_NAMES,
    "finish_vote_template": construct.FINISH_VOTE_VARIABLE_NAMES,
}


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


class ReleaseManager(CommitteeParticipant):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsReleaseManager,
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

    async def cycle_overrides_clear(
        self, project_key: models.safe.ProjectKey, cycle: str, field: str, action: str = ""
    ) -> None:
        """Stop one cycle overriding one setting, so the project's value applies to it again."""
        await self.__get_or_create_policy(project_key)
        overrides = await self.__cycle_overrides(project_key, cycle)
        settings = dict(overrides.settings)
        if (field == "recipient_defaults") and action:
            recipients = dict(settings.get("recipient_defaults") or {})
            recipients.pop(action, None)
            settings["recipient_defaults"] = recipients
            if not recipients:
                del settings["recipient_defaults"]
        else:
            settings.pop(field, None)
        await self.__cycle_overrides_store(overrides, settings, operation="clear", field=field)

    async def cycle_overrides_edit(self, form: shared.projects.CyclePolicyForm) -> None:
        """Save a release settings form for one cycle, keeping only what differs from the project's settings."""
        project_key = form.project_key
        project, release_policy = await self.__get_or_create_policy(project_key)
        cycle = form.cycle.strip()
        overrides = await self.__data.cycle_overrides(project_key=str(project_key), cycle=cycle).get()
        if overrides is None:
            if not (await self.__cycle_in_use(project_key, cycle) or (cycles.planned_cycle(project, cycle) == cycle)):
                raise storage.AccessError(f"Cycle {cycle} cannot have settings of its own", status=400)
            overrides = models.sql.CycleOverrides(project_key=str(project_key), cycle=cycle)

        # The form is applied to a copy of what the cycle uses, and what then differs from the project is kept
        effective = overrides.applied_to(release_policy)
        fields, action = self.__apply_form(form, project, effective)
        settings = dict(overrides.settings)
        for field in fields:
            value = getattr(effective, field)
            if value == getattr(release_policy, field):
                settings.pop(field, None)
            else:
                settings[field] = value.value if isinstance(value, models.sql.LicenseCheckMode) else value
        if action is not None:
            _recipient_override(settings, release_policy, effective, action)
        await self.__cycle_overrides_store(overrides, settings, operation="edit", field=None)

    async def cycle_overrides_move(self, project_key: models.safe.ProjectKey, cycle: str, target: str) -> None:
        """Move one cycle's overrides to another cycle that has none."""
        project, _ = await self.__get_or_create_policy(project_key)
        overrides = await self.__cycle_overrides(project_key, cycle)
        if (target == cycle) or (not await self.__cycle_in_use(project_key, target)):
            raise storage.AccessError(f"Project {project_key} has no cycle {target} to move these to", status=400)
        if await self.__data.cycle_overrides(project_key=str(project_key), cycle=target).get() is not None:
            raise storage.AccessError(
                f"Cycle {target} has settings of its own. Remove them before moving these to it.", status=409
            )
        moved = models.sql.CycleOverrides(project_key=str(project_key), cycle=target, settings=dict(overrides.settings))
        moved.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
        project.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
        await self.__data.delete(overrides)
        self.__data.add(moved)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid, project_key=str(project_key), cycle=cycle, operation="move", target=target
        )

    async def cycle_overrides_remove(self, project_key: models.safe.ProjectKey, cycle: str) -> None:
        """Remove every override one cycle has."""
        await self.__get_or_create_policy(project_key)
        overrides = await self.__cycle_overrides(project_key, cycle)
        await self.__cycle_overrides_store(overrides, {}, operation="remove", field=None)

    async def edit_compose(self, form: shared.projects.ComposePolicyForm) -> None:
        project_key = form.project_key
        _, release_policy = await self.__get_or_create_policy(project_key)
        self.__apply_compose(form, release_policy)
        await self.__commit_and_log(str(project_key))

    async def edit_cycle_dates(self, form: shared.projects.EditCycleDatesForm) -> None:
        project_key = form.project_key
        await self.__validate_project_in_committee(project_key)
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
                        models.sql.LifecycleEvent.withdrawing(
                            prior_event_id,
                            project_key=cycle.project_key,
                            cycle_key=cycle.cycle_key,
                            version_key=None,
                            when=now,
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

        await catalog_site.queue_regeneration(self.__data, self.__asf_uid, str(project_key))
        await self.__commit_and_log(str(project_key))

    async def edit_policy(
        self,
        project_key: models.safe.ProjectKey,
        update: models.api.PolicyUpdateArgs,
    ) -> None:
        await self.__data.begin_immediate()
        self.__data.expire_all()
        await self._edit_policy_no_commit(project_key, update)
        await self.__commit_and_log(str(project_key))

    async def _edit_policy_no_commit(
        self,
        project_key: models.safe.ProjectKey,
        update: models.api.PolicyUpdateArgs,
    ) -> None:
        _, release_policy = await _get_or_create_policy(self.__data, project_key)
        _apply_policy_update(release_policy, update)

    async def edit_finish(self, form: shared.projects.FinishPolicyForm) -> None:
        project_key = form.project_key
        project, release_policy = await self.__get_or_create_policy(project_key)
        self.__apply_finish(form, project, release_policy)
        await self.__commit_and_log(str(project_key))

    async def edit_version_scheme(self, form: shared.projects.EditVersionSchemeForm) -> None:
        project_key = form.project_key
        project, release_policy = await self.__get_or_create_policy(project_key)

        try:
            version_method = models.sql.VersionMethod(form.version_method)
        except ValueError as exc:
            raise ValueError(f"Unsupported version method: {form.version_method}") from exc

        # Calver projects describe cycles through a date format; everyone else
        # through a cycle_match regex. Whichever applies, empty becomes None so
        # we don't store empty strings in nullable columns.
        if version_method is models.sql.VersionMethod.CALVER:
            calver_format = form.calver_format.strip() or None
            cycle_match = _calver_cycle_match(calver_format)
        else:
            calver_format = None
            cycle_match = form.cycle_match.strip() or None
            if cycle_match is not None:
                try:
                    compiled = models.validation.compile_project_pattern(cycle_match, captures=True)
                except ValueError as exc:
                    raise ValueError(f"Invalid cycle_match regex: {exc}") from exc
                if compiled.groups < 1:
                    raise ValueError("cycle_match must contain at least one capture group")

        version_pattern = form.version_pattern.strip() or None
        if version_pattern is not None:
            try:
                models.validation.compile_project_pattern(version_pattern)
            except ValueError as exc:
                raise ValueError(f"Invalid version_pattern regex: {exc}") from exc

        if error := models.validation.download_page_cycle_error(project.download_page, cycle_match):
            raise ValueError(error)
        project.version_method = version_method
        project.version_pattern = version_pattern
        project.cycle_match = cycle_match
        project.calver_format = calver_format
        project.branch_template = form.branch_template.strip() or None
        release_policy.require_version_in_filenames = form.require_version_in_filenames

        await cycles.reassign_release_cycles(self.__data, project)
        await catalog_site.queue_regeneration(self.__data, self.__asf_uid, str(project_key))
        await self.__commit_and_log(str(project_key))

    async def edit_vote(self, form: shared.projects.VotePolicyForm) -> None:
        project_key = form.project_key
        project, release_policy = await self.__get_or_create_policy(project_key)
        self.__apply_vote(form, project, release_policy)
        await self.__commit_and_log(str(str(project_key)))

    def __apply_compose(
        self, form: shared.projects.ComposePolicyForm, release_policy: models.sql.ReleasePolicy
    ) -> None:
        license_check_mode = form.license_check_mode
        if not isinstance(license_check_mode, models.sql.LicenseCheckMode):
            raise ValueError(f"Unsupported license check mode: {license_check_mode}")
        release_policy.license_check_mode = license_check_mode
        release_policy.source_excludes_lightweight = _split_lines_verbatim(form.source_excludes_lightweight)
        release_policy.source_excludes_rat = _split_lines_verbatim(form.source_excludes_rat)
        release_policy.rat_excludes_url = form.rat_excludes_url.strip()

    def __apply_finish(
        self,
        form: shared.projects.FinishPolicyForm,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        self.__set_announce_release_subject(form.announce_release_subject or "", project, release_policy)
        self.__set_announce_release_template(form.announce_release_template or "", project, release_policy)
        release_policy.announce_release_template_url = form.announce_release_template_url.strip()
        _set_recipient_defaults(
            release_policy, models.sql.RecipientAction.ANNOUNCE, form.email_to, form.email_cc, form.email_bcc
        )
        release_policy.auto_archive_prior_release = form.archive_prior_release
        release_policy.download_path_suffix = form.download_path_suffix.strip()

    def __apply_form(
        self,
        form: shared.projects.CyclePolicyForm,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> tuple[frozenset[str], models.sql.RecipientAction | None]:
        # Returns the policy fields the form covers, and the action whose recipients it sets.
        # The forms can't be imported here, so the variant tells them apart
        if form.variant == "compose":
            self.__apply_compose(form, release_policy)
            return _COMPOSE_FIELDS, None
        if form.variant == "finish":
            self.__apply_finish(form, project, release_policy)
            return _FINISH_FIELDS, models.sql.RecipientAction.ANNOUNCE
        self.__apply_vote(form, project, release_policy)
        return _VOTE_FIELDS, models.sql.RecipientAction.VOTE

    def __apply_vote(
        self,
        form: shared.projects.VotePolicyForm,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        _set_recipient_defaults(
            release_policy, models.sql.RecipientAction.VOTE, form.email_to, form.email_cc, form.email_bcc
        )
        self.__set_min_hours(form.min_hours, project, release_policy)
        release_policy.release_checklist = form.release_checklist or ""
        release_policy.vote_comment_template = form.vote_comment_template or ""
        release_policy.vote_comment_template_url = form.vote_comment_template_url.strip()
        self.__set_start_vote_subject(form.start_vote_subject or "", project, release_policy)
        self.__set_start_vote_template(form.start_vote_template or "", project, release_policy)
        release_policy.start_vote_template_url = form.start_vote_template_url.strip()
        self.__set_finish_vote_template(form.finish_vote_template or "", project, release_policy)
        release_policy.finish_vote_template_url = form.finish_vote_template_url.strip()

    async def __commit_and_log(self, project_key: str) -> None:
        project = await self.__data.project(key=project_key).get()
        if project:
            project.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=project_key,
        )

    async def __cycle_in_use(self, project_key: models.safe.ProjectKey, cycle: str) -> bool:
        return await _cycle_in_use(self.__data, project_key, cycle)

    async def __cycle_overrides(self, project_key: models.safe.ProjectKey, cycle: str) -> models.sql.CycleOverrides:
        return await self.__data.cycle_overrides(project_key=str(project_key), cycle=cycle).demand(
            storage.AccessError(f"Cycle {cycle} of project {project_key} has no settings of its own", status=404)
        )

    async def __cycle_overrides_store(
        self, overrides: models.sql.CycleOverrides, settings: dict[str, Any], *, operation: str, field: str | None
    ) -> None:
        project = await self.__data.project(key=overrides.project_key).demand(
            storage.AccessError(f"Project {overrides.project_key} not found", status=404)
        )
        project.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
        # A cycle with nothing left to override has no row at all
        if settings:
            overrides.settings = settings
            overrides.mark_updated(by=self.__asf_uid, update_type=models.sql.UpdateType.MANUAL)
            self.__data.add(overrides)
        elif sqlalchemy.inspect(overrides).persistent:
            await self.__data.delete(overrides)
        await self.__data.commit()
        self.__write_as.append_to_audit_log(
            asf_uid=self.__asf_uid,
            project_key=overrides.project_key,
            cycle=overrides.cycle,
            operation=operation,
            field=field,
            overridden=", ".join(sorted(settings)),
        )

    async def __get_or_create_policy(
        self, project_key: models.safe.ProjectKey
    ) -> tuple[models.sql.Project, models.sql.ReleasePolicy]:
        await self.__data.begin_immediate()
        self.__data.expire_all()
        project, release_policy = await _get_or_create_policy(self.__data, project_key)
        if project.committee_key != self.__committee_key:
            raise storage.AccessError(f"Project {project_key} is not in committee {self.__committee_key}", status=403)
        storage.ensure_project_active(project)
        return project, release_policy

    async def __validate_project_in_committee(self, project_key: models.safe.ProjectKey) -> models.sql.Project:
        project = await self.__data.project(
            key=str(project_key), status=models.sql.ProjectStatus.ACTIVE, _committee=True
        ).demand(storage.AccessError(f"Project {project_key} not found", status=404))
        if project.committee_key != self.__committee_key:
            raise storage.AccessError(f"Project {project_key} is not in committee {self.__committee_key}", status=403)
        storage.ensure_project_active(project)
        return project

    def __set_announce_release_subject(
        self,
        submitted_subject: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        release_policy.announce_release_subject = _collapse_to_default(
            submitted_subject, project.policy_announce_release_subject_default
        )

    def __set_announce_release_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        release_policy.announce_release_template = _collapse_to_default(
            submitted_template, project.policy_announce_release_default
        )

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

    def __set_start_vote_subject(
        self,
        submitted_subject: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        release_policy.start_vote_subject = _collapse_to_default(
            submitted_subject, project.policy_start_vote_subject_default
        )

    def __set_start_vote_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        release_policy.start_vote_template = _collapse_to_default(submitted_template, project.policy_start_vote_default)

    def __set_finish_vote_template(
        self,
        submitted_template: str,
        project: models.sql.Project,
        release_policy: models.sql.ReleasePolicy,
    ) -> None:
        release_policy.finish_vote_template = _collapse_to_default(
            submitted_template, project.policy_finish_vote_default
        )


class CommitteeMember(ReleaseManager):
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


class FoundationAdmin(FoundationCommitter):
    def __init__(self, write: storage.Write, write_as: storage.WriteAsFoundationAdmin, data: db.Session):
        super().__init__(write, write_as, data)
        self.__data = data

    async def cycle_overrides_sync_no_commit(
        self,
        project_key: models.safe.ProjectKey,
        cycle_args: dict[str, models.api.ProjectConfigCycleArgs],
        *,
        asf_uid: str,
        update_type: models.sql.UpdateType,
    ) -> list[str]:
        """Make these the project's cycle settings, and return the cycles whose settings changed."""
        project, release_policy = await _get_or_create_policy(self.__data, project_key)
        rows = await self.__data.cycle_overrides(project_key=str(project_key)).all()
        existing = {overrides.cycle: overrides for overrides in rows}
        changed: list[str] = []
        for cycle, args in cycle_args.items():
            overrides = existing.get(cycle)
            if overrides is None:
                if not (await _cycle_in_use(self.__data, project_key, cycle) or _cycle_allowed(project, cycle)):
                    raise ValueError(f"Cycle {cycle} cannot have settings of its own")
                overrides = models.sql.CycleOverrides(project_key=str(project_key), cycle=cycle)
            # A cycle has only what it is given here, whatever it had before
            settings = _cycle_settings_from_args(project_key, release_policy, args)
            if settings == overrides.settings:
                continue
            changed.append(cycle)
            if settings:
                overrides.settings = settings
                overrides.mark_updated(by=asf_uid, update_type=update_type)
                self.__data.add(overrides)
            else:
                await self.__data.delete(overrides)
        for cycle in sorted(set(existing) - set(cycle_args)):
            changed.append(cycle)
            await self.__data.delete(existing[cycle])
        return changed

    async def edit_no_commit(
        self,
        project_key: models.safe.ProjectKey,
        update: models.api.PolicyUpdateArgs,
    ) -> None:
        _, release_policy = await _get_or_create_policy(self.__data, project_key)
        _apply_policy_update(release_policy, update)


def _apply_policy_update(release_policy: models.sql.ReleasePolicy, update: models.api.PolicyUpdateArgs) -> None:
    excluded_fields = {"project", "vote_mode"} | set(_RECIPIENT_API_FIELDS)
    fields_to_update = update.model_fields_set - excluded_fields
    normalised_values: dict[str, Any] = {}

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
        normalised_values[field] = _normalise_url_value(field, value)

    if ("file_tag_mappings" in fields_to_update) and (update.file_tag_mappings is not None):
        _validate_file_tag_mappings(update.file_tag_mappings)

    if ("min_hours" in fields_to_update) and (update.min_hours is not None):
        models.validation.validate_policy_min_hours(update.min_hours)

    _validate_template_fields(update, fields_to_update)

    for field in fields_to_update:
        setattr(release_policy, field, normalised_values[field])


def _calver_cycle_match(calver_format: str | None) -> str | None:
    # The format string's cycle group compiles to a cycle_match regex, so we don't
    # need special calver membership handling. A string
    # with no group (a linear project) leaves cycle_match unset.
    if calver_format is None:
        return None
    try:
        models.calver.validate(calver_format)
    except ValueError as exc:
        raise ValueError(f"Invalid calver format: {exc}") from exc
    return models.calver.cycle_regex(calver_format)


def _collapse_to_default(submitted: str, default_text: str) -> str:
    # Storing "" lets the project default apply. We compare ignoring line endings and
    # surrounding whitespace, otherwise a default that has been through a textarea - which
    # can drop the trailing newline - fails the match and gets saved as a spurious custom value.
    submitted = submitted.replace("\r\n", "\n").strip()
    if submitted == default_text.strip():
        return ""
    return submitted


def _cycle_allowed(project: models.sql.Project, cycle: str) -> bool:
    return cycles.planned_cycle(project, cycle) == cycle


async def _cycle_in_use(data: db.Session, project_key: models.safe.ProjectKey, cycle: str) -> bool:
    # A cycle is in use while some release of the project is in it
    if not cycles.can_override(cycle):
        return False
    in_cycle = data.release(project_key=str(project_key), _project=False, _committee=False).where(
        models.sql.Release.cycle_key == f"{project_key}-{cycle}"
    )
    return bool(await in_cycle.limit(1).all())


def _cycle_settings_from_args(
    project_key: models.safe.ProjectKey,
    release_policy: models.sql.ReleasePolicy,
    args: models.api.ProjectConfigCycleArgs,
) -> dict[str, Any]:
    """The settings to store for a cycle that is given these args, which is only what differs from the project's."""
    effective = release_policy.duplicate()
    # A null is the project's value, the same as a key that is left out
    given = {field: value for field, value in args.model_dump(exclude_unset=True).items() if value is not None}
    _apply_policy_update(effective, models.api.PolicyUpdateArgs(project=project_key, **given))
    settings: dict[str, Any] = {}
    for field in sorted(models.sql.CYCLE_OVERRIDE_FIELDS - {"recipient_defaults"}):
        value = getattr(effective, field)
        if value != getattr(release_policy, field):
            settings[field] = value.value if isinstance(value, models.sql.LicenseCheckMode) else value
    for action in _RECIPIENT_API_FIELDS.values():
        _recipient_override(settings, release_policy, effective, action)
    return settings


async def _get_or_create_policy(
    data: db.Session,
    project_key: models.safe.ProjectKey,
) -> tuple[models.sql.Project, models.sql.ReleasePolicy]:
    project = await data.project(
        key=str(project_key), status=models.sql.ProjectStatus.ACTIVE, _release_policy=True, _committee=True
    ).demand(storage.AccessError(f"Project {project_key} not found", status=404))

    release_policy = project.release_policy
    if release_policy is None:
        release_policy = models.sql.ReleasePolicy(project=project)
        project.release_policy = release_policy
        data.add(release_policy)

    return project, release_policy


def _normalise_url_value(field: str, value: Any) -> Any:
    if isinstance(value, str) and field.endswith("_url"):
        return value.strip()
    return value


def _recipient_override(
    settings: dict[str, Any],
    release_policy: models.sql.ReleasePolicy,
    effective: models.sql.ReleasePolicy,
    action: models.sql.RecipientAction,
) -> None:
    # No stored recipients and an empty entry read the same, so a cycle that clears the project's
    # recipients stores an empty entry
    empty = {"to": "", "cc": [], "bcc": []}
    entry = effective.recipient_defaults.get(action.value) or empty
    recipients = dict(settings.get("recipient_defaults") or {})
    if entry == (release_policy.recipient_defaults.get(action.value) or empty):
        recipients.pop(action.value, None)
    else:
        recipients[action.value] = entry
    settings.pop("recipient_defaults", None)
    if recipients:
        settings["recipient_defaults"] = recipients


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


def _validate_template_fields(update: models.api.PolicyUpdateArgs, fields_to_update: set[str]) -> None:
    for template_field, names in _TEMPLATE_VARIABLE_NAMES.items():
        if template_field in fields_to_update:
            value = getattr(update, template_field)
            if value is not None:
                construct.validate_template_variables(value, names)
