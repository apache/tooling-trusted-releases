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

import datetime
from typing import Annotated, Final, Literal

import markupsafe
import pydantic

import atr.form as form
import atr.htm as htm
import atr.models.sql as sql
import atr.shared as shared
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.template as template
import atr.util as util

# The Apache Subversion KEYS file is largest at 3732091 bytes
MAX_KEYS_SIZE: Final[int] = 10 * 1024 * 1024
MAX_PUBLIC_KEY_SIZE: Final[int] = 1024 * 1024

type DELETE_OPENPGP_KEY = Literal["delete_openpgp_key"]
type DELETE_SSH_KEY = Literal["delete_ssh_key"]
type SET_KEYS_MODE = Literal["set_keys_mode"]
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
        "Associating your key with a committee adds it to that committee's public KEYS file on the"
        " ASF distribution site, which ATR and downstream users rely on to verify the signatures on its"
        " releases. Associate a key with a committee when you sign releases for it.",
        required=True,
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
        required=True,
    )


class DeleteOpenPGPKeyForm(form.Form):
    variant: DELETE_OPENPGP_KEY = form.value(DELETE_OPENPGP_KEY)
    fingerprint: str = form.label("Fingerprint", widget=form.Widget.HIDDEN)


class DeleteSSHKeyForm(form.Form):
    variant: DELETE_SSH_KEY = form.value(DELETE_SSH_KEY)
    fingerprint: str = form.label("Fingerprint", widget=form.Widget.HIDDEN)


class SetKeysModeForm(form.Empty):
    variant: SET_KEYS_MODE = form.value(SET_KEYS_MODE)
    committee_key: str = form.label("Committee name", widget=form.Widget.HIDDEN)
    mode: Literal["automatic", "manual", "reflect"] = form.label("Mode", widget=form.Widget.HIDDEN)


# KEYS management modes for the committee page. The order here is the order the radios render in.
KEYS_MODE_LABELS: Final[dict[sql.KeysMode, str]] = {
    sql.KeysMode.AUTOMATIC: "Automatically update the committee's KEYS file",
    sql.KeysMode.REFLECT: "Automatically import changes to the KEYS file made in SVN",
    sql.KeysMode.MANUAL: "Manually upload KEYS files in ATR",
}


class UpdateCommitteeKeysForm(form.Empty):
    variant: UPDATE_COMMITTEE_KEYS = form.value(UPDATE_COMMITTEE_KEYS)
    committee_key: str = form.label("Committee name", widget=form.Widget.HIDDEN)


type KeysForm = Annotated[
    DeleteOpenPGPKeyForm | DeleteSSHKeyForm | SetKeysModeForm | UpdateCommitteeKeysForm,
    form.DISCRIMINATOR,
]


class UpdateKeyCommitteesForm(form.Form):
    selected_committees: form.StrList = form.label(
        "Associated committees",
        widget=form.Widget.CUSTOM,
    )


class UploadFileForm(form.Form):
    variant: UPLOAD_FILE_KEYS = form.value(UPLOAD_FILE_KEYS)
    key: form.File = form.label(
        "KEYS file",
        "Upload a KEYS file containing multiple PGP public keys."
        " The file should contain keys in ASCII-armored format, starting with"
        ' "-----BEGIN PGP PUBLIC KEY BLOCK-----".',
        required=True,
    )
    selected_committee: str = form.label(
        "Associate keys with committee",
        "Choose the committee whose KEYS file these keys belong to. Every key in the uploaded file is"
        " added to that committee's public KEYS file, which is used to verify the signatures on its releases.",
        widget=form.Widget.RADIO,
        required=True,
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
        "Choose the committee whose existing KEYS file to fetch from the ASF downloads server and import.",
        widget=form.Widget.RADIO,
        required=True,
    )


type UploadKeysForm = Annotated[
    UploadFileForm | UploadRemoteForm,
    form.DISCRIMINATOR,
]


def certificate_all_revoked(certificate: sql.SigningCertificate) -> bool:
    # Every key on the certificate is revoked, so it can no longer sign anything
    signing_keys = certificate.signing_keys
    return bool(signing_keys) and all(signing_key.revoked for signing_key in signing_keys)


def publication_added_notice(publications: dict[str, storage.outcome.Outcome[datatypes.KeysPublish]]) -> str | None:
    committees = publication_disabled(publications)
    if not committees:
        return None
    return (
        f"KEYS publication to SVN was skipped for {util.conjunction(committees)}"
        " because automated publication is disabled."
    )


