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

import io
import pathlib
from collections.abc import Iterator
from typing import Final

import openpgp.armor
import openpgp.composed

# A certificate whose primary key remains valid long after its signing subkey has lapsed, which is
# what routine subkey rotation looks like. The signature below was made while the subkey was still
# valid, so only a check which reads the subkey's own binding will reject it
EXPIRED_SUBKEY_PRIMARY_FINGERPRINT: Final[str] = "3827511743c93b90e228de4dfbabaaeb4cee5278"
EXPIRED_SUBKEY_SIGNING_FINGERPRINT: Final[str] = "b19a4986de24d30074181ccd2aa349018639fa3b"
EXPIRED_SUBKEY_ENCRYPTION_FINGERPRINT: Final[str] = "bf1b75c74f388427fb45197296ab43a5385c02c7"

# The primary certifies until 2029-12-29, the signing subkey lapsed on 2020-12-31
EXPIRED_SUBKEY_PRIMARY_EXPIRES_YEAR: Final[int] = 2029
EXPIRED_SUBKEY_SIGNING_EXPIRES_YEAR: Final[int] = 2020

EXPIRED_SUBKEY_ARTIFACT_BYTES: Final[bytes] = b"atr subkey expiry fixture\n"

EXPIRED_SUBKEY_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEXgvhABYJKwYBBAHaRw8BAQdASh0vR04NP4YtesJXVieTK+z4mwnLoQ7LvUNU
YSabtVC0RkFUUiBTdWJrZXkgRXhwaXJ5IChGb3IgdGVzdCB1c2Ugb25seSkgPGFw
YWNoZS10b29saW5nQGV4YW1wbGUuaW52YWxpZD6ImQQTFgoAQRYhBDgnURdDyTuQ
4ijeTfurqutM7lJ4BQJeC+EAAhsBBQkSzAMABQsJCAcCAiICBhUKCQgLAgQWAgMB
Ah4HAheAAAoJEPurqutM7lJ4TX4BAM2/SBTVhU+ZAsDYxjhqcFmNsBKsRJj0n/tB
KdCyAs6iAQCft7wvuZ2n8sZRaxskFXz1puxeZVoJq3K2nsgrpGg5BrgzBF4L4QAW
CSsGAQQB2kcPAQEHQN2C8ayhhXa6PXNjxRvy0JL0slljiJ92pDfN1UqI10r1iPUE
GBYKACYWIQQ4J1EXQ8k7kOIo3k37q6rrTO5SeAUCXgvhAAIbAgUJAeEzgACBCRD7
q6rrTO5SeHYgBBkWCgAdFiEEsZpJht4k0wB0GBzNKqNJAYY5+jsFAl4L4QAACgkQ
KqNJAYY5+jtJPAD+MCvVHkJL4wKOGifYn6c8++GwLGc/SU68Iyl8h8FePXAA/j3J
bdPHDY7UYKKygscoIfVyu89p2mZYGzuPj59oSTYKgJEBAIWPk3NmiL2AEkmg1TCT
2pJRQLrh8A09ZuQbmnIlIm1mAP9X7VCnbn75CuN/3tInVaww8rZKux9tPRilBD6q
35pqCrg4BF4L4QASCisGAQQBl1UBBQEBB0B2yd/KKNJHFVeqzbQatVO71wQuEeFT
Bai2z5mGeMICQgMBCAeIfgQYFgoAJhYhBDgnURdDyTuQ4ijeTfurqutM7lJ4BQJe
C+EAAhsMBQkSzAMAAAoJEPurqutM7lJ41bUBAN+DTVAVshey47UmuaI2qETfAB70
+jR/P78VNOgo9UEZAPsHhM6goPebQU2U2EdxNv8Y6vAwLY7CehuCZlkcs2cJBQ==
=Bum7
-----END PGP PUBLIC KEY BLOCK-----
"""

EXPIRED_SUBKEY_DETACHED_SIGNATURE_ASC: Final[str] = """-----BEGIN PGP SIGNATURE-----

