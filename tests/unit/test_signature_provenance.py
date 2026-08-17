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

import contextlib
import datetime
import hashlib
import inspect
import pathlib
import unittest.mock as mock
from collections.abc import AsyncGenerator, AsyncIterator
from types import SimpleNamespace

import pytest
import sqlalchemy
import sqlalchemy.ext.asyncio
import sqlmodel

import atr.api
import atr.db as db
import atr.models.api as api
import atr.models.safe as safe
import atr.models.sql as sql
import tests.unit.pgp_fixtures as pgp_fixtures

_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC = """-----BEGIN PGP PUBLIC KEY BLOCK-----
Version: GnuPG v2

mDMEV2o9XRYJKwYBBAHaRw8BAQdAZ8zkuQDL9x7rcvvoo6s3iEF1j88Dknd9nZhL
nTEoBRm0G3BhdHJpY2UubHVtdW1iYUBleGFtcGxlLm5ldIh5BBMWCAAhBQJXaj1d
AhsDBQsJCAcCBhUICQoLAgQWAgMBAh4BAheAAAoJEBOVY2gqAg0KmQ0BAMUNzAlT
OzG7tolSI92lhePi5VqutdqTEQTyYYWi1aEsAP0YfiuosNggTc0oRTSz46S3i0Qj
AlpXwfU00888yIreDbg4BFdqPY0SCisGAQQBl1UBBQEBB0AWeeZlz31O4qTmIKr3
CZhlRUXZFxc3YKyoCXyIZBBRawMBCAeIYQQYFggACQUCV2o9jQIbDAAKCRATlWNo
KgINCsuFAP9BplWl813pi779V8OMsRGs/ynyihnOESft/H8qlM8PDQEAqIUPpIty
OX/OBFy2RIlIi7J1bTp9RzcbzQ/4Fk4hWQQ=
=qRfF
-----END PGP PUBLIC KEY BLOCK-----
"""


class MockQuery:
    def __init__(self, value: object) -> None:
        self._value = value

    async def all(self) -> list[object]:
        if isinstance(self._value, list):
            return self._value
        return [self._value] if (self._value is not None) else []

    async def get(self) -> object:
        return self._value


class MockDBSession:
    def __init__(self, projects: dict[str, object], releases: dict[str, list[object]]) -> None:
        self._projects = projects
        self._releases = releases

    async def execute(self, _query: object) -> object:
        return SimpleNamespace(scalars=lambda: [])

    def project(self, **kwargs: object) -> MockQuery:
        key = kwargs.get("key")
        committee_key = kwargs.get("committee_key")
        if key is not None:
            return MockQuery(self._projects.get(str(key)))
        if committee_key is not None:
            matching = [p for p in self._projects.values() if getattr(p, "committee_key", None) == committee_key]
            return MockQuery(matching)
        return MockQuery(None)

    def release(self, **kwargs: object) -> MockQuery:
        project_key = kwargs.get("project_key")
        version = kwargs.get("version")
        releases = self._releases.get(str(project_key), []) if (project_key is not None) else []
        if version is not None:
            match = next((r for r in releases if getattr(r, "version", None) == str(version)), None)
            return MockQuery(match)
        return MockQuery(releases)


class MockKeyDBSession:
    def __init__(self, signing_certificates: list[object], signing_keys: list[object] | None = None) -> None:
        self._signing_certificates = signing_certificates
        self._signing_keys = signing_keys or []

    async def execute(self, _query: object) -> object:
        signing_keys = self._signing_keys
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: signing_keys))

    def signing_certificate(self, **kwargs: object) -> MockQuery:
        fingerprint = kwargs.get("fingerprint")
        if fingerprint is not None:
            match = next(
                (key for key in self._signing_certificates if getattr(key, "fingerprint", None) == str(fingerprint)),
                None,
            )
            return MockQuery(match)
        return MockQuery(self._signing_certificates)


@pytest.fixture
async def sqlite_data() -> AsyncIterator[db.Session]:
    engine = sqlalchemy.ext.asyncio.create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(sqlmodel.SQLModel.metadata.create_all)
    sessionmaker = sqlalchemy.ext.asyncio.async_sessionmaker(bind=engine, class_=db.Session, expire_on_commit=False)
    async with sessionmaker() as data:
        yield data
    await engine.dispose()


def _certificate(fingerprint: str, deleted: datetime.datetime | None = None) -> sql.SigningCertificate:
    return sql.SigningCertificate(
        fingerprint=fingerprint,
        latest_self_signature=None,
        primary_declared_uid="Alice <alice@example.org>",
        secondary_declared_uids=[],
        apache_uid="alice",
        ascii_armored_key="",
        deleted=deleted,
    )


