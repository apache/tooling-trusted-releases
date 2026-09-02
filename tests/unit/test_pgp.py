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
from types import SimpleNamespace

import openpgp
import pytest

import atr.pgp as pgp
import tests.unit.pgp_fixtures as pgp_fixtures


def _block_with_grafted_signature(target_block: str, source_block: str, signature_type: int, after_tag: int) -> str:
    # Lift a signature packet out of one certificate and splice it into another, just after the first
    # packet of `after_tag`, which is what a forged revocation dropped into a KEYS file looks like. The
    # signature was made by a different key, so it can't verify against the target
    source = openpgp.composed.SignedPublicKey.from_armor(source_block)[0].to_bytes()
    grafted = next(
        source[start:end]
        for tag, start, end, body in pgp_fixtures.packets(source)
        if (tag == 2) and (source[body] == 4) and (source[body + 1] == signature_type)
    )
    target = openpgp.composed.SignedPublicKey.from_armor(target_block)[0].to_bytes()
    kept = bytearray()
    inserted = False
    for tag, start, end, _body in pgp_fixtures.packets(target):
        kept += target[start:end]
        if (tag == after_tag) and not inserted:
            kept += grafted
            inserted = True
    return openpgp.composed.SignedPublicKey.from_bytes(bytes(kept)).to_armored()


def test_certificate_block_shape_names_every_shape() -> None:
    expired, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    revoked, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC)
    own = pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT

    assert pgp.certificate_block_shape([revoked], own) == "single"
    assert pgp.certificate_block_shape([revoked, expired], own) == "multi-own-first"
    assert pgp.certificate_block_shape([expired, revoked], own) == "multi-own-not-first"
    assert pgp.certificate_block_shape([revoked, revoked], own) == "own-certificate-repeated"
    assert pgp.certificate_block_shape([expired], own) == "wrong-fingerprint"
    assert pgp.certificate_block_shape([expired, expired], own) == "no-own-certificate"
    assert pgp.certificate_block_shape([], own) == "empty"


def test_certificate_components_lists_each_part_with_its_signatures() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    components = pgp.certificate_components(key)

    assert [component.kind for component in components] == ["primary", "user-id", "user-id", "subkey"]
    assert components[0].label == pgp_fixtures.REVOKED_UID_FINGERPRINT
    assert (components[0].flags, components[3].flags) == ("C", "S")
    assert [component.revoked for component in components] == [False, False, True, False]
    assert [(component.self_signatures, component.other_signatures) for component in components[1:3]] == [
        (1, 0),
        (2, 0),
    ]
    assert (components[1].facts is None) and (components[3].facts is not None)
    assert components[3].facts.fingerprint == pgp_fixtures.REVOKED_UID_SIGNING_FINGERPRINT


def test_certificate_components_revives_a_recertified_uid() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.RECERTIFIED_UID_PUBLIC_KEY_ASC)
    while_revoked = datetime.datetime(2021, 6, 1, tzinfo=datetime.UTC)
    after_recertification = datetime.datetime(2023, 1, 1, tzinfo=datetime.UTC)

    revoked_then = [c.revoked for c in pgp.certificate_components(key, at=while_revoked) if c.kind == "user-id"]
    revoked_now = [c.revoked for c in pgp.certificate_components(key, at=after_recertification) if c.kind == "user-id"]

    assert revoked_then == [False, True]
    assert revoked_now == [False, False]


def test_certificate_for_fingerprint_selects_the_named_certificate() -> None:
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )

    second = pgp.certificate_for_fingerprint(block, pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT.upper())

    assert second is not None
    assert second.fingerprint.lower() == pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT
    assert pgp.certificate_for_fingerprint(block, "0" * 40) is None


def test_certificate_for_fingerprint_refuses_a_repeated_certificate() -> None:
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC
    )

    with pytest.raises(ValueError, match="appears 2 times"):
        pgp.certificate_for_fingerprint(block, pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT)


def test_latest_self_signature_skips_uid_revocations() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    latest = pgp.latest_self_signature(key)

    assert latest is not None
    assert latest.typ() == "cert-positive"


def test_latest_self_signature_survives_a_revoked_primary_uid() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_UID_PUBLIC_KEY_ASC)

    latest = pgp.latest_self_signature(key)

    assert latest is not None
    assert latest.typ() == "cert-positive"


