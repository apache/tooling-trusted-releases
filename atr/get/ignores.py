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

from typing import Literal

import atr.blueprints.get as get
import atr.form as form
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.post as post
import atr.shared as shared
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def ignores(
    session: web.Committer,
    _ignores: Literal["ignores"],
    project_name: safe.ProjectName,
) -> str | web.WerkzeugResponse:
    """
    URL: /ignores/<project_name>
    """
    await session.check_access(project_name)
    async with storage.read() as read:
        ragp = read.as_general_public()
        ignores = await ragp.checks.ignores(project_name)

    content = htm.div[
        htm.h1["Ignored checks"],
        htm.p[f"Manage ignored checks for project {project_name!s}."],
        _add_ignore(str(project_name)),
        _existing_ignores(ignores),
    ]

    return await template.blank("Ignored checks", content, javascripts=["ignore-form-change"])


def _add_ignore(project_name: str) -> htm.Element:
    form_path = util.as_url(post.ignores.ignores, project_name=project_name)
    block = htm.Block(htm.div)
    block.h2["Add ignore"]
    block.p["Add a new ignore for a check result."]
    form.render_block(
        block,
        model_cls=shared.ignores.AddIgnoreForm,
        action=form_path,
        submit_label="Add ignore",
    )
    return block.collect()


def _check_result_ignore_card(cri: sql.CheckResultIgnore) -> htm.Element:
    h3_id = cri.id or ""
    h3_asf_uid = cri.asf_uid
    h3_created = util.format_datetime(cri.created)
    card_header_h3 = htm.h3(".mt-3.mb-0")[f"{h3_id} - {h3_asf_uid} - {h3_created}"]

    # Update form
    update_form_block = htm.Block(htm.div)
    form_path_update = util.as_url(post.ignores.ignores, project_name=cri.project_name)
    status = shared.ignores.sql_to_ignore_status(cri.status)
    form.render_block(
        update_form_block,
        model_cls=shared.ignores.UpdateIgnoreForm,
        action=form_path_update,
        submit_label="Update ignore",
        form_classes="",
        defaults={
            "id": cri.id or 0,
            "release_glob": cri.release_glob or "",
            "revision_number": cri.revision_number or "",
            "checker_glob": cri.checker_glob or "",
            "primary_rel_path_glob": cri.primary_rel_path_glob or "",
            "member_rel_path_glob": cri.member_rel_path_glob or "",
            "status": status,
            "message_glob": cri.message_glob or "",
        },
    )

    # Delete form
    delete_form_block = htm.Block(htm.div)
    form.render_block(
        delete_form_block,
        model_cls=shared.ignores.DeleteIgnoreForm,
        action=form_path_update,
        submit_label="Delete",
        submit_classes="btn-danger",
        form_classes=".mt-2.mb-0",
        defaults={"id": cri.id or 0},
        empty=True,
    )

    card = htm.div(".card.mb-5")[
        htm.div(".card-header.d-flex.justify-content-between")[card_header_h3, delete_form_block.collect()],
        htm.div(".card-body")[update_form_block.collect()],
    ]

    return card


def _existing_ignores(ignores: list[sql.CheckResultIgnore]) -> htm.Element:
    return htm.div[
        htm.h2["Existing ignores"],
        [_check_result_ignore_card(cri) for cri in ignores] or htm.p["No ignores found."],
    ]
