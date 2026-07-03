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

from collections.abc import Sequence
from typing import Literal

import htpy

import atr.blueprints.get as get
import atr.db as db
import atr.form as form
import atr.get.compose as compose
import atr.get.keys as keys
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.render as render
import atr.shared as shared
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def selected(
    session: web.Committer,
    _upload: Literal["upload"],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
) -> str:
    """
    URL: /upload/<project_key>/<version_key>
    """
    await session.prevent_confusing_ui_display(project_key)
    async with db.session() as data:
        release = await session.release(project_key, version_key, data=data)
        user_ssh_keys = await data.ssh_key(asf_uid=session.uid).all()

    block = htm.Block()

    render.html_nav(
        block,
        util.as_url(compose.selected, project_key=release.project.key, version_key=release.version),
        f"Compose {release.short_display_name}",
        "COMPOSE",
    )

    block.h1[
        "Upload to ",
        htm.strong[release.project.short_display_name],
        " ",
        htm.em[release.version],
    ]

    block.p[
        htm.a(".btn.btn-outline-primary.me-2", href="#file-upload")["Use the browser"],
        htm.a(".btn.btn-outline-primary.me-2", href="#svn-upload")["Use SVN"],
        htm.a(".btn.btn-outline-primary.me-2", href="#rsync-upload")["Use rsync"],
        htm.a(".btn.btn-outline-primary", href="#github-upload")["Use GitHub Workflow"],
    ]

    block.h2(id="file-upload")["File upload"]
    block.p["Use this form to add files to this candidate draft."]

    await form.render_block(
        block,
        model_cls=shared.upload.AddFilesForm,
        submit_label="Add files",
        form_classes=".atr-canary.py-4.px-5",
    )

    block.append(htpy.div("#upload-progress-container.d-none"))

    block.h2(id="svn-upload")["SVN upload"]
    block.p[
        "Import files from this committee's ASF ",
        htm.a(href="https://dist.apache.org/repos/dist/")[htm.code["svn:dist"]],
        " repository into this draft.",
    ]
    block.p[
        "The import will be processed in the background using the ",
        htm.code["svn export"],
        " command. You can monitor progress on the ",
        htm.em["Evaluate files"],
        " page for this draft once the task is queued.",
    ]

    await form.render_block(
        block,
        model_cls=shared.upload.SvnImportForm,
        submit_label="Queue SVN import task",
        form_classes=".atr-canary.py-4.px-5",
    )

    block.h2(id="rsync-upload")["Rsync upload"]

    key_count = len(user_ssh_keys)
    if key_count == 0:
        block.div(".alert.alert-warning")[
            htm.p(".mb-0")[
                "We have no SSH keys on file for you, ",
                "so you cannot yet use this command. Please ",
                htm.a(href=util.as_url(keys.ssh_add))["add your SSH key"],
                ".",
            ]
        ]

    block.p["Import files from a remote server using rsync with the following command:"]

    server_domain = session.app_host.split(":", 1)[0]
    rsync_command = (
        f"rsync -av -e 'ssh -p 2222' ${{YOUR_FILES}}/ "
        f"{session.uid}@{server_domain}:/{release.project.key}/{release.version}/"
    )
    block.pre(".bg-light.p-3.mb-3")[rsync_command]

    _render_ssh_keys_info(block, user_ssh_keys)

    block.h2(id="github-upload")["GitHub Workflow"]
    block.p[
        "If your project is approved for reproducible builds, you can ",
        "upload files into this draft from a GitHub repository using GitHub Actions.",
    ]
    block.append(
        htm.ol[
            htm.li["Ensure GitHub Actions is enabled for your repository"],
            htm.li[
                "Create a new workflow file in the .github/workflows/ directory of your repository, ",
                "following the steps ",
                htm.a(href="https://docs.github.com/en/actions/tutorials/create-an-example-workflow")["here"],
            ],
            htm.li["Add the example code below to your workflow file, making sure to set the correct file paths"],
        ]
    )
    block.pre[
        f"""
name: Upload to ATR
on:
  workflow_dispatch:

jobs:
  upload:
    permissions:
      id-token: write
      contents: read
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683

      - name: Build artifacts
        run: |
          ./build.sh

      - name: Upload to ATR
        uses: apache/tooling-actions/upload-to-atr@f5f4c0e7ddfbde6b1f8288cef36324c6def68051
        with:
          project: {project_key!s}
          version: ${{{{github.ref_name}}}}
        """
    ]
    block.p[
        "(assuming your",
        htm.code[" github.ref_name "],
        "resolves to match your version - currently",
        htm.code[f" {version_key!s} "],
        "and",
        htm.code[" build.sh "],
        " produces the files you want to upload)",
    ]
    block.p["You can also use the", htm.code[" Upload to ATR "], "step directly in an existing workflow."]

    return await template.blank(
        f"Upload files to {release.short_display_name}",
        content=block.collect(),
        javascripts=["upload-progress-ui", "upload-progress"],
    )


def _render_ssh_keys_info(block: htm.Block, user_ssh_keys: Sequence[sql.SSHKey]) -> None:
    known_cves_url = "https://github.com/google/security-research/security/advisories/GHSA-p5pg-x43v-mvqj"
    block.p[
        "The ATR server should be compatible with long obsolete versions of rsync, ",
        "as long as you use the command as shown, but as of May 2025 the only rsync version line without ",
        htm.a(href=known_cves_url)["known CVEs"],
        " is 3.4.*. Your package manager may have backports.",
    ]
    new_issue_url = "https://github.com/apache/tooling-trusted-releases/issues/new?template=BLANK_ISSUE"
    block.p[
        "If you find that you receive errors from ATR when using rsync, please ",
        htm.a(href=new_issue_url)["open an issue"],
        " and we will try our best to make ATR compatible.",
    ]

    key_count = len(user_ssh_keys)
    if key_count == 1:
        key = user_ssh_keys[0]
        key_parts = key.key.split(" ", 2)
        key_comment = key_parts[2].strip() if (len(key_parts) > 2) else "key"
        block.p[
            "We have the SSH key ",
            htm.a(
                href=util.as_url(keys.keys, _anchor=f"ssh-key-{key.fingerprint}"),
                title=key.fingerprint,
            )[htm.code[key_comment]],
            " on file for you. You can also ",
            htm.a(href=util.as_url(keys.ssh_add))["add another SSH key"],
            ".",
        ]
    elif key_count > 1:
        block.p["We have the following SSH keys on file for you:"]
        key_items = []
        for key in user_ssh_keys:
            key_parts = key.key.split(" ", 2)
            key_comment = key_parts[2].strip() if (len(key_parts) > 2) else "key"
            key_items.append(
                htm.li[
                    htm.a(
                        href=util.as_url(keys.keys, _anchor=f"ssh-key-{key.fingerprint}"),
                        title=key.fingerprint,
                    )[htm.code[key_comment]]
                ]
            )
        block.append(htm.ul[*key_items])
        block.p[
            "You can also ",
            htm.a(href=util.as_url(keys.ssh_add))["add another SSH key"],
            ".",
        ]