def test_apply_delta_removes_a_whole_component_for_a_bare_head() -> None:
    state = pgp.certificate_placements(pgp.merge_certificate_blocks([pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC]))
    user_id = sorted({head for head, _ in state if head[0] == 13})[0]
    primary = next(head for head, frame in state if (frame is None) and (head[0] == 6))
    without_user_id = frozenset(placement for placement in state if placement[0] != user_id)

    deletions, additions = pgp.delta_fragments(state, without_user_id)
    bare = pgp.fragment_placements(deletions, primary)

    assert (additions is None) and (deletions is not None)
    assert bare == {(user_id, None)}
    assert pgp.apply_delta(state, deletions, additions) == without_user_id
    with pytest.raises(ValueError, match="may not carry"):
        pgp.apply_delta(without_user_id, pgp.fragment_bytes(frozenset({(primary, None)})), None)
    assert pgp.fold_deltas([pgp.delta_fragments(frozenset(), state), (deletions, additions)]) == without_user_id
    assert pgp.apply_delta(without_user_id, *pgp.delta_fragments(without_user_id, state)) == state


def test_certificate_spans_are_the_raw_bytes_of_the_upload() -> None:
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC
    )

    spans = pgp.certificate_spans(block)

    assert len(spans) == 2
    assert b"".join(spans) == pgp._dearmored(block)
    assert [pgp.certificate_block_fingerprint(pgp._armored(span)) for span in spans] == [
        pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT,
        pgp_fixtures.REVOKED_SUBKEY_PRIMARY_FINGERPRINT,
    ]


@pytest.mark.parametrize("footer", ["=AAAA", "=xyz"])
def test_dearmored_ignores_the_checksum_footer(footer: str) -> None:
    block = pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC
    lines = block.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("="))
    altered = "\n".join([*lines[:index], footer, *lines[index + 1 :]]) + "\n"
    assert pgp._dearmored(altered) == pgp._dearmored(block)


def test_delta_fragments_are_minimal_and_fold_back_to_the_result() -> None:
    state = pgp.certificate_placements(pgp.merge_certificate_blocks([pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC]))
    primary = next(head for head, frame in state if (frame is None) and (head[0] == 6))
    attachment = sorted(placement for placement in state if (placement[1] is not None) and (placement[0][0] == 13))[0]
    without_attachment = state - {attachment}

    genesis = pgp.delta_fragments(frozenset(), state)
    deletions, additions = pgp.delta_fragments(state, without_attachment)
    readded = pgp.delta_fragments(without_attachment, state)

    assert genesis[0] is None
    assert pgp.fragment_placements(genesis[1]) == state
    assert additions is None
    assert all(tag != 6 for tag, _ in pgp._frames(deletions))
    assert pgp.fragment_placements(deletions, primary) == {(attachment[0], None), attachment}
    assert pgp.fold_deltas([genesis, (deletions, additions), readded]) == state
    assert pgp.delta_fragments(state, state) == (None, None)


def test_fragment_bytes_round_trip_keeps_every_placement() -> None:
    placements = pgp.certificate_placements(pgp_fixtures.LOCAL_CERTIFICATION_PUBLIC_KEY_ASC)
    canonical = pgp.merge_certificate_blocks([pgp_fixtures.LOCAL_CERTIFICATION_PUBLIC_KEY_ASC])
    merged = pgp.certificate_placements(canonical)

    primary = next(head for head, frame in placements if (frame is None) and (head[0] == 6))
    later = frozenset(placements - {(primary, None)})

    assert pgp.fragment_placements(pgp.fragment_bytes(placements)) == placements
    assert pgp.fragment_placements(pgp.fragment_bytes(later, primary), primary) == later
    assert len(placements) == len(merged) + 1
    assert pgp.fragment_bytes(merged) == pgp._dearmored(canonical)


def test_fragment_placements_refuses_skippable_packets() -> None:
    frames = pgp._frames(pgp._dearmored(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC))
    spliced = b"".join(pgp._framed(tag, body) for tag, body in [*frames[:1], (10, b"PGP"), *frames[1:]])

    with pytest.raises(ValueError, match="never carry"):
        pgp.fragment_placements(spliced)
    assert (10, b"PGP") not in pgp._frames(pgp._dearmored(pgp.merge_certificate_blocks([pgp._armored(spliced)])))


def test_merge_certificate_blocks_drops_local_certifications() -> None:
    merged = pgp.merge_certificate_blocks([pgp_fixtures.LOCAL_CERTIFICATION_PUBLIC_KEY_ASC])

    signatures = [pgp._parsed_signature(body) for tag, body in pgp._frames(pgp._dearmored(merged)) if tag == 2]

    assert [signature.typ() for signature in signatures if signature is not None] == ["cert-positive"]