iHUEABYKAB0WIQSxmkmG3iTTAHQYHM0qo0kBhjn6OwUCXtRFAAAKCRAqo0kBhjn6
O+B8AQCdBRwro11q4cVGL8lQYnJGjbPMdIlj9f/ZpcKQY1cUlgEAme5PWHz3+oiU
MlyrMMYPGgnWrfquh4sgGRHbuAMzrgA=
=IPLu
-----END PGP SIGNATURE-----
"""


REVOKED_PRIMARY_UID_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEal57BBYJKwYBBAHaRw8BAQdATc1jLyFqpUYCaoqbC8xo0ndcHd3mQIyIkpz4
G1Z/eYu0HVByaW1lIDxwcmltZUBleGFtcGxlLmludmFsaWQ+iHgEMBYIACAWIQTn
G1wcvBNWtkSXTWqxxyKS7Yt3CgUCal57BgIdIAAKCRCxxyKS7Yt3CmygAQDd1OWS
+om2RO/vkxCTL5p2sGDAYK5SX5F4TKt1Q1ca0wD+JWmwD5CqjtCGwzDMnZYVJYCB
jWEWzbmVbrsGl/5oRAyIkwQTFggAOwIbAwULCQgHAgYVCgkICwIEFgIDAQIeAQIX
gBYhBOcbXBy8E1a2RJdNarHHIpLti3cKBQJqXnsFAhkBAAoJELHHIpLti3cKf+EA
/0kMjkuSmzNSvgWE/TR/Ofxip0G6Zn6ma7ZCHkTgDbA/AQCpBShMqnHW1B+68k+u
vd432O4O+3Y7YGn9gGfXTrhWBLQfU2Vjb25kIDxzZWNvbmRAZXhhbXBsZS5pbnZh
bGlkPoiQBBMWCAA4FiEE5xtcHLwTVrZEl01qscciku2LdwoFAmpeewQCGwMFCwkI
BwIGFQoJCAsCBBYCAwECHgECF4AACgkQscciku2LdwqXSgD/ayqude6JleREnPKN
KQ4uaPG1F1byPNSYr2TnRpHxiE8BAI52jDFu7urx7YMpiee6HpmD/UDwWd51bf3W
D67F/4IM
=UwRL
-----END PGP PUBLIC KEY BLOCK-----
"""