def _signing_key(fingerprint: str, key_id: str, certificate_fingerprint: str) -> sql.SigningKey:
    return sql.SigningKey(
        fingerprint=fingerprint,
        certificate_fingerprint=certificate_fingerprint,
        is_primary=False,
        key_id=key_id,
        algorithm=1,
        length=4096,
        created=datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC),
        can_sign=True,
    )


def test_args_accepts_scoping_fields() -> None:
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("0.0.1"),
    )
    assert str(args.project_key) == "example"
    assert str(args.version_key) == "0.0.1"


def test_args_defaults_scoping_fields_to_none() -> None:
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")
    assert args.project_key is None
    assert args.version_key is None


@pytest.mark.asyncio
async def test_match_release_matches_file_and_hash(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    sig_content = b"fake signature content"
    sig_hash = hashlib.sha3_256(sig_content).hexdigest()

    (tmp_path / "example-0.0.1.tar.gz.asc").write_bytes(sig_content)

    args = _make_args("example-0.0.1.tar.gz.asc", sig_hash)
    assert await atr.api._match_release(release_dir, args) is True


@pytest.mark.asyncio
async def test_match_release_matches_in_subdirectory(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    sig_content = b"nested signature"
    sig_hash = hashlib.sha3_256(sig_content).hexdigest()

    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "example-0.0.1.tar.gz.asc").write_bytes(sig_content)

    args = _make_args("example-0.0.1.tar.gz.asc", sig_hash)
    assert await atr.api._match_release(release_dir, args) is True


@pytest.mark.asyncio
async def test_match_release_no_match_empty_directory(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")
    assert await atr.api._match_release(release_dir, args) is False


@pytest.mark.asyncio
async def test_match_release_no_match_missing_directory(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "nonexistent"
    release_dir = safe.StatePath(missing)
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")
    assert await atr.api._match_release(release_dir, args) is False


@pytest.mark.asyncio
async def test_match_release_no_match_wrong_hash(tmp_path: pathlib.Path) -> None:
    release_dir = safe.StatePath(tmp_path)
    (tmp_path / "example-0.0.1.tar.gz.asc").write_bytes(b"actual content")

    wrong_hash = hashlib.sha3_256(b"different content").hexdigest()
    args = _make_args("example-0.0.1.tar.gz.asc", wrong_hash)
    assert await atr.api._match_release(release_dir, args) is False


@pytest.mark.asyncio
async def test_resolve_signing_key_from_signature_rejects_mismatched_issuer_metadata() -> None:
    parsed_key, _ = atr.api.openpgp.composed.SignedPublicKey.from_armor(_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC)
    subkey = next(iter(parsed_key.public_subkeys))
    stored = SimpleNamespace(
        fingerprint=parsed_key.fingerprint.lower(),
        ascii_armored_key=_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC,
        committees=[],
    )
    db_data = MockKeyDBSession([stored])

    with pytest.raises(atr.api.exceptions.NotFound, match="No matching signing key"):
        await atr.api._resolve_signing_key_from_signature(
            db_data,
            issuer_fingerprints={parsed_key.fingerprint.lower()},
            issuer_key_ids={subkey.key.key_id.lower()},
        )


@pytest.mark.asyncio
async def test_resolve_signing_key_from_signature_prefers_the_signing_key_row() -> None:
    stored = SimpleNamespace(fingerprint="ab" * 20, ascii_armored_key="", committees=[])
    signing_key = SimpleNamespace(fingerprint="cd" * 20, key_id="cd" * 8, certificate_fingerprint="ab" * 20)
    db_data = MockKeyDBSession([stored], [signing_key])

    resolved, signer_fingerprint = await atr.api._resolve_signing_key_from_signature(
        db_data, issuer_fingerprints=set(), issuer_key_ids={"cd" * 8}
    )

    assert resolved is stored
    assert signer_fingerprint == "cd" * 20


@pytest.mark.asyncio
async def test_resolve_signing_key_from_signature_refuses_an_ambiguous_key_id() -> None:
    signing_keys = [
        SimpleNamespace(fingerprint="cd" * 20, key_id="cd" * 8, certificate_fingerprint="ab" * 20),
        SimpleNamespace(fingerprint="ef" * 20, key_id="cd" * 8, certificate_fingerprint="12" * 20),
    ]
    db_data = MockKeyDBSession([], signing_keys)

    with pytest.raises(atr.api.exceptions.Conflict, match="more than one signing key"):
        await atr.api._resolve_signing_key_from_signature(db_data, issuer_fingerprints=set(), issuer_key_ids={"cd" * 8})


@pytest.mark.asyncio
async def test_resolve_signing_key_from_signature_reads_the_row_certificate_from_a_two_certificate_block() -> None:
    parsed_key, _ = atr.api.openpgp.composed.SignedPublicKey.from_armor(_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC)
    subkey = next(iter(parsed_key.public_subkeys))
    block = pgp_fixtures.two_certificate_block(
        pgp_fixtures.EXPIRED_SUBKEY_PUBLIC_KEY_ASC, _EMBEDDED_SUBKEY_PUBLIC_KEY_ASC
    )
    stored = SimpleNamespace(fingerprint=parsed_key.fingerprint.lower(), ascii_armored_key=block, committees=[])
    db_data = MockKeyDBSession([stored])

    resolved, signer_fingerprint = await atr.api._resolve_signing_key_from_signature(
        db_data, issuer_fingerprints=set(), issuer_key_ids={subkey.key.key_id.lower()}
    )

    assert resolved is stored
    assert signer_fingerprint == subkey.key.fingerprint.lower()


@pytest.mark.asyncio
async def test_resolve_signing_key_from_signature_returns_subkey_fingerprint() -> None:
    parsed_key, _ = atr.api.openpgp.composed.SignedPublicKey.from_armor(_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC)
    subkey = next(iter(parsed_key.public_subkeys))
    stored = SimpleNamespace(
        fingerprint=parsed_key.fingerprint.lower(),
        ascii_armored_key=_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC,
        committees=[],
    )
    db_data = MockKeyDBSession([stored])

    resolved, signer_fingerprint = await atr.api._resolve_signing_key_from_signature(
        db_data,
        issuer_fingerprints=set(),
        issuer_key_ids={subkey.key.key_id.lower()},
    )

    assert resolved is stored
    assert signer_fingerprint == subkey.key.fingerprint.lower()


@pytest.mark.asyncio
async def test_signing_key_for_issuer_matches_one_active_row_on_both_predicates(sqlite_data: db.Session) -> None:
    key_id = "cd" * 8
    sqlite_data.add_all(
        [
            _certificate("aa" * 20),
            _certificate("bb" * 20, deleted=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)),
            _certificate("cc" * 20),
            _signing_key("11" * 20, key_id, "aa" * 20),
            _signing_key("22" * 20, key_id, "bb" * 20),
            _signing_key("33" * 20, key_id, "cc" * 20),
        ]
    )
    await sqlite_data.commit()

    matched = await atr.api._signing_key_for_issuer(sqlite_data, {"11" * 20}, {key_id})
    assert (matched is not None) and (matched.fingerprint == "11" * 20)
    assert await atr.api._signing_key_for_issuer(sqlite_data, {"22" * 20}, {key_id}) is None
    assert await atr.api._signing_key_for_issuer(sqlite_data, {"11" * 20}, {"ee" * 8}) is None
    with pytest.raises(atr.api.exceptions.Conflict):
        await atr.api._signing_key_for_issuer(sqlite_data, set(), {key_id})


@pytest.mark.asyncio
async def test_scoped_finds_committee_with_project_and_version(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
        is_embargoed=False,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("0.0.1"),
    )

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=True)),
    ):
        result = await atr.api._match_committees_scoped([committee], args, "tester")

    assert len(result) == 1
    assert result[0].key == "example-pmc"


