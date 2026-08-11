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

import htpy
import quart
import sqlalchemy
import sqlmodel

import atr.blueprints.get as get
import atr.db as db
import atr.form as form
import atr.htm as htm
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.unsafe as unsafe
import atr.post as post
import atr.shared as shared
import atr.storage as storage
import atr.template as template
import atr.util as util
import atr.web as web


@get.typed
async def add(_session: web.Committer, _keys_add: Literal["keys/add"]) -> str:
    """
    URL: /keys/add
    Add a new public signing key to the user's account.
    """
    async with storage.write() as write:
        participant_of_committees = await write.participant_of_committees()

    committee_choices = [(c.key, c.display_name or c.key) for c in participant_of_committees]

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(keys))["← Back to Manage keys"],]
    page.div(".my-4")[
        htm.h1(".mb-4")["Add your OpenPGP key"],
        htm.p["Add your public key to use for signing release artifacts."],
        htm.p(".text-muted")[
            "Associating your key with a committee below adds it to that committee's public KEYS file on the ",
            "ASF distribution site, which ATR and downstream users rely on to verify the signatures on its ",
            "releases. Associate a key with a committee when you sign releases for it.",
        ],
    ]
    page.div(".alert.alert-info")[
        htm.p(".fw-semibold.mb-2")["Which email address should your key use?"],
        htm.p[
            "Your key's user ID must contain an email address that ATR can link to an ASF account. ",
            "The simplest choice is your own ",
            htm.code["@apache.org"],
            " address. Otherwise, use an address that you have linked to your account at ",
            htm.a(href="https://id.apache.org/")["id.apache.org"],
            ". Always use your own address, not someone else's.",
        ],
        htm.p(".mb-0")[
            "If ATR cannot map the address to an ASF UID, this form will not accept the key. ",
            "Fix the key's user ID or link the address at ",
            htm.a(href="https://id.apache.org/")["id.apache.org"],
            " before uploading it. The address is publically associated with releases that you sign through your "
            "public key and committee KEYS files, so choose carefully.",
        ],
    ]
    await form.render_block(
        page,
        model_cls=shared.keys.AddOpenPGPKeyForm,
        action=util.as_url(post.keys.add),
        submit_label="Add OpenPGP key",
        cancel_url=util.as_url(keys),
        defaults={
            "selected_committees": committee_choices,
        },
    )

    return await template.blank(
        "Add your OpenPGP key",
        content=page.collect(),
        description="Add your public signing key to your ATR account.",
        javascripts=["keys-add-toggle"],
    )


