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

from types import SimpleNamespace

import openpgp

import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures


def _block_without_signature_type(block: str, signature_type: int) -> str:
    # Re-armour a certificate with every self-signature of a given type removed, which is what a
    # stripped re-upload looks like at the packet level
    key, _ = openpgp.PublicKey.from_armor(block)
    data = key.to_bytes()
    kept = bytearray()
    index = 0
    while index < len(data):
        start = index
        header = data[index]
        index += 1
        if header & 0x40:
            tag = header & 0x3F
            first = data[index]
            index += 1
            if first < 192:
                length = first
            elif first < 224:
                length = ((first - 192) << 8) + data[index] + 192
                index += 1
            else:
                length = int.from_bytes(data[index : index + 4], "big")
                index += 4
        else:
            tag = (header >> 2) & 0x0F
            width = (1, 2, 4)[header & 0x03]
            length = int.from_bytes(data[index : index + width], "big")
            index += width
        body = index
        index += length
        if not ((tag == 2) and (data[body] == 4) and (data[body + 1] == signature_type)):
            kept += data[start:index]
    stripped = openpgp.PublicKey.from_bytes(bytes(kept))
    return (stripped[0] if isinstance(stripped, tuple) else stripped).to_armored()


def test_latest_self_signature_skips_uid_revocations() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    latest = pgp.latest_self_signature(key)

    assert latest is not None
    assert latest.signature_type == "cert-positive"


def test_latest_self_signature_survives_a_revoked_primary_uid() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_UID_PUBLIC_KEY_ASC)

    latest = pgp.latest_self_signature(key)

    assert latest is not None
    assert latest.signature_type == "cert-positive"


def test_signing_key_status_expiry_follows_the_issuing_subkey() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert status.expires is not None
    assert status.expires.year == pgp_fixtures.EXPIRED_SUBKEY_SIGNING_EXPIRES_YEAR


def test_signing_key_status_expiry_ignores_a_later_primary_expiry() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    primary_expires = pgp.key_expires_at(key)
    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert primary_expires is not None
    assert primary_expires.year == pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_EXPIRES_YEAR
    assert status.expires != primary_expires


def test_signing_key_status_reports_an_issuer_naming_no_held_key_as_unidentified() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, set(), set())

    # Borrowing the primary's answer here would pass off its later expiry as the issuing subkey's
    assert status.identified is False
    assert status.expires is None
    assert status.can_sign is False


def test_signing_key_status_reads_the_primary_when_the_primary_issued_the_signature() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT}, set())

    assert status.identified is True
    assert status.expires == pgp.key_expires_at(key)


def test_signing_key_status_refuses_a_certify_only_primary_which_issued_the_signature() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT}, set())

    assert status.can_sign is False


def test_signing_key_status_permits_a_key_declaring_signing() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert status.can_sign is True


def test_signing_key_status_refuses_a_key_declaring_capabilities_without_signing() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_ENCRYPTION_FINGERPRINT}, set())

    assert status.can_sign is False


def test_declares_signing_permits_a_key_declaring_no_capabilities() -> None:
    # An absent key flags subpacket is indistinguishable from one declaring nothing, and older keys
    # often declare nothing at all, so silence must not be read as a refusal to sign
    signature = SimpleNamespace(
        key_flags=SimpleNamespace(
            certify=False,
            sign=False,
            encrypt_communications=False,
            encrypt_storage=False,
            authenticate=False,
            timestamping=False,
        )
    )

    assert pgp._declares_signing(signature) is True


def test_declares_signing_refuses_a_key_whose_self_signature_cannot_be_read() -> None:
    assert pgp._declares_signing(None) is False


def test_signing_key_status_flags_a_revoked_signing_subkey() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert status.revoked is True


def test_signing_key_status_flags_a_subkey_beneath_a_revoked_primary() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)

    # The subkey binding is intact, so only the primary's revocation cascading down can reject it
    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_PRIMARY_SIGNING_FINGERPRINT}, set())

    assert status.revoked is True


def test_signing_key_status_flags_a_revoked_primary() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT}, set())

    assert status.revoked is True


def test_signing_key_status_does_not_treat_a_revoked_user_id_as_a_revoked_key() -> None:
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_UID_FINGERPRINT}, set())

    assert status.revoked is False


def test_key_expires_at_ignores_a_revoked_user_id_self_signature() -> None:
    # A revocation is self-issued but declares no expiry, so reading it as the effective self-signature
    # would erase the expiry the certification actually carries
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    expires = pgp.key_expires_at(key)

    assert expires is not None
    assert expires.year == pgp_fixtures.REVOKED_UID_PRIMARY_EXPIRES_YEAR


def test_signing_key_status_reads_capabilities_past_a_revoked_user_id() -> None:
    # The same revocation carries no key flags, so admitting it would flip a certify-only primary to
    # can_sign purely because a secondary address was revoked
    key, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_UID_FINGERPRINT}, set())

    assert status.can_sign is False


def test_revocations_dropped_detects_a_revocation_removed_on_re_upload() -> None:
    stored, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)
    incoming, _ = openpgp.PublicKey.from_armor(
        _block_without_signature_type(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC, 0x20)
    )

    dropped = pgp.revocations_dropped(stored, incoming)

    assert pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT in dropped


def test_revocations_dropped_permits_a_revocation_added_on_re_upload() -> None:
    # Gap A relies on a genuine new revocation still being able to arrive, so growth is never a drop
    stored, _ = openpgp.PublicKey.from_armor(
        _block_without_signature_type(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC, 0x20)
    )
    incoming, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)

    assert pgp.revocations_dropped(stored, incoming) == set()


def test_revocations_dropped_detects_a_subkey_revocation_removed_on_re_upload() -> None:
    stored, _ = openpgp.PublicKey.from_armor(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC)
    incoming, _ = openpgp.PublicKey.from_armor(
        _block_without_signature_type(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC, 0x28)
    )

    dropped = pgp.revocations_dropped(stored, incoming)

    assert pgp_fixtures.REVOKED_SUBKEY_SIGNING_FINGERPRINT in dropped
