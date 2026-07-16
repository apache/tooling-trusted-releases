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

import datetime
from typing import Literal

import asfquart.base as base
import sqlalchemy
import sqlmodel

import atr.blueprints.get as get
import atr.db as db
import atr.db.interaction as interaction
import atr.form as form
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def directory(_session: web.Public, _committees: Literal["committees"]) -> str:
    """
    URL: /committees
    Main committee directory page.
    """
    async with db.session() as data:
        committees = await data.committee(_projects=True).order_by(sql.Committee.key).all()
        latest_releases = await interaction.project_latest_releases(data)
        projects_by_committee = {
            committee.key: sorted(
                committee.projects,
                key=lambda p: interaction.project_order_key(p, latest_releases.get(str(p.key))),
            )
            for committee in committees
        }
        return await template.render(
            "committee-directory.html",
            committees=committees,
            projects_by_committee=projects_by_committee,
            committee_is_standing=util.committee_is_standing,
            plural_fn=util.plural,
        )


@get.typed
async def view(session: web.Public, _committees: Literal["committees"], name: safe.CommitteeKey) -> str:
    """
    URL: /committees/<name>
    """
    async with db.session() as data:
        committee = await data.committee(
            key=str(name),
            _projects=True,
            _public_signing_keys=True,
        ).demand(base.ASFQuartException(f"Committee {name!s} not found", errorcode=404))

        roster = sorted(set(committee.committee_members + committee.committers))
        user_rows = await data.execute(
            sqlmodel.select(sql.User).where(sql.validate_instrumented_attribute(sql.User.asfuid).in_(roster))
        )
        names: dict[str, str | None] = {u.asfuid: u.name for u in user_rows.scalars().all()}

        fingerprints = [k.fingerprint for k in committee.public_signing_keys]
        artifact_counts: dict[str, int] = {}
        if fingerprints:
            via = sql.validate_instrumented_attribute
            count_rows = await data.execute(
                sqlalchemy.select(
                    via(sql.Artifact.key_fingerprint),
                    sqlalchemy.func.count(),
                )
                .where(via(sql.Artifact.key_fingerprint).in_(fingerprints))
                .group_by(via(sql.Artifact.key_fingerprint))
            )
            artifact_counts = {fp: n for fp, n in count_rows.all() if fp is not None}

        signing_committees = await interaction.automated_release_signing_committees(data)

        latest_releases = await interaction.project_latest_releases(
            data, project_keys=[str(p.key) for p in committee.projects]
        )

    project_list = list(committee.projects)
    for project in project_list:
        project.committee = committee
    project_list.sort(key=lambda p: interaction.project_order_key(p, latest_releases.get(str(p.key))))
    signing_keys = sorted(
        committee.public_signing_keys,
        key=lambda k: (k.apache_uid or "", k.fingerprint[-16:]),
    )
    committee_member = False
    if isinstance(session, web.Committer):
        committee_member = await session.prevent_confusing_ui_display_committee(name, False)

    is_standing = util.committee_is_standing(committee.key)
    release_managers = sorted(committee.release_managers)
    roster_action_forms: dict[str, htm.Element] = {}
    if committee_member and (not is_standing):
        action = util.as_url(post.committees.view, name=committee.key)
        for uid in roster:
            if uid in committee.committee_members:
                # PMC members are release managers implicitly, so there is no action
                continue
            if uid in committee.release_managers:
                model_cls: type[form.Form] = shared.committees.RemoveReleaseManagerForm
                submit_label = "Remove"
                submit_classes = "btn-outline-danger btn-sm"
            elif uid in committee.committers:
                model_cls = shared.committees.AddReleaseManagerForm
                submit_label = "Designate"
                submit_classes = "btn-outline-primary btn-sm"
            else:
                continue
            roster_action_forms[uid] = await form.render(
                model_cls=model_cls,
                action=action,
                form_classes=".d-inline-block.m-0",
                submit_classes=submit_classes,
                submit_label=submit_label,
                empty=True,
                defaults={"committee_key": committee.key, "asf_uid": uid},
            )

    keys_automated = committee.automated_keys_file
    automated_keys_file_form = await form.render(
        model_cls=shared.keys.SetAutomatedKeysFileForm,
        action=util.as_url(post.keys.keys),
        submit_label="Disable automated publication" if keys_automated else "Enable automated publication",
        defaults={"committee_key": committee.key, "enabled": "false" if keys_automated else "true"},
        empty=True,
    )

    return await template.render(
        "committee-view.html",
        automated_keys_file_form=automated_keys_file_form,
        committee=committee,
        projects=project_list,
        roster=roster,
        names=names,
        signing_keys=signing_keys,
        artifact_counts=artifact_counts,
        ci_builds_enabled=committee.key in signing_committees,
        algorithms=shared.algorithms,
        now=datetime.datetime.now(datetime.UTC),
        email_from_key=util.email_from_uid,
        is_committee_member=committee_member,
        release_managers=release_managers,
        roster_action_forms=roster_action_forms,
        update_committee_keys_form=await form.render(
            model_cls=shared.keys.UpdateCommitteeKeysForm,
            action=util.as_url(post.keys.keys),
            submit_label="Regenerate KEYS file",
            defaults={"committee_key": committee.key},
            empty=True,
        ),
        is_standing=is_standing,
    )