REVOKED_UID_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEal52nhYJKwYBBAHaRw8BAQdAkkyLnkBjDoWMkWAOZgW/ggtUPB5EYQcFOsnA
hHDuvGG0HUFsaWNlIDxhbGljZUBleGFtcGxlLmludmFsaWQ+iHgEMBYIACAWIQR2
OPrERA8xZafQBte7NIWZyART/QUCal52nwIdIAAKCRC7NIWZyART/YFHAQC44c6r
2aMZVwqthVm3zBPCqFu4x6vuTRwdIHJsgktlPAEArJLY/oIQLZ3w7/eHDSnIZka/
5c5BZ6IK/m3erALy4QCIkAQTFggAOBYhBHY4+sREDzFlp9AG17s0hZnIBFP9BQJq
XnaeAhsDBQsJCAcCBhUKCQgLAgQWAgMBAh4BAheAAAoJELs0hZnIBFP9CO4A/1/P
nK9TnA3eGquBdRYflY4rNsCw3lIeuaM728sGnk1bAQCq4pHwP6j9j2g7q6QI8fgh
jIB8bZ1m4qJfQr312ZqOBIh0BBEWCAAdFiEEtyjIg/grSedLSOYkjDieBWwwo7cF
Ampedp4ACgkQjDieBWwwo7cDzwEA47ND7Ze141QwMlk1+SJuS+3KLn4u7W/5hvZZ
W36enIYA+K/4w4pkDXDdfNX0wxeXdOH3GvgcUzw5UnGAyPLPDwC0IkFsaWNlIFR3
byA8YWxpY2UyQGV4YW1wbGUuaW52YWxpZD6IkAQTFggAOBYhBHY4+sREDzFlp9AG
17s0hZnIBFP9BQJqXnaeAhsDBQsJCAcCBhUKCQgLAgQWAgMBAh4BAheAAAoJELs0
hZnIBFP9OA8A/1t15vvaSJjtPZEKGFT7POk+2Z5Q2krVupn6V9oW4oDpAQDig6j9
6IrSPtodizFMPPzDPNp6KPOaSiuA6zZshWZEDQ==
=8ozG
-----END PGP PUBLIC KEY BLOCK-----
"""


def block_without_signature_type(block: str, signature_type: int) -> str:
    # Re-armour a certificate with every self-signature of a given type removed, which is what a
    # stripped re-upload looks like at the packet level
    key, _ = openpgp.composed.SignedPublicKey.from_armor(block)
    data = key.to_bytes()
    kept = bytearray()
    for tag, start, end, body in packets(data):
        if not ((tag == 2) and (data[body] == 4) and (data[body + 1] == signature_type)):
            kept += data[start:end]
    stripped = openpgp.composed.SignedPublicKey.from_bytes(bytes(kept))
    return stripped.to_armored()


def packets(data: bytes) -> Iterator[tuple[int, int, int, int]]:
    # Walk an OpenPGP byte stream, yielding (tag, start, end, body) per packet so a test can rewrite a
    # certificate at the packet level. Covers both old and new-format framing
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
        yield tag, start, index, body


def two_certificate_block(first_armored: str, second_armored: str) -> str:
    first, _ = openpgp.composed.SignedPublicKey.from_armor(first_armored)
    second, _ = openpgp.composed.SignedPublicKey.from_armor(second_armored)
    buffer = io.BytesIO()
    openpgp.armor.write(first.to_bytes() + second.to_bytes(), openpgp.armor.BlockType.PublicKey, buffer)
    return buffer.getvalue().decode()


def write_expired_subkey_fixture(tmp_path: pathlib.Path) -> tuple[str, str]:
    artifact_path = tmp_path / "apache-test-1.0.txt"
    signature_path = tmp_path / "apache-test-1.0.txt.asc"
    artifact_path.write_bytes(EXPIRED_SUBKEY_ARTIFACT_BYTES)
    signature_path.write_text(EXPIRED_SUBKEY_DETACHED_SIGNATURE_ASC, encoding="utf-8")
    return str(signature_path), str(artifact_path)


# A certificate whose signing subkey was revoked after it had signed. The signature below was made
# while the subkey was still good, so only a check which reads the revocation will reject it
REVOKED_SUBKEY_PRIMARY_FINGERPRINT: Final[str] = "627e587f1f3f8cd99cc8c6781e02faf30b9b2d9d"
REVOKED_SUBKEY_SIGNING_FINGERPRINT: Final[str] = "5b024ae4e7690e808c1c3a18c13114edca77d92f"

REVOKED_SUBKEY_ARTIFACT_BYTES: Final[bytes] = b"atr revocation fixture\n"

REVOKED_SUBKEY_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEalpBFhYJKwYBBAHaRw8BAQdAjDlZ6Lr4uf3ylu8ozNd5ZeyG9dPPeG/vHH6l
QVRSW+q0R0FUUiBSZXZva2VkIFN1YmtleSAoRm9yIHRlc3QgdXNlIG9ubHkpIDxy
ZXZva2VkLXN1YmtleUBleGFtcGxlLmludmFsaWQ+iJMEExYKADsWIQRiflh/Hz+M
2ZzIxngeAvrzC5stnQUCalpBFgIbAQULCQgHAgIiAgYVCgkICwIEFgIDAQIeBwIX
gAAKCRAeAvrzC5stnfdAAQCDqDG6MbixdM9Vxsouxg3IeM3xSPH00uv8zs7ds9je
NgD+OuBJ81xTQF2Nud+aZO9QPbr8JVRebjDi517pMiE+kA64MwRqWkEWFgkrBgEE
AdpHDwEBB0DYrVxiE6+rIZt5WhehU+Y1ZgjSUmS4LUjQBeBxcs78u4h4BCgWCgAg
FiEEYn5Yfx8/jNmcyMZ4HgL68wubLZ0FAmpaQRYCHQIACgkQHgL68wubLZ21oQEA
2POrR1Ff0xJik+0M17V8nu8zCagAauhiM9S7nKD/5bgBAI/pD0d08UMLGqDFzIO7
GlxonRgJsv+LcRh0Mw5yyVUEiO8EGBYKACAWIQRiflh/Hz+M2ZzIxngeAvrzC5st
nQUCalpBFgIbAgCBCRAeAvrzC5stnXYgBBkWCgAdFiEEWwJK5OdpDoCMHDoYwTEU
7cp32S8FAmpaQRYACgkQwTEU7cp32S8PsQD+P1bArcgoslNBVjKS1WvxmO7LQv+1
/xymuXAvvVyIARsA/2bv6hKQMYhqrGERZ7ptUh9z6x15tgLGiS3UMr5FSW0LepYB
AJOv44nvZhSS7FEFKEVUDA8wFXMkN8b0vONjttnOSVqQAP9aONCFDVFZHDwxhXe2
Ai4vJkztXwDtM9aamALxT+OrDw==
=2c/y
-----END PGP PUBLIC KEY BLOCK-----
"""

