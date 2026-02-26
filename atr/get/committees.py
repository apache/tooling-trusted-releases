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

import atr.blueprints.get as get
import atr.db as db
import atr.form as form
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
        committees = await data.committee(_projects=True).order_by(sql.Committee.name).all()
        return await template.render(
            "committee-directory.html",
            committees=committees,
            committee_is_standing=util.committee_is_standing,
            plural_fn=util.plural,
        )


@get.typed
async def view(_session: web.Public, _committees: Literal["committees"], name: str) -> str:
    """
    URL: /committees/<name>
    """
    # TODO: Could also import this from keys.py
    async with db.session() as data:
        committee = await data.committee(
            name=name,
            _projects=True,
            _public_signing_keys=True,
        ).demand(base.ASFQuartException(f"Committee {name} not found", errorcode=404))
    project_list = list(committee.projects)
    for project in project_list:
        # Workaround for the usual loading problem
        project.committee = committee
    return await template.render(
        "committee-view.html",
        committee=committee,
        projects=project_list,
        algorithms=shared.algorithms,
        now=datetime.datetime.now(datetime.UTC),
        email_from_key=util.email_from_uid,
        update_committee_keys_form=form.render(
            model_cls=shared.keys.UpdateCommitteeKeysForm,
            action=util.as_url(post.keys.keys),
            submit_label="Regenerate KEYS file",
            defaults={"committee_name": name},
            empty=True,
        ),
        is_standing=util.committee_is_standing(name),
    )