@get.typed
async def details(session: web.Committer, _keys_details: Literal["keys/details"], fingerprint: unsafe.UnsafeStr) -> str:
    """
    URL: /keys/details/<fingerprint>
    Display details for a specific OpenPGP key.
    """
    key_fingerprint = str(fingerprint).lower()
    async with db.session() as data:
        key, is_owner = await _key_and_is_owner(data, session, key_fingerprint)
        user_committees = []
        if is_owner:
            project_list = session.member_committees + session.participant_committees
            user_committees = await data.committee(name_in=project_list).all()
        via = sql.validate_instrumented_attribute
        signing_keys = list(
            (
                await data.execute(
                    sqlmodel.select(sql.SigningKey)
                    .where(via(sql.SigningKey.certificate_fingerprint) == key.fingerprint)
                    .order_by(via(sql.SigningKey.is_primary).desc(), via(sql.SigningKey.created))
                )
            )
            .scalars()
            .all()
        )

    if isinstance(key.ascii_armored_key, bytes):
        key.ascii_armored_key = key.ascii_armored_key.decode("utf-8", errors="replace")

    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(keys))["← Back to Manage keys"]]
    page.h1["OpenPGP key details"]

    tbody = htm.Block(htm.tbody)

    def _add_row(th: str, td: str | htm.Element) -> None:
        tbody.append(htm.tr[htm.th(".p-2.text-dark")[th], htm.td(".text-break.align-middle")[td]])

    _add_row("Fingerprint", key.fingerprint.upper())

    primary_key = next((signing_key for signing_key in signing_keys if signing_key.is_primary), None)
    if primary_key is not None:
        _add_row("Type", f"{shared.algorithms[primary_key.algorithm]} ({primary_key.length} bits)")
        _add_row("Created", primary_key.created.strftime("%Y-%m-%d %H:%M:%S"))

    latest_sig = key.latest_self_signature.strftime("%Y-%m-%d %H:%M:%S") if key.latest_self_signature else "Never"
    _add_row("Latest self signature", latest_sig)

    _add_row("Primary UID", key.primary_declared_uid or "-")
    secondary_uids = ", ".join(key.secondary_declared_uids) if key.secondary_declared_uids else "-"

    _add_row("Secondary UIDs", secondary_uids)

    _add_row("Apache UID", key.apache_uid or "-")

    pmc_div = htm.Block(htm.div, classes=".text-break.pt-2")
    if is_owner:
        committee_choices = [(c.key, c.display_name or c.key) for c in user_committees]
        current_committee_keys = [c.key for c in key.committees]

        # form.render_block(
        #     pmc_div,
        #     model_cls=shared.keys.UpdateKeyCommitteesForm,
        #     action=util.as_url(post.keys.details, fingerprint=fingerprint),
        #     form_classes=".mb-4.d-inline-block",
        #     submit_label="Update associations",
        #     submit_classes="btn-primary btn-sm",
        #     defaults={"selected_committees": committee_choices},
        #     custom={"selected_committees": _render_committee_checkboxes(committee_choices, current_committee_keys)},
        # )
        pmc_div.p(".text-muted.small.mb-2")[
            "Associating your key with a committee adds it to that committee's public KEYS file on the "
            "ASF distribution site, used to verify the signatures on its releases. Associate a key with a "
            "committee when you sign releases for it.",
        ]
        checkboxes = _render_committee_checkboxes(committee_choices, current_committee_keys)
        pmc_div.form(
            method="post",
            action=util.as_url(post.keys.details, fingerprint=key_fingerprint),
        )[
            form.csrf_input(),
            checkboxes,
            htm.div(".mt-3")[htpy.button(".btn.btn-primary.btn-sm", type="submit")["Update associations"]],
        ]
    else:
        if key.committees:
            committee_keys = ", ".join([c.key for c in key.committees])
            pmc_div.text(committee_keys)
        else:
            pmc_div.text("Not associated with any committee")
    _add_row("Associated committees", pmc_div.collect())

    page.table(".mb-0.table.border.border-2.table-striped.table-sm")[tbody.collect()]

    _add_signing_keys(page, signing_keys)

    page.h2["ASCII armored key"]
    page.pre(".mt-3.border.border-2.p-3")[key.ascii_armored_key]

    return await template.blank(
        "OpenPGP key details",
        content=page.collect(),
        description="View details for a specific OpenPGP public signing key.",
    )


@get.typed
async def export(
    _session: web.Committer, _keys_export: Literal["keys/export"], committee_key: safe.CommitteeKey
) -> web.TextResponse:
    """
    URL: /keys/export/<committee_key>
    Export a KEYS file for a specific committee.
    """
    async with storage.write() as write:
        wafc = write.as_foundation_committer()
        keys_file_text = await wafc.keys.keys_file_text(str(committee_key))

    return web.TextResponse(keys_file_text)


@get.typed
async def keys(session: web.Committer, _keys: Literal["keys"]) -> str:
    """
    URL: /keys
    View all keys associated with the user's account.
    """
    committees_to_query = list(set(session.member_committees + session.participant_committees))

    async with db.session() as data:
        user_keys = await data.signing_certificate(
            apache_uid=session.uid.lower(), _committees=True, _signing_keys=True
        ).all()
        user_ssh_keys = await data.ssh_key(asf_uid=session.uid).all()
        user_committees_with_keys = await data.committee(
            name_in=committees_to_query, _signing_certificates=True, _signing_keys=True
        ).all()

        all_fingerprints = [k.fingerprint for c in user_committees_with_keys for k in c.signing_certificates]
        artifact_counts: dict[str, int] = {}
        if all_fingerprints:
            via = sql.validate_instrumented_attribute
            # An artifact is attributed to the key which signed it, so counting per certificate has
            # to go through SigningKey or everything signed by a subkey is left out
            count_rows = await data.execute(
                sqlalchemy.select(
                    via(sql.SigningKey.certificate_fingerprint),
                    sqlalchemy.func.count(),
                )
                .select_from(sql.Artifact)
                .join(sql.SigningKey, via(sql.SigningKey.fingerprint) == via(sql.Artifact.key_fingerprint))
                .where(via(sql.SigningKey.certificate_fingerprint).in_(all_fingerprints))
                .group_by(via(sql.SigningKey.certificate_fingerprint))
            )
            artifact_counts = {fp: n for fp, n in count_rows.all()}

    for key in user_keys:
        key.committees.sort(key=lambda c: c.key)

    page = htm.Block()
    page.h1["Manage keys"]
    page.p(".mb-4")[
        htm.a(".btn.btn-sm.btn-secondary.me-3", href="#your-public-keys")["Your public keys"],
        htm.a(".btn.btn-sm.btn-secondary", href="#your-committee-keys")["Your committee's keys"],
    ]

    page.h2("#your-public-keys")["Your public keys"]
    page.p["Review your public keys used for signing release artifacts."]
    page.div(".d-flex.gap-3.mb-4")[
        htm.a(".btn.btn-outline-primary", href=util.as_url(add))["Add your OpenPGP key"],
        htm.a(".btn.btn-outline-primary", href=util.as_url(ssh_add))["Add your SSH key"],
    ]

    await _openpgp_keys(page, list(user_keys))
    await _ssh_keys(page, list(user_ssh_keys))
    await _committee_keys(page, list(user_committees_with_keys), artifact_counts)

    return await template.blank(
        "Manage keys",
        content=page.collect(),
        description="Review your keys.",
    )


