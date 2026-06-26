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

import itertools
import secrets
from typing import Final

import pytest

import atr.noisy as noisy

BASE32_ALPHABET: Final = noisy.BASE32_ALPHABET
BASE32_SET: Final = noisy.BASE32_ALPHABET_SET
BASE36_ALPHABET: Final = noisy.BASE36_ALPHABET
BASE36_SET: Final = noisy.BASE36_ALPHABET_SET
BASE37_ALPHABET: Final = noisy.BASE37_ALPHABET
BASE37_SET: Final = noisy.BASE37_ALPHABET_SET
COMPONENT_SET: Final = noisy.COMPONENT_ALPHABET_SET
FIELD_MAP: Final = noisy.FIELD_MAP
K: Final = noisy.K
PREFIX: Final = noisy.PREFIX
Q: Final = noisy.Q

_MAX_NAMESPACE: Final = b"z_" + b"a" * 29 + b"z"

_REPRESENTATIVE_NAMESPACES: Final = (
    b"2",
    b"e_org_example",
    b"i_org_example_api",
    _MAX_NAMESPACE,
)

_REPRESENTATIVE_PAYLOADS: Final = (
    b"2" * 32,
    BASE32_ALPHABET,
)

_SPEC_VECTORS: Final = [
    (
        None,
        b"2",
        b"22222222222222222222222222222222",
        b"secret_2_22222222222222222222222222222222_ogwraat",
    ),
    (
        None,
        b"2",
        b"23456789abcdefghijkmnpqrstuvwxyz",
        b"secret_2_23456789abcdefghijkmnpqrstuvwxyz11izsa_3",
    ),
    (
        b"example.org",
        b"e_org_example",
        b"22222222222222222222222222222222",
        b"secret_e_org_example_222222222222222222222222222222222mcnca82",
    ),
    (
        b"example.org",
        b"e_org_example",
        b"23456789abcdefghijkmnpqrstuvwxyz",
        b"secret_e_org_example_23456789abcdefghijkmnpqrstuvwxyztzeqda7b",
    ),
]


def checksum_compute(message: bytes | bytearray) -> bytes:
    return noisy.checksum_compute(message)


def construct_namespace(fqdn: bytes | None) -> bytes:
    return noisy.construct_namespace(noisy.FQDN(fqdn) if fqdn is not None else None)


def construct_namespace_domain(namespace: bytes) -> bytes | None:
    namespace = noisy.Namespace(noisy.NamespaceString(namespace))
    return noisy.construct_namespace_domain(namespace)


def construct_noisy_secret_tag(namespace: bytes, payload: bytes) -> bytes:
    return noisy.construct_noisy_secret_tag(noisy.NamespaceString(namespace), noisy.PayloadString(payload))


def domain(candidate: bytes) -> bytes | None:
    if not noisy.is_candidate(candidate):
        return None

    namespace = noisy.construct_candidate_namespace(candidate)
    payload = noisy.construct_candidate_payload(candidate)
    if namespace is None or payload is None:
        return None
    return noisy.construct_namespace_domain(noisy.Namespace(namespace))


def is_namespace_string(value: bytes) -> bool:
    return noisy.is_namespace_string(value)


def _det_4x4_mod(matrix: list[list[int]], mod: int) -> int:
    m = [row[:] for row in matrix]
    det = 1
    for col in range(4):
        pivot = next((row for row in range(col, 4) if m[row][col] % mod != 0), None)
        if pivot is None:
            return 0
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]
            det = (-det) % mod
        inv = pow(m[col][col], mod - 2, mod)
        for row in range(col + 1, 4):
            factor = (m[row][col] * inv) % mod
            for k in range(col, 4):
                m[row][k] = (m[row][k] - factor * m[col][k]) % mod
        det = (det * m[col][col]) % mod
    return det


def _evaluate_poly_low_first(coefficients: list[int], x: int, modulus: int) -> int:
    value = 0
    for c in reversed(coefficients):
        value = (value * x + c) % modulus
    return value


def _message_pair(namespace: bytes, payload: bytes) -> tuple[bytes, bytes]:
    padded = namespace + b"_" * (K - len(namespace))
    return padded[::2] + payload[::2], padded[1::2] + payload[1::2]


