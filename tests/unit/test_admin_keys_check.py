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

import collections
import datetime

import atr.admin as admin
import atr.cache as cache
import atr.models.sql as sql
import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures

_BROKEN_BLOCK = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nnotbase64\n-----END PGP PUBLIC KEY BLOCK-----\n"
_CHECKSUM_ON_DATA_LINE = pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC.replace("cJBQ==\n=", "cJBQ===")


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


def test_certificate_block_findings_reports_text_that_only_openpgp_can_parse() -> None:
    certificate = _certificate(pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT, _CHECKSUM_ON_DATA_LINE)

    findings = admin._certificate_block_findings(certificate, set(), 0)

    assert [kind for kind, _ in findings] == ["unparseable"]


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

    findings = admin._certificate_block_report(certificates, signing_keys)

    assert collections.Counter(finding.kind for finding in findings) == {
        "multi-own-first": 1,
        "multi-own-not-first": 1,
        "shared-text": 4,
        "unparseable": 2,
        "metadata": 2,
        "signing-keys": 2,
    }
    rows = {(finding.kind, finding.fingerprint, finding.deleted): finding.detail for finding in findings}
    assert rows["multi-own-not-first", pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT, True] == (
        "2 certificates in the text"
    )
    assert rows["shared-text", "ab" * 20, False] == "Same text as 1 other key"
    details = [finding.detail for finding in findings]
    assert "User ID 'Nobody <nobody@example.invalid>' is stored but isn't on the key" in details
    assert any(detail.startswith("Self-signature date stored as 2030-01-01") for detail in details)
    assert any(detail.startswith("No rows for ") for detail in details)


def test_certificate_metadata_problems_ignores_unset_metadata() -> None:
    key, _ = pgp.openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    certificate = _certificate(
        pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT, pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC
    )
    uids = pgp.user_id_texts(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    rows = {facts.fingerprint.lower() for facts in pgp.signing_key_facts(key)}

    assert admin._certificate_metadata_problems(certificate, key, uids, rows) == []


def test_check_keys_finding_kind_says_whether_the_user_ids_name_the_owner() -> None:
    lookup = cache.EmailUidLookup.from_plain({"alice@example.org": "alice"})
    two_apache = ["Alice <alexp@apache.org>", "Alice <alice@apache.org>"]
    mapped = ["Alice <alexp@apache.org>", "Alice <alice@example.org>"]

    assert admin._check_keys_finding_kind(two_apache, "alice", "alexp", lookup) == "named"
    assert admin._check_keys_finding_kind(mapped, "alice", "alexp", lookup) == "named"
    assert admin._check_keys_finding_kind(mapped, None, "alexp", lookup) == "unowned"
    assert admin._check_keys_finding_kind(["Bob <bob@example.org>"], "bob", None, lookup) == "unnamed"
    assert admin._check_keys_finding_kind(mapped, "carol", "alexp", lookup) == "different"
