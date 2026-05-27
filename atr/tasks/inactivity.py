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

from __future__ import annotations

import dataclasses
import datetime
import enum
from typing import TYPE_CHECKING, Final

import sqlmodel

import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.log as log
import atr.mail as mail
import atr.models.safe as safe
import atr.models.sql as sql
import atr.storage as storage

if TYPE_CHECKING:
    from collections.abc import Sequence

NOTICE_KIND_PREVIEW_ESCALATION: Final[str] = "preview_escalation"
NOTICE_KIND_WARNING: Final[str] = "warning"

_DELETION_ENABLED: Final[bool] = False
_UNFINISHED_PHASES: Final[frozenset[sql.ReleasePhase]] = frozenset(
    {
        sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        sql.ReleasePhase.RELEASE_CANDIDATE,
        sql.ReleasePhase.RELEASE_PREVIEW,
    }
)


class Decision(enum.StrEnum):
    DELETE_CANDIDATE = "delete_candidate"
    PREVIEW_ESCALATE = "preview_escalate"
    SKIP = "skip"
    WARN = "warn"


@dataclasses.dataclass(frozen=True)
class Plan:
    release_key: str
    project_key: str
    version: str
    phase: sql.ReleasePhase
    activity_at: datetime.datetime
    decision: Decision


async def apply_plan(plan: Plan) -> None:
    match plan.decision:
        case Decision.SKIP:
            return
        case Decision.WARN:
            await _send_warning(plan)
        case Decision.PREVIEW_ESCALATE:
            await _send_preview_escalation(plan)
        case Decision.DELETE_CANDIDATE:
            await _delete_or_dry_run(plan)


def classify(
    release: sql.Release,
    *,
    now: datetime.datetime,
) -> Plan:
    activity_at = release.activity_at
    age_days = (now - activity_at).days

    if release.phase not in _UNFINISHED_PHASES:
        return _plan(release, activity_at, Decision.SKIP)

    if age_days < constants.INACTIVITY_WARNING_DAYS:
        return _plan(release, activity_at, Decision.SKIP)

    if age_days < constants.INACTIVITY_DELETE_DAYS:
        return _plan(release, activity_at, Decision.WARN)

    if release.phase == sql.ReleasePhase.RELEASE_PREVIEW:
        return _plan(release, activity_at, Decision.PREVIEW_ESCALATE)
    return _plan(release, activity_at, Decision.DELETE_CANDIDATE)


def deletion_enabled() -> bool:
    return _DELETION_ENABLED


def private_committee_list(committee_key: str) -> str:
    return f"private@{committee_key}.apache.org"


async def run_scan() -> Sequence[Plan]:
    now = datetime.datetime.now(datetime.UTC)
    plans: list[Plan] = []
    via = sql.validate_instrumented_attribute

    async with db.session() as data:
        query = (
            sqlmodel.select(sql.Release)
            .join(sql.Project, via(sql.Release.project_key) == via(sql.Project.key))
            .where(via(sql.Release.phase).in_(list(_UNFINISHED_PHASES)))
            .where(via(sql.Project.status) == sql.ProjectStatus.ACTIVE)
            .options(db.joined_load_nested(sql.Release.project, sql.Project.committee))
        )
        result = await data.execute(query)
        releases: Sequence[sql.Release] = list(result.scalars().all())

        for release in releases:
            try:
                plan = classify(release, now=now)
            except Exception:
                log.exception(f"Inactivity classification failed for release {release.key!r}")
                continue
            plans.append(plan)
            if plan.decision != Decision.SKIP:
                log.info(
                    "Inactivity scan decision"
                    f" release={plan.release_key!r}"
                    f" phase={plan.phase.value}"
                    f" decision={plan.decision.value}"
                    f" activity_at={plan.activity_at.isoformat()}"
                )

    counts: dict[str, int] = {}
    for plan in plans:
        counts[plan.decision.value] = counts.get(plan.decision.value, 0) + 1
    log.info(f"Inactivity scan summary total={len(plans)} deletion_enabled={_DELETION_ENABLED} counts={counts}")
    return plans


def thresholds() -> tuple[int, int]:
    return constants.INACTIVITY_WARNING_DAYS, constants.INACTIVITY_DELETE_DAYS