def _namespace_bounds(namespace: bytes) -> tuple[int, int]:
    start = len(PREFIX) + 1
    return start, start + len(namespace)


def _namespace_for_length(total_length: int) -> bytes:
    if total_length == 1:
        return b"2"
    joined_length = total_length - 2
    if joined_length == 1:
        joined = b"a"
    elif joined_length == 2:
        joined = b"ab"
    else:
        joined = b"a" + b"b" * (joined_length - 2) + b"c"
    return bytes([BASE32_ALPHABET[len(joined) + 1]]) + b"_" + joined


def _payload_bounds(namespace: bytes) -> tuple[int, int]:
    start = len(PREFIX) + 1 + len(namespace) + 1
    return start, start + K


def _representative_vectors() -> list[tuple[bytes, bytes, bytes]]:
    return [
        (namespace, payload, construct_noisy_secret_tag(namespace, payload))
        for namespace in _REPRESENTATIVE_NAMESPACES
        for payload in _REPRESENTATIVE_PAYLOADS
    ]


class TestAlphabets:
    def test_base32_excludes_ambiguous_characters(self) -> None:
        excluded = {ord("0"), ord("1"), ord("_"), ord("l"), ord("o")}
        assert BASE32_SET & excluded == set()

    def test_base32_length(self) -> None:
        assert len(BASE32_ALPHABET) == 32

    def test_base32_sorted_ascii(self) -> None:
        assert BASE32_ALPHABET == bytes(sorted(BASE32_ALPHABET))

    def test_base32_subset_of_base37(self) -> None:
        assert BASE32_SET.issubset(BASE37_SET)

    def test_base36_length(self) -> None:
        assert len(BASE36_ALPHABET) == 36

    def test_base36_sorted_ascii(self) -> None:
        assert BASE36_ALPHABET == bytes(sorted(BASE36_ALPHABET))

    def test_base37_length(self) -> None:
        assert len(BASE37_ALPHABET) == 37

    def test_base37_matches_field_size(self) -> None:
        assert len(BASE37_ALPHABET) == Q

    def test_base37_sorted_ascii(self) -> None:
        assert BASE37_ALPHABET == bytes(sorted(BASE37_ALPHABET))

    def test_component_set_includes_hyphen_and_base36(self) -> None:
        assert ord("-") in COMPONENT_SET
        assert BASE36_SET.issubset(COMPONENT_SET)

    def test_field_map_bijective(self) -> None:
        assert set(FIELD_MAP.values()) == set(range(37))

    def test_field_map_size(self) -> None:
        assert len(FIELD_MAP) == 37

    def test_underscore_field_element_nonzero(self) -> None:
        assert FIELD_MAP[ord("_")] != 0

    def test_underscore_not_in_base32(self) -> None:
        assert ord("_") not in BASE32_SET

    def test_underscore_not_in_base36(self) -> None:
        assert ord("_") not in BASE36_SET

    def test_zero_character_not_in_base32(self) -> None:
        assert ord("0") not in BASE32_SET


class TestChecksumCompute:
    def test_all_constant_messages_produce_constant_checksums(self) -> None:
        for ch in BASE37_ALPHABET:
            msg = bytes([ch]) * 32
            assert checksum_compute(msg) == bytes([ch]) * 4

    def test_codeword_vanishes_at_generator_roots(self) -> None:
        messages = [
            b"_" * 32,
            b"0" * 32,
            b"secret__________________________",
            BASE37_ALPHABET[:32],
        ]
        for message in messages:
            cksum = checksum_compute(message)
            codeword = [FIELD_MAP[b] for b in message + cksum]
            for root in [2, 4, 8, 16]:
                assert _evaluate_poly_low_first(codeword, root, Q) == 0

    def test_codeword_vanishes_for_random_messages(self) -> None:
        for _ in range(50):
            message = bytes([secrets.choice(BASE37_ALPHABET) for _ in range(32)])
            cksum = checksum_compute(message)
            codeword = [FIELD_MAP[b] for b in message + cksum]
            for root in [2, 4, 8, 16]:
                assert _evaluate_poly_low_first(codeword, root, Q) == 0

    def test_output_in_base37(self) -> None:
        cksum = checksum_compute(b"_" * 32)
        assert all(b in BASE37_SET for b in cksum)

    def test_output_length(self) -> None:
        assert len(checksum_compute(b"_" * 32)) == 4

    def test_single_error_detected(self) -> None:
        for _ in range(50):
            message = bytes([secrets.choice(BASE37_ALPHABET) for _ in range(32)])
            cksum = checksum_compute(message)
            codeword = bytearray(message + cksum)
            idx = secrets.randbelow(36)
            original = codeword[idx]
            while codeword[idx] == original:
                codeword[idx] = secrets.choice(BASE37_ALPHABET)
            recomputed = checksum_compute(bytes(codeword[:32]))
            assert recomputed != bytes(codeword[32:36])

    def test_zero_message_has_zero_checksum(self) -> None:
        assert checksum_compute(b"0" * 32) == b"0000"


