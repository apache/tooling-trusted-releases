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

"""keys.py"""

from typing import Annotated, Literal

import markupsafe
import pydantic

import atr.form as form
import atr.htm as htm
import atr.shared as shared
import atr.storage as storage
import atr.storage.types as types
import atr.template as template
import atr.util as util

type DELETE_OPENPGP_KEY = Literal["delete_openpgp_key"]
type DELETE_SSH_KEY = Literal["delete_ssh_key"]
type UPLOAD_REMOTE_KEYS = Literal["upload_remote_keys"]
type UPDATE_COMMITTEE_KEYS = Literal["update_committee_keys"]
type UPLOAD_FILE_KEYS = Literal["upload_file_keys"]


class AddOpenPGPKeyForm(form.Form):
    public_key: str = form.label(
        "Public OpenPGP key",
        'Your public key should be in ASCII-armored format, starting with "-----BEGIN PGP PUBLIC KEY BLOCK-----".'
        " Paste the key here, or upload it as a file below.",
        default="",
        widget=form.Widget.TEXTAREA,
    )
    public_key_file: form.File = form.label(
        "Public OpenPGP key file",
        "Alternatively, upload an ASCII-armored OpenPGP public key file.",
        default=None,
    )
    selected_committees: form.StrList = form.label(
        "Associate key with committees",
        "Select the committees for which you sign releases. Associating your key with a committee"
        " adds it to that committee's public KEYS file, which downstream users rely on to verify"
        " the authenticity of release artifacts. Only associate with committees where you are a release manager.",
    )

    @pydantic.model_validator(mode="after")
    def validate_at_least_one_committee(self) -> "AddOpenPGPKeyForm":
        if not self.selected_committees:
            raise ValueError("You must select at least one committee to associate with this key")
        return self

    @pydantic.model_validator(mode="after")
    def validate_exactly_one_key_source(self) -> "AddOpenPGPKeyForm":
        has_text = bool(self.public_key.strip())
        has_file = self.public_key_file is not None
        if has_text and has_file:
            raise ValueError("Provide either pasted key text or an uploaded file, not both")
        if (not has_text) and (not has_file):
            raise ValueError("Provide either pasted key text or an uploaded file")
        return self


class AddSSHKeyForm(form.Form):
    key: str = form.label(
        "SSH public key",
        "Your SSH public key should be in the standard format, starting with a key type"
        ' (like "ssh-rsa" or "ssh-ed25519") followed by the key data.',
        widget=form.Widget.TEXTAREA,
    )


class DeleteOpenPGPKeyForm(form.Form):
    variant: DELETE_OPENPGP_KEY = form.value(DELETE_OPENPGP_KEY)
    fingerprint: str = form.label("Fingerprint", widget=form.Widget.HIDDEN)


class DeleteSSHKeyForm(form.Form):
    variant: DELETE_SSH_KEY = form.value(DELETE_SSH_KEY)
    fingerprint: str = form.label("Fingerprint", widget=form.Widget.HIDDEN)


class UpdateCommitteeKeysForm(form.Empty):
    variant: UPDATE_COMMITTEE_KEYS = form.value(UPDATE_COMMITTEE_KEYS)
    committee_key: str = form.label("Committee name", widget=form.Widget.HIDDEN)


type KeysForm = Annotated[
    DeleteOpenPGPKeyForm | DeleteSSHKeyForm | UpdateCommitteeKeysForm,
    form.DISCRIMINATOR,
]


class UpdateKeyCommitteesForm(form.Form):
    selected_committees: form.StrList = form.label(
        "Associated PMCs",
        widget=form.Widget.CUSTOM,
    )


class UploadFileForm(form.Form):
    variant: UPLOAD_FILE_KEYS = form.value(UPLOAD_FILE_KEYS)
    key: form.File = form.label(
        "KEYS file",
        "Upload a KEYS file containing multiple PGP public keys."
        " The file should contain keys in ASCII-armored format, starting with"
        ' "-----BEGIN PGP PUBLIC KEY BLOCK-----".',
    )
    selected_committee: str = form.label(
        "Associate keys with committee",
        "Select the committee with which to associate these keys.",
        widget=form.Widget.RADIO,
    )

    @pydantic.model_validator(mode="after")
    def validate_key_required(self) -> "UploadFileForm":
        if not self.key:
            raise ValueError("A KEYS file is required")
        return self


class UploadRemoteForm(form.Form):
    variant: UPLOAD_REMOTE_KEYS = form.value(UPLOAD_REMOTE_KEYS)
    committee: str = form.label(
        "Committee",
        "Select the committee whose KEYS file to fetch from ASF downloads.",
        widget=form.Widget.RADIO,
    )


type UploadKeysForm = Annotated[
    UploadFileForm | UploadRemoteForm,
    form.DISCRIMINATOR,
]