async def warning_recipients_for(release: sql.Release, data: db.Session) -> list[str]:
    via = sql.validate_instrumented_attribute
    query = (
        sqlmodel.select(via(sql.Revision.asfuid))
        .where(sql.Revision.release_key == release.key)
        .where(via(sql.Revision.asfuid) != constants.SYSTEM_SERVICE_UID)
        .distinct()
    )
    result = await data.execute(query)
    uids: list[str] = sorted({row for row in result.scalars().all() if row})
    if uids:
        return [f"{uid}@apache.org" for uid in uids]
    return [private_committee_list(_committee_key_for(release))]


def _committee_key_for(release: sql.Release) -> str:
    project = release.project
    if project.committee is not None:
        return project.committee.key
    return project.committee_key or release.project_key


async def _delete_or_dry_run(plan: Plan) -> None:
    async with storage.write_as_system() as release_admin:
        error = await release_admin.delete_inactive(
            project_key=safe.ProjectKey(plan.project_key),
            version=safe.VersionKey(plan.version),
            dry_run=not _DELETION_ENABLED,
        )
    if error:
        log.info(f"Inactivity deletion skipped for {plan.release_key!r}: {error}")
        return
    if _DELETION_ENABLED:
        log.info(f"Inactivity deletion succeeded for {plan.release_key!r}")
    else:
        log.info(
            "Inactivity dry-run would_delete"
            f" release={plan.release_key!r}"
            f" phase={plan.phase.value}"
            f" activity_at={plan.activity_at.isoformat()}"
        )


async def _load_release(data: db.Session, release_key: str) -> sql.Release | None:
    return await data.release(
        key=release_key,
        _committee=True,
    ).get()


def _notice_already_recorded(stored: str | None, expected: str) -> bool:
    return stored == expected


def _notice_key(kind: str, activity_at: datetime.datetime) -> str:
    return f"{kind}:{activity_at.isoformat()}"


def _plan(release: sql.Release, activity_at: datetime.datetime, decision: Decision) -> Plan:
    return Plan(
        release_key=release.key,
        project_key=release.project_key,
        version=release.version,
        phase=release.phase,
        activity_at=activity_at,
        decision=decision,
    )


def _plan_still_current(plan: Plan, release: sql.Release, *, expected_kind: str) -> bool:
    project_status = release.project.status
    if project_status != sql.ProjectStatus.ACTIVE:
        log.info(
            f"Inactivity {expected_kind} skipped for {plan.release_key!r}:"
            f" project {release.project_key!r} status changed to {project_status.value}"
        )
        return False
    if expected_kind == NOTICE_KIND_PREVIEW_ESCALATION:
        expected_phase_ok = release.phase == sql.ReleasePhase.RELEASE_PREVIEW
        activity_ok = release.activity_at == plan.activity_at
    else:
        expected_phase_ok = release.phase in _UNFINISHED_PHASES
        activity_ok = release.activity_at == plan.activity_at
    if not expected_phase_ok:
        log.info(f"Inactivity {expected_kind} skipped for {plan.release_key!r}: phase changed to {release.phase.value}")
        return False
    if not activity_ok:
        log.info(
            f"Inactivity {expected_kind} skipped for {plan.release_key!r}:"
            f" activity_at advanced to {release.activity_at.isoformat()}"
        )
        return False
    return True


def _preview_escalation_body(plan: Plan) -> str:
    _warning_days, delete_days = thresholds()
    return (
        f"This release preview has been inactive for at least {delete_days} days.\n\n"
        f"Project: {plan.project_key}\n"
        f"Version: {plan.version}\n"
        f"Phase: {plan.phase.value}\n\n"
        f"Release previews are not automatically deleted. The PMC is being notified\n"
        f"under the inactive-release policy (issue #871) so the finish step can be\n"
        f"completed or the release can be cancelled."
    )


async def _record_notice_sent(release_key: str, kind: str, activity_at: datetime.datetime) -> None:
    async with db.session() as data:
        release = await data.release(key=release_key).get()
        if release is None:
            return
        if release.activity_at != activity_at:
            return
        release.inactivity_notice_key = _notice_key(kind, activity_at)
        await data.commit()