class TestCreate:
    def test_all_fqdn_lengths_roundtrip(self) -> None:
        for length in range(1, 31):
            fqdn = b"a" * length
            secret = noisy.create(fqdn)
            assert noisy.verify(secret) is True

    def test_each_result_unique(self) -> None:
        results = {noisy.create() for _ in range(100)}
        assert len(results) == 100

    def test_length_no_domain(self) -> None:
        assert len(noisy.create()) == 49

    def test_length_varies_with_fqdn(self) -> None:
        for length in range(1, 31):
            fqdn = b"a" * length
            result = noisy.create(fqdn)
            assert len(result) == 50 + length

    def test_max_fqdn_length(self) -> None:
        result = noisy.create(b"a" * 30)
        assert len(result) == 80
        assert result.startswith(b"secret_")
        assert noisy.verify(result) is True

    def test_namespace_in_output(self) -> None:
        result = noisy.create(b"apache.org")
        assert b"d_org_apache" in result

    def test_no_domain_prefix(self) -> None:
        assert noisy.create().startswith(b"secret_2_")

    def test_only_base37_chars(self) -> None:
        for _ in range(50):
            result = noisy.create(b"test")
            assert all(b in BASE37_SET for b in result)

    def test_payload_uses_base32(self) -> None:
        result = noisy.create()
        payload = result[9:41]
        assert len(payload) == 32
        assert all(b in BASE32_SET for b in payload)

    def test_result_verifies(self) -> None:
        for _ in range(20):
            assert noisy.verify(noisy.create()) is True

    def test_result_with_fqdn_verifies(self) -> None:
        for _ in range(20):
            assert noisy.verify(noisy.create(b"example.org")) is True


class TestDomain:
    def test_invalid_returns_none(self) -> None:
        assert domain(b"not a secret") is None

    def test_no_domain_returns_none(self) -> None:
        secret = noisy.create()
        assert domain(secret) is None

    def test_roundtrip_hyphenated(self) -> None:
        secret = noisy.create(b"a-b.org")
        assert domain(secret) == b"a-b.org"

    def test_roundtrip_simple(self) -> None:
        secret = noisy.create(b"apache.org")
        assert domain(secret) == b"apache.org"

    def test_roundtrip_subdomain(self) -> None:
        secret = noisy.create(b"api.example.org")
        assert domain(secret) == b"api.example.org"

    def test_wrong_length_returns_none(self) -> None:
        assert domain(b"a" * 50) is None


