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

import base64
import pathlib

import openpgp
import pytest

import atr.models.safe as safe
import atr.models.sql as sql
import atr.tasks.checks as checks
import atr.tasks.checks.signature as signature_check
import tests.unit.recorders as recorders


class SignatureRecorderStub(recorders.RecorderStub):
    def __init__(self, primary_path: pathlib.Path, base_dir: pathlib.Path, checker: str) -> None:
        super().__init__(safe.StatePath(primary_path), checker)
        self._base_dir = base_dir

    async def abs_path(self, rel_path: str | None = None) -> safe.StatePath | None:
        if rel_path is None:
            return self._path
        return safe.StatePath(self._base_dir / rel_path)


_PRIMARY_FINGERPRINT = "557f8d855def8bbe2dc5603b64c271bb87b7fe7b"
_SIGNING_SUBKEY_ID = "a1f5f85d9baea612"

_EMBEDDED_PUBLIC_KEY_ASC = """-----BEGIN PGP PUBLIC KEY BLOCK-----
Comment: 557F 8D85 5DEF 8BBE 2DC5  603B 64C2 71BB 87B7 FE7B
Comment: Apache Tooling (For test use only) <apache-tooling@exam

xjMEaVvlZBYJKwYBBAHaRw8BAQdAZdQYnph1gHloUcpR2mZzwhL6k0HT+QDBqM/H
vyjZzyvCwBEEHxYKAIMFgmlb5WQFiQWkj70DCwkHCRBkwnG7h7f+e0cUAAAAAAAe
ACBzYWx0QG5vdGF0aW9ucy5zZXF1b2lhLXBncC5vcmdoq3iHXnuFOOLJfwpeu4XT
VX4msduw7YD3giI9Lf+dygMVCggCm4ECHgkWIQRVf42FXe+Lvi3FYDtkwnG7h7f+
ewAAplMA+gNORg2Xc8WGcltomGH3KM+9scvjELz4R2ybQ+AcSBU9AP0fPXYWetgU
A7DWRJoM8xL2qwbcX/yTTWEOEhnm374dDs1DQXBhY2hlIFRvb2xpbmcgKEZvciB0
ZXN0IHVzZSBvbmx5KSA8YXBhY2hlLXRvb2xpbmdAZXhhbXBsZS5pbnZhbGlkPsLA
FAQTFgoAhgWCaVvlZAWJBaSPvQMLCQcJEGTCcbuHt/57RxQAAAAAAB4AIHNhbHRA
bm90YXRpb25zLnNlcXVvaWEtcGdwLm9yZ13YdB0XL3hrUouaLDHSmDSR+u2RUONT
MnA9JMbpu7G3AxUKCAKZAQKbgQIeCRYhBFV/jYVd74u+LcVgO2TCcbuHt/57AADX
/wEAnV7b6coWlYX5qacWs3RZndBpIik0/7EfXolzOlE6mtsBAKwTWtHuo4H/qDLm
Gz61bJpTEnjtJIMJ+QgXMpj+QfsJzjMEaVvlZBYJKwYBBAHaRw8BAQdAI94zBjdn
L+Q8MpAY9HplBoYrVxd1Zf3dnw4LPFMmOirCwMUEGBYKATcFgmlb5WQFiQWkj70J
EGTCcbuHt/57RxQAAAAAAB4AIHNhbHRAbm90YXRpb25zLnNlcXVvaWEtcGdwLm9y
Z575eSorjzm2UstnDzHuJdkdDGp5HdUpz5RPmyeQj/CmApuCvqAEGRYKAG8Fgmlb
5WQJEKH1+F2brqYSRxQAAAAAAB4AIHNhbHRAbm90YXRpb25zLnNlcXVvaWEtcGdw
Lm9yZ0NOwQpHaiAxRI4QJN8XOHCqPSMENuyzuBiSzIKlZMf9FiEElA9rZFYELdlZ
UocIofX4XZuuphIAAH7UAQCsOQplKWu2kExRj47mCt+vOaBUfQa9OJJHi88wViq3
lQD/VkgsQ0EphsWq/a28d0Qy86DGbY9FcWOnBFQVah/KRwAWIQRVf42FXe+Lvi3F
YDtkwnG7h7f+ewAAaFQA/0j09N78B9/kSMs9Fb27KPyExjKWKigr15tzYFJlaqPg
AQDYH3DAnIb5fpD2zB6kJ7a3ylrK3mHunThrNjc4W4nLDM4zBGlb5WQWCSsGAQQB
2kcPAQEHQFy/vRPUG0EwCmqNrInUI8x3yCtc+R1kvnrE9IBNc+eLwsDFBBgWCgE3
BYJpW+VkBYkFpI+9CRBkwnG7h7f+e0cUAAAAAAAeACBzYWx0QG5vdGF0aW9ucy5z
ZXF1b2lhLXBncC5vcmcY5GkzClw4T3+kUaErDbI8EKTVhdQZ5Rf1ZPfQpmWp6AKb
oL6gBBkWCgBvBYJpW+VkCRCP1wFtWCy38UcUAAAAAAAeACBzYWx0QG5vdGF0aW9u
cy5zZXF1b2lhLXBncC5vcmdMQVQtulNNFpGLEZa1iM0kt+/upQJkVMp4ZDNOuBi6
JxYhBFOyL2Xlm8KYWYThIY/XAW1YLLfxAAAyNgD9Giw5EGHsqbiG6Goj0lyu2U8u
1+iMVHsZS5J0yLBGJt4A/1tfLkVXT6Ee9WkJFUu+SSjEKpiWVWXcI3TAJsP+Bj4H
FiEEVX+NhV3vi74txWA7ZMJxu4e3/nsAAAF4AQC7HS6gLQ2T29EnVrUkAtPIeK2x
vvdWtVoWXRK5PgZyzQD8DeoeQ3y6NHFzBzqSxn8QrBtmBxBxUb4jnznC8xmZ3gvO
OARpW+VkEgorBgEEAZdVAQUBAQdAeFiwmTrBMhU3LWr8jQUmshetnbIU7VXHIlor
b/tHBmIDAQgHwsAGBBgWCgB4BYJpW+VkBYkFpI+9CRBkwnG7h7f+e0cUAAAAAAAe
ACBzYWx0QG5vdGF0aW9ucy5zZXF1b2lhLXBncC5vcmcMAYQv3Yk8H3qHGsKaG7b9
AKme/nZ4eChCkwGM7mb+TQKbjBYhBFV/jYVd74u+LcVgO2TCcbuHt/57AACPIgD7
BNP+DIaSxDSj11rom16KN16bZ3/lWUMAOplGHOWbAa0A/jyf1xOvZMVqvJPo+42a
9Pse1pGqyUGyeT6owz7abyEJ
=XQ9q
-----END PGP PUBLIC KEY BLOCK-----
"""