REVOKED_SUBKEY_DETACHED_SIGNATURE_ASC: Final[str] = """-----BEGIN PGP SIGNATURE-----

iHUEABYKAB0WIQRbAkrk52kOgIwcOhjBMRTtynfZLwUCalpBFgAKCRDBMRTtynfZ
L96xAP9O/POM9QlwStFscv/Ylj2AcGmnRIipcdYtG63grTTgWwD8C0d+wPULT/oY
9gkQVkwcs5X4x8x+qKKvLLQHAgX3jgg=
=wOC7
-----END PGP SIGNATURE-----
"""


# A certificate whose primary key is revoked. Revoking the primary revokes everything beneath it, so
# its still-bound signing subkey is unusable too
REVOKED_PRIMARY_FINGERPRINT: Final[str] = "894e4b57dca9772b6b692b6a1d725675acbdb724"
REVOKED_PRIMARY_SIGNING_FINGERPRINT: Final[str] = "d52d750d3e80bede5553b2edaf0a1d93538cd91e"

REVOKED_PRIMARY_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEalpBFhYJKwYBBAHaRw8BAQdAKUdQxIFXyT1Nn153YCA/poF2sSjombcngmqF
0z8FAxyIeAQgFgoAIBYhBIlOS1fcqXcra2krah1yVnWsvbckBQJqWkEWAh0AAAoJ
EB1yVnWsvbck1/sBAJPxFkfg+Cz8+RLXieMId3W7z1ySbAY0ZXdW0+zIXTs6AP0V
lbtW9cFIu8fpMPQxw7ghJAiT/NcLewQTSdc1QfZ8DbRJQVRSIFJldm9rZWQgUHJp
bWFyeSAoRm9yIHRlc3QgdXNlIG9ubHkpIDxyZXZva2VkLXByaW1hcnlAZXhhbXBs
ZS5pbnZhbGlkPoiTBBMWCgA7FiEEiU5LV9ypdytraStqHXJWday9tyQFAmpaQRYC
GwEFCwkIBwICIgIGFQoJCAsCBBYCAwECHgcCF4AACgkQHXJWday9tyRzUAEA8dxQ
eXeLraUvbKmTwArK4EBez/kfb/iAw3SjhcNzlQgBAK7rYpI/2O7lCEJ/Ibhom9cI
CcY0CEWd4CB148F+bEQMuDMEalpBFhYJKwYBBAHaRw8BAQdAWTKqGwULwFLUhP2f
plzat/55UCABJStYrUeeCwbbXYGI7wQYFgoAIBYhBIlOS1fcqXcra2krah1yVnWs
vbckBQJqWkEWAhsCAIEJEB1yVnWsvbckdiAEGRYKAB0WIQTVLXUNPoC+3lVTsu2v
Ch2TU4zZHgUCalpBFgAKCRCvCh2TU4zZHovHAP46C9cmUu4ep4HuMhsdALUH7V2F
i0zZxlh06TsoCyjqegD/Wc+lxUjExmc6Nwth8iib5BqB8NUxYNO42AHQRW1y6Aq6
9wD+NEFQrTDnM7165KMXcprJwmixQAS8sVKsTuuQRo642hABAOoifUNE9r4/6Bzm
ixdipMf9A5HgGyWO0mNWIGk+ZRIH
=Sivq
-----END PGP PUBLIC KEY BLOCK-----
"""


# A certificate with a revoked secondary user id. The key itself stays entirely valid: reading the
# uid revocation as a key-level self-signature would erase its expiry and its declared capabilities
ALL_UIDS_REVOKED_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

xjMEXgvhABYJKwYBBAHaRw8BAQdAmbGDg3AS7oD3hJHRMiqyl66UyEtAAhkFDuZR
+I8CY/7NPUFUUiBSZWNlcnQgVHdvIChGb3IgdGVzdCB1c2Ugb25seSkgPHJlY2Vy
dC0yQGV4YW1wbGUuaW52YWxpZD7CkwQTFgoAOxYhBLbgBR68+9U0Pk2D37Vu607g
vPwlBQJeC+EAAhsBBQsJCAcCAiICBhUKCQgLAgQWAgMBAh4HAheAAAoJELVu607g
vPwlep4A/1efZVjLbaTYw33bfHVAc1XRiF/I2WD7jjZNmT3D42HEAP4ulBMFnq0+
SBRtf05r+PFN6qo64l4ssyR22SqeAD6zBsJ4BDAWCgAgFiEEtuAFHrz71TQ+TYPf
tW7rTuC8/CUFAl/uZgACHSAACgkQtW7rTuC8/CXjJwEAooBNfv0rpklQL0Qg3GGL
bAULW8LDtveiOg6rh5o7gEoA/R3f6oCT9wbJKlmq4xal8bc9SSpqtoVlcW6iPONl
gn0O
-----END PGP PUBLIC KEY BLOCK-----
"""

