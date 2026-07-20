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

import pathlib
from typing import Final

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


def write_expired_subkey_fixture(tmp_path: pathlib.Path) -> tuple[str, str]:
    artifact_path = tmp_path / "apache-test-1.0.txt"
    signature_path = tmp_path / "apache-test-1.0.txt.asc"
    artifact_path.write_bytes(EXPIRED_SUBKEY_ARTIFACT_BYTES)
    signature_path.write_text(EXPIRED_SUBKEY_DETACHED_SIGNATURE_ASC, encoding="utf-8")
    return str(signature_path), str(artifact_path)