class TestErrorDetection:
    def test_adjacent_transpositions_detected(self) -> None:
        for _, _ns, _pl, secret in _SPEC_VECTORS:
            for pos in range(len(secret) - 1):
                if secret[pos] == secret[pos + 1]:
                    continue
                mutated = bytearray(secret)
                mutated[pos], mutated[pos + 1] = mutated[pos + 1], mutated[pos]
                assert noisy.verify(bytes(mutated)) is False

    def test_burst_of_8_consecutive_in_payload_detected(self) -> None:
        for _, namespace, _pl, secret in _SPEC_VECTORS:
            ns_len = len(namespace)
            payload_start = 8 + ns_len
            for start_offset in range(32 - 7):
                mutated = bytearray(secret)
                changed = False
                for i in range(8):
                    pos = payload_start + start_offset + i
                    old = mutated[pos]
                    idx = BASE32_ALPHABET.index(old)
                    new = BASE32_ALPHABET[(idx + 1) % 32]
                    if new != old:
                        changed = True
                    mutated[pos] = new
                if changed:
                    assert noisy.verify(bytes(mutated)) is False

    def test_double_substitution_in_even_payload_positions_detected(self) -> None:
        _, namespace, _pl, secret = _SPEC_VECTORS[3]
        payload_start = len(namespace) + 8
        even_positions = list(range(payload_start, payload_start + 32, 2))
        for pos1, pos2 in itertools.combinations(even_positions, 2):
            mutated = bytearray(secret)
            idx1 = BASE32_ALPHABET.index(mutated[pos1])
            idx2 = BASE32_ALPHABET.index(mutated[pos2])
            mutated[pos1] = BASE32_ALPHABET[(idx1 + 1) % 32]
            mutated[pos2] = BASE32_ALPHABET[(idx2 + 2) % 32]
            if bytes(mutated) != secret:
                assert noisy.verify(bytes(mutated)) is False

    def test_double_substitution_in_odd_payload_positions_detected(self) -> None:
        _, namespace, _pl, secret = _SPEC_VECTORS[3]
        payload_start = len(namespace) + 8
        odd_positions = list(range(payload_start + 1, payload_start + 32, 2))
        for pos1, pos2 in itertools.combinations(odd_positions, 2):
            mutated = bytearray(secret)
            idx1 = BASE32_ALPHABET.index(mutated[pos1])
            idx2 = BASE32_ALPHABET.index(mutated[pos2])
            mutated[pos1] = BASE32_ALPHABET[(idx1 + 1) % 32]
            mutated[pos2] = BASE32_ALPHABET[(idx2 + 2) % 32]
            if bytes(mutated) != secret:
                assert noisy.verify(bytes(mutated)) is False

    def test_single_byte_corruption(self) -> None:
        for _ in range(20):
            secret = noisy.create(b"test")
            mutated = bytearray(secret)
            idx = secrets.randbelow(len(mutated))
            original = mutated[idx]
            while mutated[idx] == original:
                mutated[idx] = secrets.choice(BASE37_ALPHABET)
            assert noisy.verify(bytes(mutated)) is False

    def test_single_substitutions_detected(self) -> None:
        for _, _ns, _pl, secret in _SPEC_VECTORS:
            for pos in range(len(secret)):
                original = secret[pos]
                for replacement in BASE37_ALPHABET:
                    if replacement == original:
                        continue
                    mutated = bytearray(secret)
                    mutated[pos] = replacement
                    assert noisy.verify(bytes(mutated)) is False


class TestGeneratorPolynomial:
    def test_alpha_generates_all_nonzero_elements(self) -> None:
        generated = {pow(2, i, 37) for i in range(36)}
        assert generated == set(range(1, 37))

    def test_alpha_is_primitive_root(self) -> None:
        proper_divisors_of_36 = [1, 2, 3, 4, 6, 9, 12, 18]
        for d in proper_divisors_of_36:
            assert pow(2, d, 37) != 1
        assert pow(2, 36, 37) == 1

    def test_coefficients_from_roots(self) -> None:
        poly = [1, (-2) % 37]
        for root in [4, 8, 16]:
            new_poly = [0] * (len(poly) + 1)
            for i, c in enumerate(poly):
                new_poly[i] = (new_poly[i] + c) % 37
                new_poly[i + 1] = (new_poly[i + 1] - c * root) % 37
            poly = new_poly
        assert poly == [1, 7, 21, 2, 25]

    def test_field_size_is_prime(self) -> None:
        for d in range(2, int(37**0.5) + 1):
            assert 37 % d != 0

    def test_minimum_distance_all_4x4_submatrices_nonsingular(self) -> None:
        n = 36
        t = 4
        alpha = 2
        q = 37
        parity_check = [[pow(alpha, (i + 1) * j, q) for j in range(n)] for i in range(t)]
        for cols in itertools.combinations(range(n), t):
            submatrix = [[parity_check[i][j] for j in cols] for i in range(t)]
            assert _det_4x4_mod(submatrix, q) != 0

    def test_roots_of_generator(self) -> None:
        gen = [25, 2, 21, 7, 1]
        for root in [2, 4, 8, 16]:
            assert _evaluate_poly_low_first(gen, root, 37) == 0


