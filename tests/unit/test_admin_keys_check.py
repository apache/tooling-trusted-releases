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

import atr.admin as admin
import atr.models.sql as sql
import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures

_BROKEN_BLOCK = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nnotbase64\n-----END PGP PUBLIC KEY BLOCK-----\n"


def _certificate(
    fingerprint: str,
    armored: str,
    latest_self_signature: datetime.datetime | None = None,
    deleted: datetime.datetime | None = None,
    primary_declared_uid: str | None = None,
) -> sql.SigningCertificate:
    return sql.SigningCertificate(
        fingerprint=fingerprint,
        latest_self_signature=latest_self_signature,
        primary_declared_uid=primary_declared_uid,
        secondary_declared_uids=[],
        apache_uid="alice",
        ascii_armored_key=armored,
        deleted=deleted,
    )


def test_certificate_block_report_lists_shapes_shared_text_and_metadata() -> None:
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )
    revoked, _ = pgp.openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC)
    revoked_rows = {facts.fingerprint.lower() for facts in pgp.signing_key_facts(revoked)}
    stale = datetime.datetime(2030, 1, 1, tzinfo=datetime.UTC)
    certificates = [
        _certificate(pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT, block),
        _certificate(pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT, block, deleted=stale),
        _certificate(
            pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT,
            pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC,
            stale,
            primary_declared_uid="Nobody <nobody@example.invalid>",
        ),
        _certificate("ab" * 20, _BROKEN_BLOCK),
        _certificate("cd" * 20, _BROKEN_BLOCK),
    ]
    signing_keys = {
        pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT: {pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT},
        pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT: revoked_rows,
    }

    header, *lines = admin._certificate_block_report(certificates, signing_keys).split("\n")

    assert header.startswith("Checked 5 certificate blocks: ")
    for expected in (
        "1 multi-own-first",
        "1 multi-own-not-first",
        "4 shared-text",
        "2 unparseable",
        "2 metadata",
        "2 signing-keys",
    ):
        assert expected in header
    kinds = [
        "metadata"
        if (" declared uid " in line or " latest self-signature " in line)
        else "signing-keys"
        if " SigningKey rows missing " in line
        else "shape"
        for line in lines
    ]
    assert kinds == sorted(kinds, key=["shape", "signing-keys", "metadata"].index)
    body = "\n".join(lines)
    assert f"{pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT} (deleted): multi-own-not-first" in body
    assert f"{'ab' * 20}: stored text is shared with another row" in body
    assert "declared uid 'Nobody <nobody@example.invalid>' is not in the certificate" in body
    assert "latest self-signature stored as 2030-01-01" in body
    assert "SigningKey rows missing for" in body


def test_certificate_metadata_problems_ignores_unset_metadata() -> None:
    key, _ = pgp.openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    certificate = _certificate(
        pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT, pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC
    )
    rows = {facts.fingerprint.lower() for facts in pgp.signing_key_facts(key)}

    assert admin._certificate_metadata_problems(certificate, key, rows) == []