RECERTIFIED_UID_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

xjMEXgvhABYJKwYBBAHaRw8BAQdAmbGDg3AS7oD3hJHRMiqyl66UyEtAAhkFDuZR
+I8CY/7NPUFUUiBSZWNlcnQgT25lIChGb3IgdGVzdCB1c2Ugb25seSkgPHJlY2Vy
dC0xQGV4YW1wbGUuaW52YWxpZD7CkwQTFgoAOxYhBLbgBR68+9U0Pk2D37Vu607g
vPwlBQJeC+EAAhsBBQsJCAcCAiICBhUKCQgLAgQWAgMBAh4HAheAAAoJELVu607g
vPwlh04BAODwhPX8utN5+pKDDt5QYES3hzPdY9c63Qsu3K6F+yr9AQDPVFCOwrXW
yos8KFwpfcCKdaR2Smbg4KK3Be65Qf23Cs09QVRSIFJlY2VydCBUd28gKEZvciB0
ZXN0IHVzZSBvbmx5KSA8cmVjZXJ0LTJAZXhhbXBsZS5pbnZhbGlkPsKTBBMWCgA7
FiEEtuAFHrz71TQ+TYPftW7rTuC8/CUFAl4L4QACGwEFCwkIBwICIgIGFQoJCAsC
BBYCAwECHgcCF4AACgkQtW7rTuC8/CV6ngD/V59lWMttpNjDfdt8dUBzVdGIX8jZ
YPuONk2ZPcPjYcQA/i6UEwWerT5IFG1/Tmv48U3qqjriXiyzJHbZKp4APrMGwngE
MBYKACAWIQS24AUevPvVND5Ng9+1butO4Lz8JQUCX+5mAAIdIAAKCRC1butO4Lz8
JeMnAQCigE1+/SumSVAvRCDcYYtsBQtbwsO296I6DquHmjuASgD9Hd/qgJP3Bskq
WarjFqXxtz1JKmq2hWVxbqI842WCfQ7CkwQTFgoAOxYhBLbgBR68+9U0Pk2D37Vu
607gvPwlBQJhz5mAAhsBBQsJCAcCAiICBhUKCQgLAgQWAgMBAh4HAheAAAoJELVu
607gvPwlz7wA/R5oMWr85w1+aPq/2lWgMX8gPjx+0uugqSzlTFpMGf3/AQDzgPjC
v/kThixelh2qISyZgqlnxyHzf8K8nAvHHSobBA==
-----END PGP PUBLIC KEY BLOCK-----
"""

LOCAL_CERTIFICATION_FINGERPRINT: Final[str] = "34a7704ef4a3760d556e53867362b716c7acd9f6"

LOCAL_CERTIFICATION_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEaoccdBYJKwYBBAHaRw8BAQdAz8SrbRVLTsholjimiU4SQeEeSq4jSsu8wrEh
QaKTQWC0REFUUiBMb2NhbCBTaWcgVGFyZ2V0IChGb3IgdGVzdCB1c2Ugb25seSkg
PGxvY2FsLXNpZ0BleGFtcGxlLmludmFsaWQ+iJMEExYKADsWIQQ0p3BO9KN2DVVu
U4ZzYrcWx6zZ9gUCaoccdAIbAQULCQgHAgIiAgYVCgkICwIEFgIDAQIeBwIXgAAK
CRBzYrcWx6zZ9i9sAP48Z7BlHvq5g516KBkXnl21I+7hcRUdGZaVn8fJYTiZpQEA
8tRs+szgi5RcQVSuw5/N0f0KwM3FGC+GB9XHT+SeiwOIeAQQFgoAIBYhBCv+BPRX
tnN+CLbd4I99cc259PeTBQJqhxx1AgQAAAoJEI99cc259PeT57cA/1tWd7RyPVyY
Vx2MB/LkcbDoOUE+qqmlawoFvK2d3kxRAQDvfCK3GyFsgaU1IBrSPCIXACV8m8he
JxoCla67csUuAA==
=SanX
-----END PGP PUBLIC KEY BLOCK-----
"""