def test_merge_certificate_blocks_keeps_unknown_packets() -> None:
    block = pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC
    frames = pgp._frames(pgp._dearmored(block))
    spliced = [*frames[:2], (60, b"experimental"), *frames[2:]]
    variant = pgp._armored(b"".join(pgp._framed(tag, body) for tag, body in spliced))

    merged = pgp.merge_certificate_blocks([variant, block])

    assert (60, b"experimental") in pgp._frames(pgp._dearmored(merged))
    assert pgp.merge_certificate_blocks([merged]) == merged


def test_merge_certificate_blocks_refuses_different_primaries() -> None:
    blocks = [pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC, pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC]

    with pytest.raises(ValueError, match="different primary keys"):
        pgp.merge_certificate_blocks(blocks)


def test_merge_certificate_blocks_unions_and_commutes() -> None:
    block = pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC
    canonical = pgp.merge_certificate_blocks([block])
    frames = pgp._frames(pgp._dearmored(block))
    without_revocation = pgp._armored(b"".join(pgp._framed(tag, body) for tag, body in frames[:4] + frames[5:]))
    without_subkey = pgp._armored(b"".join(pgp._framed(tag, body) for tag, body in frames[:6]))

    forward = pgp.merge_certificate_blocks([without_revocation, without_subkey])
    reverse = pgp.merge_certificate_blocks([without_subkey, without_revocation])
    folded = pgp.merge_certificate_blocks([forward, block])

    assert pgp.merge_certificate_blocks([canonical]) == canonical
    assert (forward == reverse) and (folded == canonical)


def test_reference_time_excludes_future_signatures() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)
    before = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    at_certification = datetime.datetime(2026, 7, 17, 14, 49, 58, tzinfo=datetime.UTC)

    assert pgp.latest_self_signature_created_at(key, at=before) is None
    assert pgp.key_expires_at(key, at=before) is None
    assert [facts.can_sign for facts in pgp.signing_key_facts(key, at=before)] == [False, False]
    revoked = [component.revoked for component in pgp.certificate_components(key, at=at_certification)]
    assert revoked == [False, False, False, False]
    assert pgp.latest_self_signature_created_at(key, at=at_certification) == at_certification
    assert pgp.latest_self_signature_created_at(key) is not None
    expires = pgp.key_expires_at(key)
    assert (expires is not None) and (expires.year == pgp_fixtures.REVOKED_UID_PRIMARY_EXPIRES_YEAR)


def test_signing_key_facts_conservative_when_every_uid_is_revoked() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.ALL_UIDS_REVOKED_PUBLIC_KEY_ASC)

    facts = pgp.signing_key_facts(key, at=datetime.datetime(2023, 1, 1, tzinfo=datetime.UTC))

    assert (facts[0].can_sign, facts[0].expires) == (False, None)


def test_signing_key_status_expiry_follows_the_issuing_subkey() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert status.expires is not None
    assert status.expires.year == pgp_fixtures.EXPIRED_SUBKEY_SIGNING_EXPIRES_YEAR


def test_signing_key_status_expiry_ignores_a_later_primary_expiry() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    primary_expires = pgp.key_expires_at(key)
    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert primary_expires is not None
    assert primary_expires.year == pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_EXPIRES_YEAR
    assert status.expires != primary_expires


def test_signing_key_status_reports_an_issuer_naming_no_held_key_as_unidentified() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, set(), set())

    # Borrowing the primary's answer here would pass off its later expiry as the issuing subkey's
    assert status.identified is False
    assert status.expires is None
    assert status.can_sign is False


def test_signing_key_status_reads_the_primary_when_the_primary_issued_the_signature() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT}, set())

    assert status.identified is True
    assert status.expires == pgp.key_expires_at(key)


def test_signing_key_status_refuses_a_certify_only_primary_which_issued_the_signature() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT}, set())

    assert status.can_sign is False


def test_signing_key_status_permits_a_key_declaring_signing() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert status.can_sign is True


def test_signing_key_status_refuses_a_key_declaring_capabilities_without_signing() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_ENCRYPTION_FINGERPRINT}, set())

    assert status.can_sign is False


def test_declares_signing_permits_a_key_declaring_no_capabilities() -> None:
    # An absent key flags subpacket is indistinguishable from one declaring nothing, and older keys
    # often declare nothing at all, so silence must not be read as a refusal to sign
    signature = SimpleNamespace(
        key_flags=lambda: SimpleNamespace(
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
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_SUBKEY_SIGNING_FINGERPRINT}, set())

    assert status.revoked is True


def test_signing_key_status_flags_a_subkey_beneath_a_revoked_primary() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)

    # The subkey binding is intact, so only the primary's revocation cascading down can reject it
    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_PRIMARY_SIGNING_FINGERPRINT}, set())

    assert status.revoked is True