@get.typed
async def ssh_add(session: web.Committer, _keys_ssh_add: Literal["keys/ssh/add"]) -> str:
    """
    URL: /keys/ssh/add
    Add a new SSH key to the user's account.
    """
    page = htm.Block()
    page.p[htm.a(".atr-back-link", href=util.as_url(keys))["← Back to Manage keys"]]
    page.h1["Add your SSH key"]
    page.p["Add your SSH public key to use for rsync authentication."]
    page.div[
        htm.p[
            "Welcome, ",
            htm.strong[session.uid],
            "! You are authenticated as an ASF committer.",
        ]
    ]

    await form.render_block(
        page,
        model_cls=shared.keys.AddSSHKeyForm,
        action=util.as_url(post.keys.ssh_add),
        submit_label="Add SSH key",
    )

    return await template.blank(
        "Add your SSH key",
        content=page.collect(),
        description="Add your SSH public key to your account.",
    )


@get.typed
async def upload(_session: web.Committer, _keys_upload: Literal["keys/upload"]) -> str:
    """
    URL: /keys/upload
    Upload a KEYS file containing multiple OpenPGP keys.
    """
    return await shared.keys.render_upload_page()


def _add_signing_keys(page: htm.Block, signing_keys: list[sql.SigningKey]) -> None:
    if not signing_keys:
        return
    page.h2(".h5.mt-4")["Signing keys"]

    thead = htm.thead[
        htm.tr[
            htm.th(".px-2", scope="col")["Key ID"],
            htm.th(".px-2", scope="col")["Role"],
            htm.th(".px-2", scope="col")["Created"],
            htm.th(".px-2", scope="col")["Expires"],
            htm.th(".px-2", scope="col")["Status"],
        ]
    ]
    tbody = htm.Block(htm.tbody)
    for signing_key in signing_keys:
        tbody.append(
            htm.tr[
                htm.td(".px-2.text-break")[signing_key.key_id.upper()],
                htm.td(".px-2")["Primary" if signing_key.is_primary else "Subkey"],
                htm.td(".px-2")[signing_key.created.strftime("%Y-%m-%d")],
                htm.td(".px-2")[shared.keys.signing_key_expiry(signing_key)],
                htm.td(".px-2")[shared.keys.signing_key_status(signing_key)],
            ]
        )
    page.div(".table-responsive.mb-4")[htm.table(".table.border.table-striped.table-sm")[thead, tbody.collect()]]


def _certificate_key_id(key: sql.SigningCertificate) -> htm.Element:
    link = htm.a(href=util.as_url(details, fingerprint=key.fingerprint))[key.fingerprint[-16:].upper()]
    # A certificate whose primary and every subkey are revoked can't sign anything, so strike it through
    # and say so, since these tables carry no per-key status column
    if shared.keys.certificate_all_revoked(key):
        return htm.span[
            htm.span(".text-decoration-line-through")[link],
            " ",
            htm.span(".text-muted")["(revoked)"],
        ]
    return link