REVOKED_UID_FINGERPRINT: Final[str] = "346c2e8cd37b4f7c0bde03d9fdefe91a648ddd76"
REVOKED_UID_SIGNING_FINGERPRINT: Final[str] = "5aec39f36f29b0d02ac67eb6dd6e33ceb5d275a6"
REVOKED_UID_PRIMARY_EXPIRES_YEAR: Final[int] = 2030

REVOKED_UID_PUBLIC_KEY_ASC: Final[str] = """-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEalpBFhYJKwYBBAHaRw8BAQdAo/CQbE40pWaoJHDJ+zqNttN6+Op4Nb/7cFFw
xev24XO0QUFUUiBSZXZva2VkIFVpZCAoRm9yIHRlc3QgdXNlIG9ubHkpIDxyZXZv
a2VkLXVpZEBleGFtcGxlLmludmFsaWQ+iJkEExYKAEEWIQQ0bC6M03tPfAveA9n9
7+kaZI3ddgUCalpBFgIbAQUJCGIiKgULCQgHAgIiAgYVCgkICwIEFgIDAQIeBwIX
gAAKCRD97+kaZI3ddoKJAP0Qj4S7sl729rTc0UxbGeQur/GyuY/38H9LOCZUqsQ3
UQEA+fneJoJtrT/TCCe9F+3xKDGLRrFzXbM5j6jwRTaILgK0NkFUUiBSZXZva2Vk
IFVpZCBTZWNvbmQgPHJldm9rZWQtdWlkLTJAZXhhbXBsZS5pbnZhbGlkPoh4BDAW
CgAgFiEENGwujNN7T3wL3gPZ/e/pGmSN3XYFAmpaQRcCHSAACgkQ/e/pGmSN3XYT
EQD/UjqNftLvxzE8ah+nHWZZ3SRTSCcdZC6B/Hak4dd92gkA/3HPMo3BNSky+mml
KSFbjOCembdIL9cWhsk/60T70ZADiJkEExYKAEEWIQQ0bC6M03tPfAveA9n97+ka
ZI3ddgUCalpBFgIbAQUJCGIiKgULCQgHAgIiAgYVCgkICwIEFgIDAQIeBwIXgAAK
CRD97+kaZI3ddv1AAP9nXCJtCPupIhiFj8MJmf5zs1iB8mvB8EExA62MEHFLMgD/
Rfj3KhAGm8D6+e+T11dtP4RSeAev6OA8smDj1WBldQO4MwRqWkEWFgkrBgEEAdpH
DwEBB0DfkeUI+ub7ARbR7L1mpf7IWypAnCO5dOzjcnJ6dwlSQ4jvBBgWCgAgFiEE
NGwujNN7T3wL3gPZ/e/pGmSN3XYFAmpaQRYCGwIAgQkQ/e/pGmSN3XZ2IAQZFgoA
HRYhBFrsOfNvKbDQKsZ+tt1uM8610nWmBQJqWkEWAAoJEN1uM8610nWmYHwA/1ng
VdOTntQA+bbo+vHhqEGdpDvw/a1r6M2lxtBT87+2AQC1UhAkMxN0vdAZczqlBMFC
i05PRe7RtkmV0875EUYpAz1fAP4yeSCHoNNOtOZKj3+uZuUeoDFyC6afH/LAmU+w
rkJ3LQEA8l4Luk7tZdHZyq+w5q+hbbkRz4X5RJh3091rYGqBtAQ=
=P9R1
-----END PGP PUBLIC KEY BLOCK-----
"""


def write_revoked_subkey_fixture(tmp_path: pathlib.Path) -> tuple[str, str]:
    artifact_path = tmp_path / "apache-test-1.0.txt"
    signature_path = tmp_path / "apache-test-1.0.txt.asc"
    artifact_path.write_bytes(REVOKED_SUBKEY_ARTIFACT_BYTES)
    signature_path.write_text(REVOKED_SUBKEY_DETACHED_SIGNATURE_ASC, encoding="utf-8")
    return str(signature_path), str(artifact_path)