def test_signing_key_status_flags_a_revoked_primary() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT}, set())

    assert status.revoked is True


def test_signing_key_status_ignores_a_forged_primary_revocation() -> None:
    # A key-revocation lifted from another key and dropped in front of this one verifies against
    # nothing here, so its mere presence must not block the key. Otherwise anyone could deny a project
    # its releases just by planting a revocation packet in a KEYS file
    forged = _block_with_grafted_signature(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC, 0x20, 6
    )
    key, _ = openpgp.composed.SignedPublicKey.from_armor(forged)

    # The forged packet really is present, so this shows verification rejects it rather than absence
    assert any(signature.typ() == "key-revocation" for signature in key.details.revocation_signatures)
    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_PRIMARY_FINGERPRINT}, set())
    assert status.revoked is False


def test_signing_key_status_ignores_a_forged_subkey_revocation() -> None:
    # The same forgery aimed at a subkey: a subkey-revocation made by another key can't verify as a
    # binding on this primary, so it must not revoke the subkey it sits beneath
    forged = _block_with_grafted_signature(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC, 0x28, 14
    )
    key, _ = openpgp.composed.SignedPublicKey.from_armor(forged)

    signing_subkey = next(
        subkey
        for subkey in key.public_subkeys
        if subkey.key.fingerprint.lower() == pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT
    )
    assert any(signature.typ() == "subkey-revocation" for signature in signing_subkey.signatures)
    status = pgp.signing_key_status(key, {pgp_fixtures.EXPIRED_SUBKEY_SIGNING_FINGERPRINT}, set())
    assert status.revoked is False


def test_signing_key_status_does_not_treat_a_revoked_user_id_as_a_revoked_key() -> None:
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_UID_FINGERPRINT}, set())

    assert status.revoked is False


def test_key_expires_at_ignores_a_revoked_user_id_self_signature() -> None:
    # A revocation is self-issued but declares no expiry, so reading it as the effective self-signature
    # would erase the expiry the certification actually carries
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    expires = pgp.key_expires_at(key)

    assert expires is not None
    assert expires.year == pgp_fixtures.REVOKED_UID_PRIMARY_EXPIRES_YEAR


def test_signing_key_status_reads_capabilities_past_a_revoked_user_id() -> None:
    # The same revocation carries no key flags, so admitting it would flip a certify-only primary to
    # can_sign purely because a secondary address was revoked
    key, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_UID_PUBLIC_KEY_ASC)

    status = pgp.signing_key_status(key, {pgp_fixtures.REVOKED_UID_FINGERPRINT}, set())

    assert status.can_sign is False


def test_revocations_dropped_detects_a_revocation_removed_on_re_upload() -> None:
    stored, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)
    incoming, _ = openpgp.composed.SignedPublicKey.from_armor(
        pgp_fixtures.block_without_signature_type(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC, 0x20)
    )

    dropped = pgp.revocations_dropped(stored, incoming)

    assert pgp_fixtures.REVOKED_PRIMARY_FINGERPRINT in dropped


def test_revocations_dropped_permits_a_revocation_added_on_re_upload() -> None:
    # Gap A relies on a genuine new revocation still being able to arrive, so growth is never a drop
    stored, _ = openpgp.composed.SignedPublicKey.from_armor(
        pgp_fixtures.block_without_signature_type(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC, 0x20)
    )
    incoming, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_PRIMARY_PUBLIC_KEY_ASC)

    assert pgp.revocations_dropped(stored, incoming) == set()


def test_revocations_dropped_detects_a_subkey_revocation_removed_on_re_upload() -> None:
    stored, _ = openpgp.composed.SignedPublicKey.from_armor(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC)
    incoming, _ = openpgp.composed.SignedPublicKey.from_armor(
        pgp_fixtures.block_without_signature_type(pgp_fixtures.REVOKED_SUBKEY_PUBLIC_KEY_ASC, 0x28)
    )

    dropped = pgp.revocations_dropped(stored, incoming)

    assert pgp_fixtures.REVOKED_SUBKEY_SIGNING_FINGERPRINT in dropped


def test_user_id_texts_decodes_utf8_then_latin_1() -> None:
    state = pgp.certificate_placements(pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC)
    latin = "Ren\u00e9 <r@example.com>".encode("latin-1")
    block = pgp.certificate_block(frozenset(state | {((13, latin), None)}))
    texts = pgp.user_id_texts(block)
    assert "Ren\u00e9 <r@example.com>" in texts
    assert all("\ufffd" not in text for text in texts)