class TestGrammar:
    def test_full_fqdn_structure(self) -> None:
        secret = noisy.create(b"extra.airflow.apache.org")
        namespace = b"t_org_apache_airflow_extra"
        ns_len = len(namespace)
        assert secret[:6] == b"secret"
        assert secret[6:7] == b"_"
        assert secret[7 : 7 + ns_len] == namespace
        assert secret[7 + ns_len : 8 + ns_len] == b"_"
        assert len(secret) == 48 + ns_len

    def test_no_domain_structure(self) -> None:
        secret = noisy.create()
        assert secret[:6] == b"secret"
        assert secret[6:7] == b"_"
        assert secret[7:8] == b"2"
        assert secret[8:9] == b"_"
        assert len(secret) == 49

    def test_simple_fqdn_structure(self) -> None:
        secret = noisy.create(b"apache.org")
        namespace = b"d_org_apache"
        ns_len = len(namespace)
        assert secret[:6] == b"secret"
        assert secret[6:7] == b"_"
        assert secret[7 : 7 + ns_len] == namespace
        assert secret[7 + ns_len : 8 + ns_len] == b"_"
        assert len(secret) == 48 + ns_len


class TestInterleavingProtection:
    def test_namespace_change_affects_both_messages(self) -> None:
        payload = b"2" * 32
        padded_before = b"7_test" + b"_" * 26
        padded_after = b"7_xtest" + b"_" * 25

        even_before = padded_before[::2] + payload[::2]
        even_after = padded_after[::2] + payload[::2]
        odd_before = padded_before[1::2] + payload[1::2]
        odd_after = padded_after[1::2] + payload[1::2]

        assert even_before != even_after
        assert odd_before != odd_after


class TestLengthGapProtection:
    def test_delete_from_51_gives_rejected_50(self) -> None:
        secret = noisy.create(b"a")
        assert len(secret) == 51
        for pos in range(len(secret)):
            mutated = secret[:pos] + secret[pos + 1 :]
            assert len(mutated) == 50
            assert noisy.verify(mutated) is False

    def test_insert_into_49_gives_rejected_50(self) -> None:
        secret = noisy.create()
        assert len(secret) == 49
        for ch in BASE37_ALPHABET:
            mutated = secret[:8] + bytes([ch]) + secret[8:]
            assert len(mutated) == 50
            assert noisy.verify(mutated) is False

    def test_length_49_valid(self) -> None:
        secret = noisy.create()
        assert len(secret) == 49
        assert noisy.verify(secret) is True

    def test_length_50_always_rejected(self) -> None:
        assert noisy.verify(b"a" * 50) is False

    def test_length_51_valid(self) -> None:
        secret = noisy.create(b"a")
        assert len(secret) == 51
        assert noisy.verify(secret) is True


class TestMalformedStructure:
    def test_missing_prefix(self) -> None:
        secret = noisy.create()
        mutated = b"abcdef" + secret[6:]
        assert noisy.verify(mutated) is False

    def test_non_base32_in_payload(self) -> None:
        secret = noisy.create()
        mutated = bytearray(secret)
        mutated[9] = ord("0")
        assert noisy.verify(bytes(mutated)) is False

    def test_underscore_in_payload(self) -> None:
        secret = noisy.create()
        mutated = bytearray(secret)
        mutated[9] = ord("_")
        assert noisy.verify(bytes(mutated)) is False

    def test_wrong_separator_after_namespace(self) -> None:
        secret = noisy.create(b"test")
        mutated = bytearray(secret)
        sep_pos = 7 + len(construct_namespace(b"test"))
        mutated[sep_pos] = ord("a")
        assert noisy.verify(bytes(mutated)) is False

    def test_wrong_separator_after_prefix(self) -> None:
        secret = noisy.create()
        mutated = bytearray(secret)
        mutated[6] = ord("a")
        assert noisy.verify(bytes(mutated)) is False