_EMBEDDED_DETACHED_SIGNATURE_ASC = """-----BEGIN PGP SIGNATURE-----

wr0EABYKAG8Fgmlb5WQJEKH1+F2brqYSRxQAAAAAAB4AIHNhbHRAbm90YXRpb25z
LnNlcXVvaWEtcGdwLm9yZz5EQzdcny9mjUUxatFEOaClOjI9cNqJoF1OJDQx7uAa
FiEElA9rZFYELdlZUocIofX4XZuuphIAADBzAQDECUPchT+jiheztMxLxy9hJYkL
M9eBBQI3WII8MK8yVAEAyRajfs9qExqT1d9Jh2LsQfO6wXAPpca6AUaGIlrhngo=
=ybPh
-----END PGP SIGNATURE-----
"""

_EMBEDDED_ARTIFACT_TAR_GZ_B64 = """
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
9ffHsHs/P8/P8PD9/2Oc/nCsYWABCAAA=
"""


async def test_check_blocks_on_all_verification_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    signature_path = tmp_path / "artifact.tar.gz.asc"
    signature_path.write_text("not a signature", encoding="utf-8")
    recorder = SignatureRecorderStub(signature_path, tmp_path, "atr.tasks.checks.signature.check")

    async def check_core_logic(**kwargs: object) -> dict[str, object]:
        del kwargs
        return {
            "verified": False,
            "error": "Signature was made by an untrusted signing key",
            "error_kind": "untrusted_key",
        }

    monkeypatch.setattr(signature_check, "_check_core_logic", check_core_logic)
    args = checks.FunctionArguments(
        recorder=recorders.get_recorder(recorder),
        asf_uid="tester",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=safe.RelPath(signature_path.name),
        extra_args={"committee_key": "test"},
    )

    await signature_check.check(args)

    assert recorder.messages == [
        (
            sql.CheckResultStatus.BLOCKER.value,
            "Signature was made by an untrusted signing key",
            {
                "verified": False,
                "error": "Signature was made by an untrusted signing key",
                "error_kind": "untrusted_key",
            },
        )
    ]