async def _committee_keys(
    page: htm.Block,
    user_committees_with_keys: list[sql.Committee],
    artifact_counts: dict[str, int],
) -> None:
    page.h2("#your-committee-keys")["Your committee's keys"]
    page.div(".mb-4")[htm.a(".btn.btn-outline-primary", href=util.as_url(upload))["Upload a KEYS file"]]

    for committee in user_committees_with_keys:
        if not util.committee_is_standing(committee.key):
            page.h3(f"#committee-{committee.key}.mt-3")[committee.display_name or committee.key]

            if committee.signing_certificates:
                sorted_keys = sorted(
                    committee.signing_certificates,
                    key=lambda k: (k.apache_uid or "", k.fingerprint[-16:]),
                )
                thead = htm.thead[
                    htm.tr[
                        htm.th(".px-2", scope="col")["Key ID"],
                        htm.th(".px-2", scope="col")["Email"],
                        htm.th(".px-2", scope="col")["Apache UID"],
                        htm.th(".px-2", scope="col")["Role"],
                        htm.th(".px-2.text-end", scope="col")["Artifacts"],
                        htm.th(".px-2.text-end", scope="col")["Any historic use"],
                    ]
                ]
                tbody = htm.Block(htm.tbody)
                for key in sorted_keys:
                    row = htm.Block(htm.tr)
                    row.td(".text-break.px-2")[*_key_id_cell_contents(key)]
                    email = util.email_from_uid(key.primary_declared_uid) if key.primary_declared_uid else "-"
                    row.td(".text-break.px-2")[email or "-"]
                    row.td(".text-break.px-2")[key.apache_uid or "-"]
                    uid = (key.apache_uid or "").lower()
                    if uid in committee.committee_members:
                        role = "PMC member"
                    elif uid in committee.committers:
                        role = "Committer"
                    else:
                        role = "-"
                    row.td(".text-break.px-2")[role]
                    row.td(".text-break.px-2.text-end")[str(artifact_counts.get(key.fingerprint, 0))]
                    row.td(".text-break.px-2.text-end")["Yes" if key.historic_use else "-"]
                    tbody.append(row.collect())

                page.div(".table-responsive.mb-2")[
                    htm.table(".table.border.table-striped.table-sm")[thead, tbody.collect()]
                ]
                page.p(".text-muted")[
                    "The ",
                    htm.code["KEYS"],
                    " file is automatically generated when you add or remove a key,"
                    " but you can also use the form below to manually regenerate it.",
                ]

                await form.render_block(
                    page,
                    model_cls=shared.keys.UpdateCommitteeKeysForm,
                    action=util.as_url(post.keys.keys),
                    form_classes=".mb-4.d-inline-block",
                    submit_label="Regenerate KEYS file",
                    submit_classes="btn btn-sm btn-outline-secondary",
                    defaults={"committee_key": committee.key},
                    empty=True,
                )
            else:
                page.p(".mb-4")["No keys uploaded for this committee yet."]


async def _key_and_is_owner(
    data: db.Session, session: web.Committer, fingerprint: str
) -> tuple[sql.SigningCertificate, bool]:
    key = await data.signing_certificate(fingerprint=fingerprint, _committees=True).get()
    if not key:
        quart.abort(404, description="OpenPGP key not found")
    key.committees.sort(key=lambda c: c.key)

    # Allow owners and committee members to view the key
    authorised = False
    is_owner = False
    if key.apache_uid and session.uid:
        is_owner = key.apache_uid.lower() == session.uid.lower()
    if is_owner:
        authorised = True
    else:
        user_affiliations = set(session.member_committees + session.participant_committees)
        key_committee_keys = {c.key for c in key.committees}
        if user_affiliations.intersection(key_committee_keys):
            authorised = True
        elif session.is_admin:
            authorised = True

    if not authorised:
        quart.abort(403, description="You are not authorised to view this key")

    return key, is_owner


def _key_id_cell_contents(certificate: sql.SigningCertificate) -> list[htm.Element]:
    # The certificate key id links through to the details, with the signing keys collapsed beneath it
    contents = [htm.div(".font-monospace")[_certificate_key_id(certificate)]]
    keys_list = shared.keys.signing_keys_list(certificate.signing_keys)
    if keys_list is not None:
        contents.append(keys_list)
    return contents