async def _send_email(*, recipient: str, subject: str, body: str) -> bool:
    message = mail.Message(
        email_sender=mail.NOREPLY_EMAIL_ADDRESS,
        email_to=recipient,
        subject=subject,
        body=body,
    )
    try:
        if config.is_dev_environment():
            log.info(f"Dev environment detected, not sending inactivity email to {recipient!r}")
            mid: str | None = message.message_id
            errors: list[str] = []
        else:
            mid, errors = await mail.send(message, mail.MailFooterCategory.AUTO)
    except Exception:
        log.exception(f"Inactivity mail send failed for {recipient!r} subject={subject!r}")
        return False
    storage.audit(
        asf_uid=constants.SYSTEM_SERVICE_UID,
        email_sender=message.email_sender,
        email_to=message.email_to,
        subject=message.subject,
        mid=mid,
        errors=", ".join(errors) if errors else "",
    )
    if errors:
        log.warning(
            f"Inactivity mail to {recipient!r} reported SMTP errors {errors};"
            f" treating as unsuccessful so the notice marker is not recorded"
        )
        return False
    return True


async def _send_preview_escalation(plan: Plan) -> None:
    expected = _notice_key(NOTICE_KIND_PREVIEW_ESCALATION, plan.activity_at)
    async with db.session() as data:
        release = await _load_release(data, plan.release_key)
        if release is None:
            log.warning(f"Inactivity escalation: release {plan.release_key!r} no longer exists")
            return
        if not _plan_still_current(plan, release, expected_kind=NOTICE_KIND_PREVIEW_ESCALATION):
            return
        if _notice_already_recorded(release.inactivity_notice_key, expected):
            log.info(
                f"Inactivity preview escalation already sent for {plan.release_key!r} at {plan.activity_at.isoformat()}"
            )
            return
        recipient = private_committee_list(_committee_key_for(release))

    body = _preview_escalation_body(plan)
    sent = await _send_email(
        recipient=recipient,
        subject=f"[NOTICE] Release preview inactive: {plan.project_key} {plan.version}",
        body=body,
    )
    if sent:
        await _record_notice_sent(plan.release_key, NOTICE_KIND_PREVIEW_ESCALATION, plan.activity_at)


async def _send_warning(plan: Plan) -> None:
    expected = _notice_key(NOTICE_KIND_WARNING, plan.activity_at)
    async with db.session() as data:
        release = await _load_release(data, plan.release_key)
        if release is None:
            log.warning(f"Inactivity warning: release {plan.release_key!r} no longer exists")
            return
        if not _plan_still_current(plan, release, expected_kind=NOTICE_KIND_WARNING):
            return
        if _notice_already_recorded(release.inactivity_notice_key, expected):
            log.info(f"Inactivity warning already sent for {plan.release_key!r} at {plan.activity_at.isoformat()}")
            return
        recipients = await warning_recipients_for(release, data)

    body = _warning_body(plan)
    subject = f"[WARNING] Release inactive: {plan.project_key} {plan.version}"
    all_sent = True
    for recipient in recipients:
        if not await _send_email(recipient=recipient, subject=subject, body=body):
            all_sent = False
    if all_sent and recipients:
        await _record_notice_sent(plan.release_key, NOTICE_KIND_WARNING, plan.activity_at)
    elif recipients:
        log.warning(
            f"Inactivity warning for {plan.release_key!r} had at least one recipient failure;"
            f" notice not recorded so the next maintenance run retries the full set"
        )


def _warning_body(plan: Plan) -> str:
    warn_days, delete_days = thresholds()
    if plan.phase == sql.ReleasePhase.RELEASE_PREVIEW:
        return (
            f"This release preview has been inactive for at least {warn_days} days.\n\n"
            f"Project: {plan.project_key}\n"
            f"Version: {plan.version}\n"
            f"Phase: {plan.phase.value}\n\n"
            f"Under the inactive-release policy (issue #871), previews inactive for\n"
            f"{delete_days} days are escalated to the PMC. Release previews are not\n"
            f"automatically deleted. This warning fires at {warn_days} days. Please\n"
            f"complete the finish step or cancel the release."
        )
    return (
        f"This release candidate has been inactive for at least {warn_days} days.\n\n"
        f"Project: {plan.project_key}\n"
        f"Version: {plan.version}\n"
        f"Phase: {plan.phase.value}\n\n"
        f"Under the inactive-release policy (issue #871), candidates inactive for\n"
        f"{delete_days} days are cleaned up automatically. This warning fires at\n"
        f"{warn_days} days. Please continue the release or cancel it."
    )