@pytest.mark.asyncio
async def test_scoped_finds_committee_with_project_only(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
        is_embargoed=False,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
    )

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=True)),
    ):
        result = await atr.api._match_committees_scoped([committee], args, "tester")

    assert len(result) == 1
    assert result[0].key == "example-pmc"


@pytest.mark.asyncio
async def test_scoped_returns_empty_when_committee_not_linked() -> None:
    unlinked_committee = SimpleNamespace(key="other-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    mock_data = MockDBSession(
        projects={"example": project},
        releases={},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
    )

    with mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)):
        result = await atr.api._match_committees_scoped([unlinked_committee], args, "tester")

    assert result == []


@pytest.mark.asyncio
async def test_scoped_returns_empty_when_project_not_found() -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    mock_data = MockDBSession(projects={}, releases={})
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("nonexistent"),
    )

    with mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)):
        result = await atr.api._match_committees_scoped([committee], args, "tester")

    assert result == []


@pytest.mark.asyncio
async def test_scoped_returns_empty_when_version_not_found() -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": []},
    )
    args = _make_args(
        "example-0.0.1.tar.gz.asc",
        "abc123",
        project_key=safe.ProjectKey("example"),
        version_key=safe.VersionKey("9.9.9"),
    )

    with mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)):
        result = await atr.api._match_committees_scoped([committee], args, "tester")

    assert result == []