def publication_disabled(publications: dict[str, storage.outcome.Outcome[datatypes.KeysPublish]]) -> list[str]:
    return [
        committee
        for committee, publication in sorted(publications.items())
        if publication.result_or_none() is datatypes.KeysPublish.AUTOMATION_DISABLED
    ]


def publication_failed_warning(publications: dict[str, storage.outcome.Outcome[datatypes.KeysPublish]]) -> str | None:
    failures = [
        f"{committee} ({error})"
        for committee, publication in sorted(publications.items())
        if (error := publication.error_or_none()) is not None
    ]
    if not failures:
        return None
    return f"KEYS publication to SVN failed for {util.conjunction(failures)}."


def publication_removed_warning(publications: dict[str, storage.outcome.Outcome[datatypes.KeysPublish]]) -> str | None:
    committees = publication_disabled(publications)
    if not committees:
        return None
    return (
        f"KEYS publication to SVN was skipped for {util.conjunction(committees)}"
        " because automated publication is disabled. Remove the key manually if necessary."
    )


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


def signing_key_expiry(signing_key: sql.SigningKey) -> str | htm.Element:
    if signing_key.expires is None:
        return "Never"
    expires_str = signing_key.expires.strftime("%Y-%m-%d")
    days_until_expiry = (signing_key.expires - datetime.datetime.now(datetime.UTC)).days
    if days_until_expiry < 0:
        return htm.span(".text-danger.fw-bold")[expires_str]
    if days_until_expiry <= 30:
        return htm.span(".text-warning.fw-bold")[
            expires_str,
            " ",
            htm.span(".badge.bg-warning.text-dark.ms-2")[f"in {util.plural(days_until_expiry, 'day')}"],
        ]
    return expires_str


def signing_key_state(signing_key: sql.SigningKey) -> Literal["revoked", "expired", "cannot_sign", "good"]:
    # Revocation outranks expiry, which outranks a key that simply can't sign
    if signing_key.revoked:
        return "revoked"
    if signing_key.expires is not None and (signing_key.expires <= datetime.datetime.now(datetime.UTC)):
        return "expired"
    if not signing_key.can_sign:
        return "cannot_sign"
    return "good"


def signing_key_status(signing_key: sql.SigningKey) -> str | htm.Element:
    state = signing_key_state(signing_key)
    if state == "revoked":
        return htm.span(".badge.bg-danger.text-white")["Revoked"]
    if state == "expired":
        return htm.span(".badge.bg-danger.text-white")["Expired"]
    if state == "cannot_sign":
        return htm.span(".badge.bg-secondary.text-white")["Cannot sign"]
    return htm.span(".badge.bg-success.text-white")["Good"]


def signing_keys_list(signing_keys: list[sql.SigningKey]) -> htm.Element | None:
    # A collapsed breakdown of the certificate's primary and subkeys, so a list can flag a key
    # which has expired or been revoked without a per-key status column of its own. Any link
    # through to the details belongs on the certificate key id above, not on each row
    if not signing_keys:
        return None
    ordered = sorted(signing_keys, key=lambda k: (not k.is_primary, k.created))
    usable = sum(1 for k in ordered if signing_key_state(k) == "good")

    summary = htm.Block(htm.summary, classes=".small")
    summary.text(util.plural(len(ordered), "signing key"))
    # Only worth calling out when some key can't sign - all usable needs no badge
    if usable < len(ordered):
        summary.span(".badge.bg-warning.text-dark.ms-2")[f"{usable} usable"]

    lines = htm.Block(htm.div, classes=".mt-1")
    for signing_key in ordered:
        lines.append(
            htm.div(".small.d-flex.flex-wrap.gap-2.align-items-center.mb-1")[
                htm.span(".fw-semibold")["Primary" if signing_key.is_primary else "Subkey"],
                htm.span(".text-muted.font-monospace")[signing_key.key_id.upper()],
                htm.span(".text-muted")[f"created {signing_key.created.strftime('%Y-%m-%d')}"],
                htm.span(".text-muted")["expires ", signing_key_expiry(signing_key)],
                signing_key_status(signing_key),
            ]
        )
    return htm.details(".mt-1")[summary.collect(), lines.collect()]


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
            added_flag = bool(key_obj.status & datatypes.KeyStatus.LINKED) if key_obj else False
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