async def _openpgp_keys(page: htm.Block, user_keys: list[sql.SigningCertificate]) -> None:
    page.h3["Your OpenPGP keys"]
    if user_keys:
        thead = htm.thead[
            htm.tr[
                htm.th(".px-2", scope="col")["Key ID"],
                htm.th(".px-2", scope="col")["Committees"],
                htm.th(".px-2", scope="col")["Any historic use"],
                htm.th(".px-2", scope="col")["Action"],
            ]
        ]

        tbody = htm.Block(htm.tbody)
        for key in user_keys:
            row = htm.Block(htm.tr, classes=".page-user-openpgp-key")
            row.td(".text-break.px-2.align-middle")[*_key_id_cell_contents(key)]
            if key.committees:
                committee_keys = ", ".join([c.key for c in key.committees])
                row.td(".text-break.px-2.align-middle")[committee_keys]
            else:
                row.td(".text-break.px-2.align-middle")["Not associated with any committee"]
            row.td(".text-break.px-2.align-middle")["Yes" if key.historic_use else "-"]
            with row.block(htm.td, classes=".px-2") as td:
                await form.render_block(
                    td,
                    model_cls=shared.keys.DeleteOpenPGPKeyForm,
                    action=util.as_url(post.keys.keys),
                    form_classes=".m-0",
                    submit_label="Delete key",
                    submit_classes="btn btn-sm btn-danger",
                    defaults={"fingerprint": key.fingerprint},
                    empty=True,
                )
            tbody.append(row.collect())

        page.div(".table-responsive.mb-5")[htm.table(".table.border.table-striped.table-sm")[thead, tbody.collect()]]
    else:
        page.p[htm.strong["You haven't added any personal OpenPGP keys yet."]]


def _render_committee_checkboxes(
    committee_choices: list[tuple[str, str]], current_committees: list[str]
) -> htm.Element:
    """Render committee checkboxes in a grid layout."""
    row_div = htm.Block(htm.div, classes=".row")
    for val, label in committee_choices:
        checkbox_id = f"selected_committees_{val}"
        checkbox_attrs = {
            "type": "checkbox",
            "name": "selected_committees",
            "id": checkbox_id,
            "value": val,
            "class_": "form-check-input",
        }
        if val in current_committees:
            checkbox_attrs["checked"] = ""

        checkbox_input = htpy.input(**checkbox_attrs)
        checkbox_label = htpy.label(for_=checkbox_id, class_="form-check-label")[label]
        checkbox_div = htm.div(".form-check.mb-2")[checkbox_input, checkbox_label]
        col_div = htm.div(".col-sm-12.col-md-6.col-lg-4")[checkbox_div]
        row_div.append(col_div)

    return row_div.collect()


async def _ssh_keys(page: htm.Block, user_ssh_keys: list[sql.SSHKey]) -> None:
    page.h3["Your SSH keys"]
    if user_ssh_keys:
        page.div(".alert.alert-warning.mb-3")[
            htm.p(".fw-semibold.mb-1")["Note:"],
            htm.p(".mb-0")[
                "Deleting any SSH key will log you out of every ATR session for security. ",
                "You will need to sign in again.",
            ],
        ]
        grid = htm.Block(htm.div, classes=".d-grid.gap-4")
        for key in user_ssh_keys:
            card_block = htm.Block(htm.div, classes=f"#ssh-key-{key.fingerprint}.card.p-3.border")

            key_type = key.key.split()[0] if key.key else ""
            tbody = htm.tbody[
                htm.tr[
                    htm.th(".p-2.text-dark")["Fingerprint"],
                    htm.td(".text-break")[key.fingerprint],
                ],
                htm.tr[
                    htm.th(".p-2.text-dark")["Type"],
                    htm.td(".text-break")[key_type],
                ],
            ]
            card_block.table(".mb-0")[tbody]
            card_block.details(".mt-3.p-3.bg-light.rounded")[
                htm.summary(".fw-bold")["View whole key"],
                htm.pre(".mt-3")[key.key],
            ]

            await form.render_block(
                card_block,
                model_cls=shared.keys.DeleteSSHKeyForm,
                action=util.as_url(post.keys.keys),
                form_classes=".mt-3",
                submit_label="Delete key",
                submit_classes="btn btn-danger",
                confirm="Deleting this SSH key will log you out of every session. Continue?",
                defaults={"fingerprint": key.fingerprint},
                empty=True,
            )
            grid.append(card_block.collect())

        page.div(".mb-5.p-4.bg-light.rounded")[grid.collect()]
    else:
        page.p[htm.strong["You haven't added any SSH keys yet."]]