class TestMutationResistance:
    def test_no_single_deletion_validates_long_fqdn(self) -> None:
        secret = noisy.create(b"airflow.apache.org")
        for pos in range(len(secret)):
            mutated = secret[:pos] + secret[pos + 1 :]
            assert noisy.verify(mutated) is False

    def test_no_single_deletion_validates_no_domain(self) -> None:
        secret = noisy.create()
        for pos in range(len(secret)):
            mutated = secret[:pos] + secret[pos + 1 :]
            assert noisy.verify(mutated) is False

    def test_no_single_deletion_validates_short_fqdn(self) -> None:
        secret = noisy.create(b"test")
        for pos in range(len(secret)):
            mutated = secret[:pos] + secret[pos + 1 :]
            assert noisy.verify(mutated) is False

    def test_no_single_insertion_validates_long_fqdn(self) -> None:
        secret = noisy.create(b"airflow.apache.org")
        for pos in range(len(secret) + 1):
            for ch in BASE37_ALPHABET:
                mutated = secret[:pos] + bytes([ch]) + secret[pos:]
                assert noisy.verify(mutated) is False

    def test_no_single_insertion_validates_no_domain(self) -> None:
        secret = noisy.create()
        for pos in range(len(secret) + 1):
            for ch in BASE37_ALPHABET:
                mutated = secret[:pos] + bytes([ch]) + secret[pos:]
                assert noisy.verify(mutated) is False

    def test_no_single_insertion_validates_short_fqdn(self) -> None:
        secret = noisy.create(b"test")
        for pos in range(len(secret) + 1):
            for ch in BASE37_ALPHABET:
                mutated = secret[:pos] + bytes([ch]) + secret[pos:]
                assert noisy.verify(mutated) is False


class TestNamespaceConstruct:
    def test_empty_component_rejected(self) -> None:
        with pytest.raises(ValueError):
            construct_namespace(b"bad..example")

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            construct_namespace(b"")

    def test_hyphen_boundary_end_rejected(self) -> None:
        with pytest.raises(ValueError):
            construct_namespace(b"test-.org")

    def test_hyphen_boundary_start_rejected(self) -> None:
        with pytest.raises(ValueError):
            construct_namespace(b"-test.org")

    def test_hyphen_conversion(self) -> None:
        assert construct_namespace(b"a-b.org") == b"b_org_a__b"

    def test_invalid_chars_rejected(self) -> None:
        with pytest.raises(ValueError):
            construct_namespace(b"Hello.org")

    def test_length_prefix_correct_for_all_fqdn_lengths(self) -> None:
        for fqdn_len in range(1, 31):
            fqdn = b"a" * fqdn_len
            namespace = construct_namespace(fqdn)
            assert namespace[0] == BASE32_ALPHABET[len(namespace) - 1]

    def test_multi_component(self) -> None:
        assert construct_namespace(b"apache.org") == b"d_org_apache"

    def test_multi_level_domain(self) -> None:
        assert construct_namespace(b"extra.airflow.apache.org") == b"t_org_apache_airflow_extra"

    def test_none_produces_digit_two(self) -> None:
        assert construct_namespace(None) == b"2"

    def test_single_component(self) -> None:
        assert construct_namespace(b"test") == b"7_test"

    def test_too_long_rejected(self) -> None:
        with pytest.raises(ValueError):
            construct_namespace(b"a" * 31)


class TestNamespaceDomain:
    def test_hyphen_roundtrip(self) -> None:
        ns = construct_namespace(b"a-b.org")
        assert construct_namespace_domain(ns) == b"a-b.org"

    def test_multi_level_roundtrip(self) -> None:
        ns = construct_namespace(b"api.example.org")
        assert construct_namespace_domain(ns) == b"api.example.org"

    def test_none_roundtrip(self) -> None:
        ns = construct_namespace(None)
        assert construct_namespace_domain(ns) is None

    def test_simple_roundtrip(self) -> None:
        ns = construct_namespace(b"apache.org")
        assert construct_namespace_domain(ns) == b"apache.org"


class TestNamespaceStringCheck:
    def test_empty_rejected(self) -> None:
        assert is_namespace_string(b"") is False

    def test_first_content_char_not_in_base36_rejected(self) -> None:
        assert is_namespace_string(b"6___a") is False

    def test_internal_non_base37_char_rejected(self) -> None:
        assert is_namespace_string(b"6_a-a") is False

    def test_last_content_char_not_in_base36_rejected(self) -> None:
        assert is_namespace_string(b"6_a__") is False

    def test_length_two_rejected(self) -> None:
        assert is_namespace_string(b"ab") is False

    def test_no_domain_accepted(self) -> None:
        assert is_namespace_string(b"2") is True

    def test_second_char_not_underscore_rejected(self) -> None:
        assert is_namespace_string(b"7atest") is False

    def test_too_long_rejected(self) -> None:
        ns = b"z_" + b"a" * 30 + b"z"
        assert len(ns) == 33
        assert is_namespace_string(ns) is False

    def test_valid_namespace_accepted(self) -> None:
        assert is_namespace_string(b"e_org_example") is True

    def test_wrong_length_prefix_rejected(self) -> None:
        assert is_namespace_string(b"4_org_example") is False


