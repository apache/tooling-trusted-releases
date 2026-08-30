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

import atr.form as form
import atr.get.projects as get_projects
import atr.models.sql as sql
import atr.shared.projects as projects


def test_recipient_grid_readonly_shows_cc_bcc_counts_only() -> None:
    project = sql.Project(key="p", status=sql.ProjectStatus.ACTIVE)
    project.committee = sql.Committee(key="p", is_podling=False)
    project.release_policy = sql.ReleasePolicy(
        recipient_defaults={
            "vote": {"to": "dev@p.apache.org", "cc": ["carbon@example.org"], "bcc": ["blind@example.org"]},
        },
    )
    html = str(get_projects._recipient_grid_widget(project, sql.RecipientAction.VOTE, readonly=True))
    assert "dev@p.apache.org" in html
    assert "carbon@example.org" not in html
    assert "blind@example.org" not in html
    assert "1 CC, 1 BCC" in html


async def test_render_readonly() -> None:
    element = await form.render(
        model_cls=projects.ComposePolicyForm,
        action="/projects/test?tab=compose",
        defaults={
            "project_key": "test",
            "license_check_mode": "both",
            "source_excludes_lightweight": "vendored/**",
            "source_excludes_rat": "",
            "file_tag_mappings": "",
        },
        readonly=True,
    )
    html = str(element)
    assert "<form" not in html
    assert "csrf_token" not in html
    assert "<fieldset" in html
    assert "disabled" in html
    assert 'type="submit"' not in html
    assert "vendored/**" in html