async def test_check_blocks_on_missing_signature_error_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    signature_path = tmp_path / "artifact.tar.gz.asc"
    signature_path.write_text("not a signature", encoding="utf-8")
    recorder = SignatureRecorderStub(signature_path, tmp_path, "atr.tasks.checks.signature.check")

    async def check_core_logic(**kwargs: object) -> dict[str, object]:
        del kwargs
        return {
            "verified": False,
            "error": "No valid signature found",
            "error_kind": "missing_signature",
        }

    monkeypatch.setattr(signature_check, "_check_core_logic", check_core_logic)
    args = checks.FunctionArguments(
        recorder=recorders.get_recorder(recorder),
        asf_uid="tester",
        project_key=safe.ProjectKey("test"),
        version_key=safe.VersionKey("1.0"),
        revision_number=safe.RevisionNumber("00001"),
        primary_rel_path=safe.RelPath(signature_path.name),
        extra_args={"committee_key": "test"},
    )

    await signature_check.check(args)

    assert recorder.messages == [
        (
            sql.CheckResultStatus.BLOCKER.value,
            "No valid signature found",
            {"verified": False, "error": "No valid signature found", "error_kind": "missing_signature"},
        )
    ]


def test_check_core_logic_verifies_signature_signed_by_signing_subkey(tmp_path: pathlib.Path) -> None:
    signature_path, artifact_path = _write_embedded_signature_fixture(tmp_path)

    result = signature_check._check_core_logic_verify_signature(
        signature_path=signature_path,
        artifact_path=artifact_path,
        ascii_armored_keys=[_EMBEDDED_PUBLIC_KEY_ASC],
        apache_uid_map={_PRIMARY_FINGERPRINT: True},
    )

    assert result["verified"] is True
    assert result["status"] == "Valid signature"
    assert result["key_id"] == _SIGNING_SUBKEY_ID


def test_key_matches_signature_accepts_subkey_issuer_metadata() -> None:
    public_key, _ = openpgp.PublicKey.from_armor(_EMBEDDED_PUBLIC_KEY_ASC)
    signature, _ = openpgp.DetachedSignature.from_armor(_EMBEDDED_DETACHED_SIGNATURE_ASC)
    signature_info = signature.signature_info()
    issuer_fingerprints = {fingerprint.lower() for fingerprint in signature_info.issuer_fingerprints}
    issuer_key_ids = {key_id.lower() for key_id in signature_info.issuer_key_ids}

    assert signature_check._key_matches_signature(public_key, issuer_fingerprints, issuer_key_ids)


def _write_embedded_signature_fixture(tmp_path: pathlib.Path) -> tuple[str, str]:
    artifact_path = tmp_path / "apache-test-0.2.tar.gz"
    signature_path = tmp_path / "apache-test-0.2.tar.gz.asc"
    artifact_path.write_bytes(base64.b64decode(_EMBEDDED_ARTIFACT_TAR_GZ_B64.strip()))
    signature_path.write_text(_EMBEDDED_DETACHED_SIGNATURE_ASC, encoding="utf-8")
    return str(signature_path), str(artifact_path)