class TestPaddingInjectivity:
    def test_boundary_char_ensures_injectivity(self) -> None:
        for length in [1, *list(range(3, 33))]:
            namespace = _namespace_for_length(length)
            padded = namespace + b"_" * (32 - length)
            assert padded[length - 1] != ord("_")
            if length < 32:
                assert padded[length] == ord("_")

    def test_different_lengths_produce_different_padded_forms(self) -> None:
        valid_lengths = [1, *list(range(3, 33))]
        padded_forms = {}
        for length in valid_lengths:
            namespace = _namespace_for_length(length)
            padded_forms[length] = namespace + b"_" * (32 - len(namespace))
        for l1, l2 in itertools.combinations(valid_lengths, 2):
            assert padded_forms[l1] != padded_forms[l2]

    def test_underscore_internal_namespaces_still_injective(self) -> None:
        ns_a = construct_namespace(b"a.b")
        ns_b = construct_namespace(b"a.b.c")
        padded_a = ns_a + b"_" * (32 - len(ns_a))
        padded_b = ns_b + b"_" * (32 - len(ns_b))
        assert padded_a != padded_b


class TestRegionTargetedMutation:
    def test_no_single_namespace_deletion(self) -> None:
        for namespace, _payload, secret in _representative_vectors():
            ns_start, ns_stop = _namespace_bounds(namespace)
            for position in range(ns_start, ns_stop):
                mutated = secret[:position] + secret[position + 1 :]
                assert noisy.verify(mutated) is False

    def test_no_single_namespace_insertion(self) -> None:
        for namespace, _payload, secret in _representative_vectors():
            ns_start, ns_stop = _namespace_bounds(namespace)
            for position in range(ns_start, ns_stop + 1):
                for inserted in BASE37_ALPHABET:
                    mutated = secret[:position] + bytes([inserted]) + secret[position:]
                    assert noisy.verify(mutated) is False

    def test_no_single_payload_deletion(self) -> None:
        for namespace, _payload, secret in _representative_vectors():
            pl_start, pl_stop = _payload_bounds(namespace)
            for position in range(pl_start, pl_stop):
                mutated = secret[:position] + secret[position + 1 :]
                assert noisy.verify(mutated) is False

    def test_no_single_payload_insertion(self) -> None:
        for namespace, _payload, secret in _representative_vectors():
            pl_start, pl_stop = _payload_bounds(namespace)
            for position in range(pl_start, pl_stop + 1):
                for inserted in BASE32_ALPHABET:
                    mutated = secret[:position] + bytes([inserted]) + secret[position:]
                    assert noisy.verify(mutated) is False

    def test_representative_vectors_verify(self) -> None:
        for _namespace, _payload, secret in _representative_vectors():
            assert noisy.verify(secret) is True