async def render_upload_page(
    results: storage.outcome.List | None = None,
    submitted_committees: list[str] | None = None,
    error: bool = False,
) -> str:
    """Render the upload page with optional results."""
    import atr.get as get
    import atr.post as post

    async with storage.write() as write:
        participant_of_committees = await write.participant_of_committees()

    eligible_committees = [
        c for c in participant_of_committees if (not util.committee_is_standing(c.key)) or (c.key == "tooling")
    ]

    committee_choices = [(c.key, c.display_name) for c in eligible_committees]
    committee_map = {c.key: c.display_name for c in eligible_committees}

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(get.keys.keys))["← Back to Manage keys"]]
    page.h1["Import KEYS"]
    page.p["Import OpenPGP public signing keys from a KEYS file."]

    if results and submitted_committees:
        page.append(_get_results_table_css())
        _render_results_table(page, results, submitted_committees, committee_map)

    page.h2["Upload a file"]
    page.p["Upload a KEYS file from your computer."]

    await form.render_block(
        page,
        model_cls=shared.keys.UploadFileForm,
        action=util.as_url(post.keys.upload),
        submit_label="Upload KEYS file",
        defaults={"selected_committee": committee_choices},
        border=True,
        wider_widgets=True,
    )

    page.h2(".mt-5")["Fetch existing KEYS file"]
    page.p["Fetch the KEYS file from the ASF downloads server for the selected committee."]

    await form.render_block(
        page,
        model_cls=shared.keys.UploadRemoteForm,
        action=util.as_url(post.keys.upload),
        submit_label="Fetch KEYS file",
        defaults={"committee": committee_choices},
        border=True,
        wider_widgets=True,
    )

    return await template.blank(
        "Import KEYS",
        content=page.collect(),
        description="Import OpenPGP public signing keys from a KEYS file.",
    )


def _get_results_table_css() -> htm.Element:
    return htm.style[
        markupsafe.Markup(
            """
        .page-rotated-header {
            height: 180px;
            position: relative;
            vertical-align: bottom;
            padding-bottom: 5px;
            width: 40px;
        }
        .page-rotated-header > div {
            transform-origin: bottom left;
            transform: translateX(25px) rotate(-90deg);
            position: absolute;
            bottom: 12px;
            left: 6px;
            white-space: nowrap;
            text-align: left;
        }
        .table th, .table td {
            text-align: center;
            vertical-align: middle;
        }
        .table td.page-key-details {
            text-align: left;
            font-family: ui-monospace, "SFMono-Regular", "Menlo", "Monaco", "Consolas", monospace;
            font-size: 0.9em;
            word-break: break-all;
        }
        .page-status-cell-new {
            background-color: #197a4e !important;
        }
        .page-status-cell-existing {
            background-color: #868686 !important;
        }
        .page-status-cell-unknown {
            background-color: #ffecb5 !important;
        }
        .page-status-cell-error {
            background-color: #dc3545 !important;
        }
        .page-status-square {
            display: inline-block;
            width: 36px;
            height: 36px;
            vertical-align: middle;
        }
        .page-table-bordered th, .page-table-bordered td {
            border: 1px solid #dee2e6;
        }
        tbody tr {
            height: 40px;
        }
        """
        )
    ]


def _render_results_table(
    page: htm.Block, results: storage.outcome.List, submitted_committees: list[str], committee_map: dict[str, str]
) -> None:
    """Render the KEYS processing results table."""
    page.h2["KEYS processing results"]
    page.p[
        "The following keys were found in your KEYS file and processed against the selected committees. "
        "Green squares indicate that a key was added, grey squares indicate that a key already existed, "
        "and red squares indicate an error."
    ]

    thead = htm.Block(htm.thead)
    header_row = htm.Block(htm.tr)
    header_row.th(scope="col")["Key ID"]
    header_row.th(scope="col")["User ID"]
    for committee_key in submitted_committees:
        header_row.th(".page-rotated-header", scope="col")[htm.div[committee_map.get(committee_key, committee_key)]]
    thead.append(header_row.collect())

    tbody = htm.Block(htm.tbody)
    for outcome in results.outcomes():
        if outcome.ok:
            key_obj = outcome.result_or_none()
            fingerprint = key_obj.key_model.fingerprint if key_obj else "UNKNOWN"
            email_addr = key_obj.key_model.primary_declared_uid if key_obj else ""
            # Check whether the LINKED flag is set
            added_flag = bool(key_obj.status & types.KeyStatus.LINKED) if key_obj else False
            error_flag = False
        else:
            err = outcome.error_or_none()
            key_obj = getattr(err, "key", None) if err else None
            fingerprint = key_obj.key_model.fingerprint if key_obj else "UNKNOWN"
            email_addr = key_obj.key_model.primary_declared_uid if key_obj else ""
            added_flag = False
            error_flag = True

        row = htm.Block(htm.tr)
        row.td(".page-key-details.px-2")[htm.code[fingerprint[-16:].upper()]]
        row.td(".page-key-details.px-2")[email_addr or ""]

        for committee_key in submitted_committees:
            if error_flag:
                cell_class = "page-status-cell-error"
                title_text = "Error processing key"
            elif added_flag:
                cell_class = "page-status-cell-new"
                title_text = "Newly linked"
            else:
                cell_class = "page-status-cell-existing"
                title_text = "Already linked"

            row.td(".text-center.align-middle.page-status-cell-container")[
                htm.span(f".page-status-square.{cell_class}", title=title_text)
            ]

        tbody.append(row.collect())

    table_div = htm.div(".table-responsive")[
        htm.table(".table.table-striped.page-table-bordered.table-sm.mt-3")[thead.collect(), tbody.collect()]
    ]
    page.append(table_div)

    processing_errors = [o for o in results.outcomes() if not o.ok]
    if processing_errors:
        page.h3(".text-danger.mt-4")["Processing errors"]
        for outcome in processing_errors:
            err = outcome.error_or_none()
            page.div(".alert.alert-danger.p-2.mb-3")[str(err)]