@pytest.mark.asyncio
async def test_signature_provenance_requires_matched_committee(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(atr.api.exceptions.NotFound, match="No signing keys found"):
        await _call_signature_provenance(monkeypatch, matched=False)


@pytest.mark.asyncio
async def test_signature_provenance_returns_database_committees(monkeypatch: pytest.MonkeyPatch) -> None:
    result, status = await _call_signature_provenance(monkeypatch, matched=True)

    assert status == 200
    assert result["committees_with_artifact"] == [{"committee": "example"}]
    assert result["key_asc_text"] == _EMBEDDED_SUBKEY_PUBLIC_KEY_ASC


@pytest.mark.asyncio
async def test_unscoped_finds_matching_committee(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
        is_embargoed=False,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=True)),
    ):
        result = await atr.api._match_committees([committee], args, "tester")

    assert len(result) == 1
    assert result[0].key == "example-pmc"


@pytest.mark.asyncio
async def test_unscoped_returns_empty_when_no_match(tmp_path: pathlib.Path) -> None:
    committee = SimpleNamespace(key="example-pmc", is_podling=False)
    project = SimpleNamespace(key="example", committee_key="example-pmc")
    release = SimpleNamespace(
        phase=sql.ReleasePhase.RELEASE,
        project=project,
        version="0.0.1",
        latest_revision_number=None,
        is_embargoed=False,
    )
    release_dir = safe.StatePath(tmp_path)
    mock_data = MockDBSession(
        projects={"example": project},
        releases={"example": [release]},
    )
    args = _make_args("example-0.0.1.tar.gz.asc", "abc123")

    with (
        mock.patch.object(atr.api.db, "session", new=_mock_session_factory(mock_data)),
        mock.patch.object(atr.api.paths, "release_directory", return_value=release_dir),
        mock.patch.object(atr.api, "_match_release", new=mock.AsyncMock(return_value=False)),
    ):
        result = await atr.api._match_committees([committee], args, "tester")

    assert result == []


async def _call_signature_provenance(
    monkeypatch: pytest.MonkeyPatch,
    matched: bool,
) -> tuple[dict, int]:
    parsed_key, _ = atr.api.openpgp.composed.SignedPublicKey.from_armor(_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC)
    committee = SimpleNamespace(key="example", is_podling=False)
    stored = SimpleNamespace(
        fingerprint=parsed_key.fingerprint.lower(),
        ascii_armored_key=_EMBEDDED_SUBKEY_PUBLIC_KEY_ASC,
        committees=[committee],
    )
    signature = SimpleNamespace(
        signature=SimpleNamespace(
            issuer_fingerprint=lambda: {parsed_key.fingerprint.lower()},
            issuer_key_id=lambda: set(),
        )
    )
    matched_committees = [committee] if matched else []

    monkeypatch.setattr(atr.api.db, "session", _mock_session_factory(MockKeyDBSession([stored])))
    monkeypatch.setattr(
        atr.api.openpgp.composed,
        "DetachedSignature",
        SimpleNamespace(from_armor=lambda _text: (signature, "")),
    )
    monkeypatch.setattr(atr.api, "_match_committees", mock.AsyncMock(return_value=matched_committees))
    monkeypatch.setattr(atr.api, "_jwt_asf_uid", lambda: "tester")

    signature_provenance = inspect.unwrap(atr.api.signature_provenance)
    return await signature_provenance(
        "signature/provenance",
        _make_args("example.tar.gz.asc", "abc123"),
    )


def _make_args(
    signature_file_name: str,
    signature_sha3_256: str,
    *,
    project_key: safe.ProjectKey | None = None,
    version_key: safe.VersionKey | None = None,
) -> api.SignatureProvenanceArgs:
    return api.SignatureProvenanceArgs(
        signature_file_name=signature_file_name,
        signature_asc_text="-----BEGIN PGP SIGNATURE-----\ntest\n-----END PGP SIGNATURE-----\n",
        signature_sha3_256=signature_sha3_256,
        project_key=project_key,
        version_key=version_key,
    )


@contextlib.asynccontextmanager
async def _mock_db_session(db_data: MockDBSession) -> AsyncGenerator[MockDBSession]:
    yield db_data


def _mock_session_factory(db_data: MockDBSession):
    def session() -> contextlib.AbstractAsyncContextManager[MockDBSession]:
        return _mock_db_session(db_data)

    return session