class TestSpecVectors:
    def test_tag_construct_matches_vector_1(self) -> None:
        _, namespace, payload, expected = _SPEC_VECTORS[0]
        assert construct_noisy_secret_tag(namespace, payload) == expected

    def test_tag_construct_matches_vector_2(self) -> None:
        _, namespace, payload, expected = _SPEC_VECTORS[1]
        assert construct_noisy_secret_tag(namespace, payload) == expected

    def test_tag_construct_matches_vector_3(self) -> None:
        _, namespace, payload, expected = _SPEC_VECTORS[2]
        assert construct_noisy_secret_tag(namespace, payload) == expected

    def test_tag_construct_matches_vector_4(self) -> None:
        _, namespace, payload, expected = _SPEC_VECTORS[3]
        assert construct_noisy_secret_tag(namespace, payload) == expected

    def test_vectors_all_verify(self) -> None:
        for _, _ns, _pl, secret in _SPEC_VECTORS:
            assert noisy.verify(secret) is True

    def test_vectors_codewords_vanish_at_roots(self) -> None:
        for _, namespace, payload, secret in _SPEC_VECTORS:
            even_msg, odd_msg = _message_pair(namespace, payload)
            even_cksum = secret[-8::2]
            odd_cksum = secret[-7::2]
            even_cw = [FIELD_MAP[b] for b in even_msg + even_cksum]
            odd_cw = [FIELD_MAP[b] for b in odd_msg + odd_cksum]
            for root in [2, 4, 8, 16]:
                assert _evaluate_poly_low_first(even_cw, root, Q) == 0
                assert _evaluate_poly_low_first(odd_cw, root, Q) == 0

    def test_vectors_field_element_mapping(self) -> None:
        even_msg = b"2_______________2222222222222222"
        odd_msg = b"________________2222222222222222"
        even_syms = [FIELD_MAP[b] for b in even_msg]
        odd_syms = [FIELD_MAP[b] for b in odd_msg]
        assert even_syms == [2] + ([10] * 15) + ([2] * 16)
        assert odd_syms == ([10] * 16) + ([2] * 16)

    def test_vectors_have_correct_lengths(self) -> None:
        assert len(_SPEC_VECTORS[0][3]) == 49
        assert len(_SPEC_VECTORS[1][3]) == 49
        assert len(_SPEC_VECTORS[2][3]) == 61
        assert len(_SPEC_VECTORS[3][3]) == 61

    def test_vectors_namespace_domain_roundtrip(self) -> None:
        for fqdn, namespace, _pl, _secret in _SPEC_VECTORS:
            if fqdn is not None:
                assert construct_namespace(fqdn) == namespace
                assert construct_namespace_domain(namespace) == fqdn


class TestTagConstruct:
    def test_checksum_even_odd_positions(self) -> None:
        namespace = b"e_org_example"
        payload = b"2" * 32
        tag = construct_noisy_secret_tag(namespace, payload)
        even_msg, odd_msg = _message_pair(namespace, payload)
        expected_even = checksum_compute(even_msg)
        expected_odd = checksum_compute(odd_msg)
        assert tag[-8::2] == expected_even
        assert tag[-7::2] == expected_odd

    def test_prefix_and_separators(self) -> None:
        tag = construct_noisy_secret_tag(b"2", b"2" * 32)
        assert tag[:7] == b"secret_"
        assert tag[7:8] == b"2"
        assert tag[8:9] == b"_"

    def test_structure_with_domain(self) -> None:
        namespace = b"e_org_example"
        payload = BASE32_ALPHABET
        tag = construct_noisy_secret_tag(namespace, payload)
        ns_len = len(namespace)
        assert tag[:6] == b"secret"
        assert tag[6:7] == b"_"
        assert tag[7 : 7 + ns_len] == namespace
        assert tag[7 + ns_len : 8 + ns_len] == b"_"
        assert tag[8 + ns_len : 40 + ns_len] == payload
        assert len(tag) == 48 + ns_len


class TestVerify:
    def test_boundary_lengths_rejected(self) -> None:
        assert noisy.verify(b"a" * 48) is False
        assert noisy.verify(b"a" * 50) is False
        assert noisy.verify(b"a" * 81) is False

    def test_empty_rejected(self) -> None:
        assert noisy.verify(b"") is False

    def test_invalid_base37_chars_rejected(self) -> None:
        secret = noisy.create(b"test")
        mutated = bytearray(secret)
        mutated[0] = ord("A")
        assert noisy.verify(bytes(mutated)) is False

    def test_invalid_length_base37_strings(self) -> None:
        assert noisy.verify(b"a" * 49) is False
        assert noisy.verify(b"a" * 80) is False

    def test_truncated_left(self) -> None:
        secret = noisy.create(b"test")
        assert noisy.verify(secret[1:]) is False

    def test_truncated_right(self) -> None:
        secret = noisy.create(b"test")
        assert noisy.verify(secret[:-1]) is False

    def test_valid_no_domain(self) -> None:
        secret = noisy.create()
        assert noisy.verify(secret) is True

    def test_valid_with_domain(self) -> None:
        secret = noisy.create(b"test")
        assert noisy.verify(secret) is True

    def test_wrong_prefix(self) -> None:
        secret = noisy.create(b"test")
        mutated = b"secrat" + secret[6:]
        assert noisy.verify(mutated) is False
