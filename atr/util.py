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
import asyncio
import base64
import binascii
import collections
import contextlib
import dataclasses
import datetime
import email.parser
import email.policy
import email.utils
import enum
import fcntl
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import socket
import ssl
import tempfile
import unicodedata
import urllib.parse
import uuid
from collections.abc import AsyncGenerator, Callable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, Final, Protocol

import aiofiles.os
import aiohttp
import aiohttp.abc
import aioshutil
import asfquart.base as base
import asfquart.session as session
import gitignore_parser
import jinja2
import markupsafe
import openpgp
import pydantic
import quart

# NOTE: The atr.db module imports this module
# Therefore, this module must not import atr.db
import atr.config as config
import atr.constants as constants
import atr.ldap as ldap
import atr.log as log
import atr.models.cap as cap
import atr.models.safe as safe
import atr.models.sql as sql
import atr.models.validation as validation
import atr.paths as paths
import atr.registry as registry
import atr.user as user

if TYPE_CHECKING:
    import atr.storage.datatypes as datatypes

ARCHIVE_FORMAT_SUFFIXES: Final[tuple[str, ...]] = (
    ".tar.bz2",
    ".tar.gz",
    ".tar.xz",
    ".tgz",
    ".zip",
)
ARCHIVE_ROOT_SUFFIXES: Final[tuple[str, ...]] = (
    "-binary-assembly",
    "-binary",
    "-bin",
    "-source-release",
    "-source",
    "-src",
)
AUTOMATED_RELEASE_SIGNING_LABELS: Final[tuple[str, ...]] = ("Automated Release Signing", "Services RM")
CONCERN_ACKNOWLEDGEMENT_MESSAGE: Final[str] = (
    "Review and acknowledge every current concern group before starting the vote"
)
DIRECTORY_PERMISSIONS: Final[int] = 0o755
DEV_TEST_MID: Final[str] = "818a44a3-6984-4aba-a650-834e86780b43@apache.org"
DEV_THREAD_URLS: Final[dict[str, str]] = {
    "CAH5JyZo8QnWmg9CwRSwWY=GivhXW4NiLyeNJO71FKdK81J5-Uw@mail.gmail.com": "https://lists.apache.org/thread/z0o7xnjnyw2o886rxvvq2ql4rdfn754w",
    "818a44a3-6984-4aba-a650-834e86780b43@apache.org": "https://lists.apache.org/thread/619hn4x796mh3hkk3kxg1xnl48dy2s64",
    "CAA9ykM+bMPNk=BOF9hj0O+mjN1igppOJ+pKdZHcAM0ddVi+5_w@mail.gmail.com": "https://lists.apache.org/thread/x0m3p2xqjvflgtkb6oxqysm36cr9l5mg",
    "CAFHDsVzgtfboqYF+a3owaNf+55MUiENWd3g53mU4rD=WHkXGwQ@mail.gmail.com": "https://lists.apache.org/thread/brj0k3g8pq63g8f7xhmfg2rbt1240nts",
    "CAMomwMrvKTQK7K2-OtZTrEO0JjXzO2g5ynw3gSoks_PXWPZfoQ@mail.gmail.com": "https://lists.apache.org/thread/y5rqp5qk6dmo08wlc3g20n862hznc9m8",
    "CANVKqzfLYj6TAVP_Sfsy5vFbreyhKskpRY-vs=F7aLed+rL+uA@mail.gmail.com": "https://lists.apache.org/thread/oy969lhh6wlzd51ovckn8fly9rvpopwh",
    "CAH4123ZwGtkwszhEU7qnMByLa-yvyKz2W+DjH_UChPMuzaa54g@mail.gmail.com": "https://lists.apache.org/thread/7111mqyc25sfqxm6bf4ynwhs0bk0r4ys",
    "CADL1oArKFcXvNb1MJfjN=10-yRfKxgpLTRUrdMM1R7ygaTkdYQ@mail.gmail.com": "https://lists.apache.org/thread/d7119h2qm7jrd5zsbp8ghkk0lpvnnxnw",
    "a1507118-88b1-4b7b-923e-7f2b5330fc01@apache.org": "https://lists.apache.org/thread/gzjd2jv7yod5sk5rgdf4x33g5l3fdf5o",
}
INCUBATOR_GENERAL_ADDRESS: Final[str] = "general@incubator.apache.org"
NPM_PACKAGE_JSON_MAX_SIZE: Final[int] = 512 * 1024
PRIVATE_KEY_UPLOAD_WARNING: Final[str] = (
    "Private key upload blocked. You appear to have uploaded a private key, not a public key. ATR has rejected it "
    "and attempted to discard the uploaded material from server memory. Treat this key as compromised: revoke it "
    "immediately, remove it anywhere that it grants access, and generate a new key before signing releases."
)
USER_TESTS_ADDRESS: Final[str] = "user-tests@tooling.apache.org"
LISTS_APACHE_TIMEOUT: Final[aiohttp.ClientTimeout] = aiohttp.ClientTimeout(total=30, connect=10)
CAP_TIMEOUT: Final[aiohttp.ClientTimeout] = aiohttp.ClientTimeout(total=30, connect=10)
PROPAGATION_TIMEOUT: Final[aiohttp.ClientTimeout] = aiohttp.ClientTimeout(total=10, connect=5)
PROPAGATION_PROBE_DEADLINE: Final[int] = 15
MAX_PROPAGATION_ARTIFACTS: Final[int] = 50
EXPECTED_DEFAULT_TLS_CHECK_HOSTNAME: Final[bool] = True
EXPECTED_DEFAULT_TLS_MINIMUM_VERSION: Final[ssl.TLSVersion] = ssl.TLSVersion.TLSv1_2
EXPECTED_DEFAULT_TLS_VERIFY_MODE: Final[ssl.VerifyMode] = ssl.CERT_REQUIRED
EXPECTED_DEFAULT_TLS_CIPHER_NAMES: Final[tuple[str, ...]] = (
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "TLS_AES_128_GCM_SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384",
    "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-AES128-GCM-SHA256",
    "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-CHACHA20-POLY1305",
    "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-ECDSA-AES256-SHA384",
    "ECDHE-RSA-AES256-SHA384",
    "ECDHE-ECDSA-AES128-SHA256",
    "ECDHE-RSA-AES128-SHA256",
    "DHE-RSA-AES256-GCM-SHA384",
    "DHE-RSA-AES128-GCM-SHA256",
    "DHE-RSA-AES256-SHA256",
    "DHE-RSA-AES128-SHA256",
)
_CONCERN_LABELS_MAX_LISTED: Final[int] = 5
# We have to do this interpolation because of our private key detection lint
_PEM_PRIVATE: Final[str] = "PRIVATE"
_PRIVATE_KEY_MARKERS: Final[tuple[str, ...]] = (
    f"-----BEGIN PGP {_PEM_PRIVATE} KEY BLOCK-----",
    f"-----BEGIN OPENSSH {_PEM_PRIVATE} KEY-----",
    f"-----BEGIN {_PEM_PRIVATE} KEY-----",
    f"-----BEGIN ENCRYPTED {_PEM_PRIVATE} KEY-----",
    f"-----BEGIN RSA {_PEM_PRIVATE} KEY-----",
    f"-----BEGIN DSA {_PEM_PRIVATE} KEY-----",
    f"-----BEGIN EC {_PEM_PRIVATE} KEY-----",
    f"-----BEGIN ED25519 {_PEM_PRIVATE} KEY-----",
)


@dataclasses.dataclass(frozen=True)
class ConcernGroup:
    checker: str
    label: str
    count: int


@dataclasses.dataclass(frozen=True)
class PropagationOutcome:
    rel_path: str
    public_url: str
    ok: bool
    status: int | None
    error: str | None

    @property
    def missing(self) -> bool:
        return self.status in {404, 410}

    @property
    def transient(self) -> bool:
        return (self.status is None) or (self.status == 429) or (500 <= self.status < 600)


@dataclasses.dataclass(frozen=True)
class PropagationSummary:
    target: "SvnPublishTarget"
    total: int
    reachable: int
    outcomes: list[PropagationOutcome]
    unprobed: int = 0

    @property
    def blocked(self) -> list[PropagationOutcome]:
        return [outcome for outcome in self.outcomes if not (outcome.ok or outcome.missing or outcome.transient)]

    @property
    def missing(self) -> list[PropagationOutcome]:
        return [outcome for outcome in self.outcomes if outcome.missing]

    @property
    def unreachable(self) -> list[PropagationOutcome]:
        return [outcome for outcome in self.outcomes if (not outcome.ok) and outcome.transient]


class SvnPublishTarget(enum.Enum):
    ATR = "atr"
    RELEASE = "release"


class DownloadFile(enum.Enum):
    # Ultimately decides where a published release file should be fetched from:
    # artifacts via the mirror network, metadata from the downloads.a.o.
    ARTIFACT = "artifact"
    METADATA = "metadata"


class EmailRecipients(Protocol):
    email_to: str
    email_cc: list[str]
    email_bcc: list[str]


class EmailUidLookup(Protocol):
    def get(self, email: str) -> str | None: ...


@dataclasses.dataclass(frozen=True)
class NpmPackInfo:
    name: str
    version: str
    filename_match: bool | None


class SshFingerprintError(ValueError):
    pass


@dataclasses.dataclass
class FileStat:
    path: str
    modified: int
    size: int
    permissions: int
    is_file: bool
    is_dir: bool


class FetchError(RuntimeError):
    def __init__(self, message: str, url: str):
        super().__init__(message)
        self.url = url


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self) -> None:
        self.__resolver = aiohttp.DefaultResolver()

    async def close(self) -> None:
        await self.__resolver.close()

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[aiohttp.abc.ResolveResult]:
        results = await self.__resolver.resolve(host, port, family)
        for result in results:
            if not ipaddress.ip_address(result["host"]).is_global:
                raise aiohttp.ClientConnectionError(f"The address of {host} is not public")
        return results


def archive_format_stem(name: str) -> str | None:
    for suffix in ARCHIVE_FORMAT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def as_url(func: Callable, **kwargs: Any) -> str:
    """Return the URL for a function."""
    if isinstance(func, jinja2.runtime.Undefined):
        log.exception("Undefined route in the calling template")
        raise RuntimeError("Undefined route in the calling template")
    endpoint = getattr(func, "endpoint", None)
    if endpoint is None:
        log.error(f"Cannot get endpoint for {func} (type: {type(func)})")
        raise RuntimeError(f"Cannot get endpoint for {func} (type: {type(func)})")
    return quart.url_for(endpoint, **kwargs)


async def asf_uid_from_uids(uids: list[str], email_uid_lookup: EmailUidLookup, use_ldap: bool = True) -> str | None:
    # Determine ASF UID if not provided
    emails = []
    for uid_str in uids:
        # This returns a lower case email address, no matter what the case of the input UID
        if email := email_from_uid(uid_str):
            if email.endswith("@apache.org"):
                return email.removesuffix("@apache.org")
            emails.append(email)
    # We did not find a direct @apache.org email address
    # Therefore, search cached LDAP data, then LDAP directly if configured
    for email in emails:
        if asf_uid := email_uid_lookup.get(email):
            return asf_uid
    if use_ldap:
        # Search LDAP directly
        for email in emails:
            if asf_uid := await asyncio.to_thread(_asf_uid_from_email, email):
                return asf_uid
    return None


@contextlib.asynccontextmanager
async def async_temporary_directory(
    suffix: str | None = None, prefix: str | None = None, dir: str | os.PathLike | None = None
) -> AsyncGenerator[pathlib.Path]:
    """Create an async temporary directory similar to tempfile.TemporaryDirectory."""
    temp_dir_path: str = await asyncio.to_thread(tempfile.mkdtemp, suffix=suffix, prefix=prefix, dir=dir)
    try:
        yield pathlib.Path(temp_dir_path)
    finally:
        try:
            await aioshutil.rmtree(temp_dir_path)
        except Exception:
            log.exception(f"Failed to remove temporary directory {temp_dir_path}")


async def atomic_modify_file(
    file_path: pathlib.Path,
    modify: Callable[[str], str],
) -> None:
    # This function assumes that file_path already exists and its a regular file
    lock_path = file_path.with_suffix(file_path.suffix + ".lock")
    lock_fd = await asyncio.to_thread(os.open, str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        await asyncio.to_thread(fcntl.flock, lock_fd, fcntl.LOCK_EX)
        try:
            async with aiofiles.open(file_path, encoding="utf-8") as rf:
                old_value = await rf.read()
            new_value = modify(old_value)
            if new_value != old_value:
                await atomic_write_file(file_path, new_value)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        await asyncio.to_thread(os.close, lock_fd)


async def atomic_write_file(file_path: pathlib.Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write content to a file using a temporary file."""
    await aiofiles.os.makedirs(file_path.parent, exist_ok=True)
    temp_path = file_path.parent / f".{file_path.name}.{uuid.uuid4()}.tmp"
    try:
        async with aiofiles.open(temp_path, "w", encoding=encoding) as f:
            await f.write(content)
            await f.flush()
            await asyncio.to_thread(os.fsync, f.fileno())
        await aiofiles.os.rename(temp_path, file_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            await aiofiles.os.remove(temp_path)
        raise


async def cap_create_approval_question(
    action: sql.ApprovalAction,
    project_key: safe.ProjectKey,
    display_name: str,
    committee_key: str,
    requested_by: str,
    closes_at: datetime.datetime,
    release_version: safe.VersionKey | None = None,
) -> cap.Question:
    token = await cap_mint_token()
    match action:
        case sql.ApprovalAction.ARCHIVE:
            verb = action.value
            approval_type = constants.CAP_ARCHIVE_APPROVAL_TYPE
            consequence = "ATR will mark the project RETIRED"
            subject = f"the project {project_key} ({display_name})"
            title = f"[ATR] {verb.capitalize()} project {project_key}"
        case sql.ApprovalAction.ARCHIVE_RELEASE:
            verb = "archive"
            approval_type = constants.CAP_ARCHIVE_APPROVAL_TYPE
            consequence = "ATR will mark the release as archived and remove it from the downloads area"
            subject = f"release {release_version} of project {project_key} ({display_name})"
            title = f"[ATR] Archive release {project_key} {release_version}"
        case sql.ApprovalAction.DELETE:
            verb = action.value
            approval_type = constants.CAP_DELETE_APPROVAL_TYPE
            consequence = "ATR will permanently delete the project and its metadata"
            subject = f"the project {project_key} ({display_name})"
            title = f"[ATR] {verb.capitalize()} project {project_key}"
    if action == sql.ApprovalAction.ARCHIVE_RELEASE:
        completion = "ATR will complete this automatically once the vote passes"
    else:
        completion = f"an authorised {committee_key} PMC member may complete the {verb} in ATR"
    description = (
        f"{requested_by} has requested, through ATR, to {verb} {subject}. "
        f"If this vote passes, {consequence}, and {completion}. "
        f"This request was filed by Apache Trusted Releases on behalf of {requested_by}."
    )
    return await cap_create_question(
        token,
        project_id=committee_key,
        title=title,
        description=description,
        target_audience=f"Binding voters: {committee_key} PMC members",
        approval_type=approval_type,
        closes_at=closes_at,
    )


async def cap_create_question(
    token: str,
    *,
    project_id: str,
    title: str,
    description: str,
    target_audience: str,
    approval_type: str,
    closes_at: datetime.datetime,
) -> cap.Question:
    url = f"{config.get().CAP_API_BASE_URL.rstrip('/')}/api/question"
    headers = {"Authorization": f"bearer {token}"}
    payload = {
        "project_id": project_id,
        "title": title,
        "description": description,
        "target_audience": target_audience,
        "approval_type": approval_type,
        "is_binding": True,
        "is_private": True,
        "response_option": {"kind": "vote"},
        "closes_at": closes_at.astimezone(datetime.UTC).isoformat(),
    }
    try:
        async with create_secure_session(timeout=CAP_TIMEOUT) as http_session:
            async with http_session.post(url, json=payload, headers=headers) as response:
                status = response.status
                body = await response.text()
    except Exception as exc:
        raise FetchError(f"CAP question creation failed: {exc}", url=url) from exc
    if status != 201:
        raise FetchError(f"CAP question creation failed ({status}): {body}", url=url)
    try:
        return cap.Question.model_validate_json(body)
    except pydantic.ValidationError as exc:
        raise FetchError(f"CAP question creation returned an invalid response: {exc}", url=url) from exc


async def cap_mint_token() -> str:
    url = f"{config.get().CAP_API_BASE_URL.rstrip('/')}/api/token"
    permanent = config.get().CAP_ROLE_ACCOUNT_TOKEN
    if not permanent:
        raise FetchError("CAP role account token is not configured", url=url)
    headers = {"Authorization": f"bearer {permanent}"}
    try:
        async with create_secure_session(timeout=CAP_TIMEOUT) as http_session:
            async with http_session.get(url, headers=headers) as response:
                status = response.status
                body = await response.text()
    except Exception as exc:
        raise FetchError(f"CAP token minting failed: {exc}", url=url) from exc
    if status != 201:
        raise FetchError(f"CAP token minting failed ({status})", url=url)
    try:
        return cap.TokenIssued.model_validate_json(body).token
    except pydantic.ValidationError as exc:
        raise FetchError(f"CAP token minting returned an invalid response: {exc}", url=url) from exc


async def cap_resolve_question(token: str, question_id: int) -> cap.Resolution | None:
    url = f"{config.get().CAP_API_BASE_URL.rstrip('/')}/api/question/{question_id}/resolve"
    headers = {"Authorization": f"bearer {token}"}
    try:
        async with create_secure_session(timeout=CAP_TIMEOUT) as http_session:
            async with http_session.post(url, headers=headers) as response:
                status = response.status
                body = await response.text()
    except Exception as exc:
        raise FetchError(f"CAP question resolution failed: {exc}", url=url) from exc
    if status == 403:
        try:
            error_kind = cap.ErrorMessage.model_validate_json(body).error
        except pydantic.ValidationError:
            error_kind = None
        if error_kind == "deadline_in_future":
            return None
    if status != 200:
        raise FetchError(f"CAP question resolution failed ({status}): {body}", url=url)
    try:
        return cap.Resolution.model_validate_json(body)
    except pydantic.ValidationError as exc:
        raise FetchError(f"CAP question resolution returned an invalid response: {exc}", url=url) from exc


async def check_propagation(
    target: SvnPublishTarget,
    public_base_url: str,
    rel_paths: Sequence[str],
) -> PropagationSummary:
    capped = list(rel_paths[:MAX_PROPAGATION_ARTIFACTS])
    unprobed = len(rel_paths) - len(capped)
    if not capped:
        return PropagationSummary(target=target, total=0, reachable=0, outcomes=[])
    async with create_secure_session(timeout=PROPAGATION_TIMEOUT) as http_session:
        outcomes = await asyncio.gather(
            *[
                _propagation_probe(http_session, rel_path, _propagation_public_url(public_base_url, rel_path))
                for rel_path in capped
            ]
        )
    reachable = sum(1 for outcome in outcomes if outcome.ok)
    return PropagationSummary(
        target=target, total=len(outcomes), reachable=reachable, outcomes=outcomes, unprobed=unprobed
    )


def checker_display_name(checker: str) -> str:
    return checker.removeprefix("atr.tasks.checks.").replace("_", " ").replace(".", " ").title().replace("Sbom", "SBOM")


def chmod_directories(path: os.PathLike, permissions: int = DIRECTORY_PERMISSIONS) -> None:
    os.chmod(path, permissions)
    for dir_path in pathlib.Path(path).rglob("*"):
        if dir_path.is_dir():
            os.chmod(dir_path, permissions)


def chmod_files(path: os.PathLike, permissions: int) -> None:
    """Set permissions on all files in a directory tree."""
    for file_path in pathlib.Path(path).rglob("*"):
        if file_path.is_file():
            os.chmod(file_path, permissions)


def committee_is_standing(committee_key: str) -> bool:
    return committee_key in registry.STANDING_COMMITTEES


def concern_acknowledgement_error(missing: Sequence[ConcernGroup]) -> str:
    """Build a stable error sentence for unacknowledged concern groups."""
    listed = [group.label for group in missing[:_CONCERN_LABELS_MAX_LISTED]]
    extra = len(missing) - _CONCERN_LABELS_MAX_LISTED
    suffix = f" and {extra} more" if (extra > 0) else ""
    return f"{CONCERN_ACKNOWLEDGEMENT_MESSAGE}: {', '.join(listed)}{suffix}"


def concern_groups(info: "datatypes.PathInfo | None") -> list[ConcernGroup]:
    if info is None:
        return []
    counts_by_checker: dict[str, int] = collections.defaultdict(int)
    for stat in info.checker_stats:
        count = stat.counts.get(sql.CheckResultStatus.CONCERN, 0)
        if count > 0:
            counts_by_checker[stat.checker] += count
    for result in info.release_level_concerns:
        counts_by_checker[result.checker] += 1
    return [
        ConcernGroup(checker=checker, label=checker_display_name(checker), count=count)
        for checker, count in sorted(counts_by_checker.items())
    ]


def configurable_recipients(action: sql.RecipientAction, committee_key: str, *, is_podling: bool) -> list[str]:
    # Stable, committee-derived recipients a project can persist as defaults.
    # These don't depend on the sender or on ALPHA test addresses, so a stored
    # default stays valid for whoever later sends the email.
    if action == sql.RecipientAction.ANNOUNCE:
        return [
            "announce@apache.org",
            f"announce@{committee_key}.apache.org",
            f"dev@{committee_key}.apache.org",
            f"user@{committee_key}.apache.org",
            f"private@{committee_key}.apache.org",
        ]
    if is_podling:
        return [f"dev@{committee_key}.apache.org"]
    return [f"dev@{committee_key}.apache.org", f"private@{committee_key}.apache.org"]


def conjunction(items: Sequence[str], empty: str | None = None) -> str:
    match len(items):
        case 0:
            if empty is None:
                raise ValueError("No items to join")
            return empty
        case 1:
            return items[0]
        case 2:
            return f"{items[0]} and {items[1]}"
        case _:
            return ", ".join(items[:-1]) + f", and {items[-1]}"


def contains_private_key_text(key_text: str) -> bool:
    return any(marker in key_text for marker in _PRIVATE_KEY_MARKERS)


async def content_list(
    phase_subdir: safe.StatePath,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    revision_name: safe.RevisionNumber | None = None,
) -> AsyncGenerator[FileStat]:
    """List all the files in the given path."""
    base_path = phase_subdir / project_key / version_key
    if phase_subdir.name in {"release-candidate-draft", "release-preview"}:
        if revision_name is None:
            raise ValueError("A revision name is required for release candidate draft or preview content listing")
    if revision_name:
        base_path = base_path / revision_name
    async for path in paths_recursive(base_path):
        stat = await aiofiles.os.stat(base_path / path)
        yield FileStat(
            path=str(path),
            modified=int(stat.st_mtime),
            size=stat.st_size,
            permissions=stat.st_mode,
            is_file=bool(stat.st_mode & 0o0100000),
            is_dir=bool(stat.st_mode & 0o040000),
        )


async def create_hard_link_clone(
    source_dir: safe.StatePath,
    dest_dir: safe.StatePath,
    do_not_create_dest_dir: bool = False,
    exist_ok: bool = False,
    dry_run: bool = False,
) -> None:
    """Recursively create a clone of source_dir in dest_dir using hard links for files."""
    await _create_hard_link_clone_checks(source_dir, dest_dir, do_not_create_dest_dir, dry_run)

    async def _clone_recursive(current_source: safe.StatePath, current_dest: safe.StatePath) -> None:
        with await aiofiles.os.scandir(current_source) as scan:
            entries = list(scan)
        for entry in entries:
            source_entry_path = current_source / entry.name
            dest_entry_path = current_dest / entry.name

            try:
                if entry.is_dir():
                    await aiofiles.os.makedirs(dest_entry_path, exist_ok=True)
                    await _clone_recursive(source_entry_path, dest_entry_path)
                elif entry.is_file():
                    if not dry_run:
                        try:
                            await aiofiles.os.link(source_entry_path, dest_entry_path)
                        except FileExistsError:
                            if not exist_ok:
                                raise
                            await aiofiles.os.remove(dest_entry_path)
                            await aiofiles.os.link(source_entry_path, dest_entry_path)
                    elif dry_run and (await aiofiles.os.path.exists(dest_entry_path)):
                        raise ValueError(f"Destination path exists: {dest_entry_path}")
                # Ignore other types like symlinks for now
            except OSError as e:
                log.error(f"Error cloning {source_entry_path} to {dest_entry_path}: {e}")
                raise

    await _clone_recursive(source_dir, dest_dir)


def create_path_matcher(
    lines: Iterable[str], full_path: pathlib.Path | None, base_dir: pathlib.Path | safe.StatePath
) -> Callable[[str], bool]:
    rules = []
    negation = False
    for line_no, line in enumerate(lines, start=1):
        rule = gitignore_parser.rule_from_pattern(line.rstrip("\n"), base_path=base_dir, source=(full_path, line_no))
        if rule:
            rules.append(rule)
            if rule.negation:
                negation = True
    if not negation:
        return lambda file_path: any(r.match(file_path) for r in rules)
    return lambda file_path: gitignore_parser.handle_negation(file_path, rules)


def create_secure_session(
    timeout: aiohttp.ClientTimeout | None = None,
    public: bool = False,
) -> aiohttp.ClientSession:
    """Create a secure aiohttp.ClientSession with hardened SSL/TLS configuration."""
    resolver = PublicResolver() if public else None
    connector = aiohttp.TCPConnector(ssl=create_secure_ssl_context(), resolver=resolver)
    # We pass the timeout to the ClientSession constructor
    return aiohttp.ClientSession(connector=connector, timeout=timeout)


def create_secure_ssl_context() -> ssl.SSLContext:
    """Create a secure SSL context compliant with ASVS 9.1.1 and 9.1.2."""
    # These are the default values in Python 3.13.3:
    # >>> import ssl
    # >>> ctx = ssl.create_default_context()
    # >>> (ctx.check_hostname, ctx.verify_mode, ctx.minimum_version)
    # (True, <VerifyMode.CERT_REQUIRED: 2>, <TLSVersion.TLSv1_2: 771>)
    # But we set them explicitly to pin and document them
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


async def delete_immutable_directory(path: safe.StatePath, reason: str) -> None:
    if not reason:
        raise ValueError("Reason is required to delete an immutable directory")
    log.info(f"Deleting immutable directory {path} because {reason}")
    await asyncio.to_thread(chmod_directories, path, 0o755)
    await aioshutil.rmtree(path)


def download_page_url_error(url: str) -> str | None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        return "Must use https"
    if not parts.hostname:
        return "Must include a host"
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return None
    return "Must not use a literal IP address"


def download_url_for_path(relpath: safe.RelPath, kind: DownloadFile, archived: bool = False) -> str:
    # Where a published file at a known dist-relative path is fetched from. Release artifacts use
    # the mirror network; verification metadata uses the canonical host; archived files use the archive.
    if archived:
        return paths.archive_download_url(relpath)
    if kind is DownloadFile.ARTIFACT:
        return paths.closer_download_url(relpath)
    return paths.downloads_url(relpath)


def email_from_uid(uid: str) -> str | None:
    if m := re.search(r"<([^>]+)>", uid):
        return m.group(1).lower()
    elif m := re.search(r"^([^@ ]+)@apache.org$", uid):
        return uid
    return None


async def email_mid_from_thread_id(thread_id: str) -> tuple[str, str]:
    async for _thread_id, msg_json in thread_messages(thread_id):
        # The .get("to", "") value may be redacted, e.g. "us...@tooling.apache.org"
        # Therefore use .get("forum", "")
        email_to = msg_json.get("forum", "")
        if email_to is None:
            raise RuntimeError(f"Cannot find email address for {thread_id}")
        # This is delimited by angle brackets, e.g. "<1234567890@apache.org>"
        message_id = msg_json.get("message-id", "")
        if message_id is None:
            raise RuntimeError(f"Cannot find message ID for {thread_id}")
        return email_to, message_id
    raise RuntimeError(f"Cannot find any messages in {thread_id}")


async def email_to_uid_map() -> dict[str, str]:
    # Get all email addresses in LDAP
    conf = config.AppConfig()
    bind_dn = conf.LDAP_BIND_DN
    bind_password = conf.LDAP_BIND_PASSWORD
    ldap_params = ldap.SearchParameters(
        uid_query="*",
        bind_dn_from_config=bind_dn,
        bind_password_from_config=bind_password,
    )
    await asyncio.to_thread(ldap.search, ldap_params)
    if ldap_params.err_msg:
        raise RuntimeError(f"LDAP search failed: {ldap_params.err_msg}")

    # Map the LDAP addresses to Apache UIDs
    email_to_uid: dict[str, str] = {}
    for entry in ldap_params.results_list:
        uid_lower = (entry.uid[0] if entry.uid else "").lower()
        for mail in entry.mail:
            email_to_uid[mail.lower()] = uid_lower
        for alt_email in entry.asf_alt_email:
            email_to_uid[alt_email.lower()] = uid_lower
        for committer_email in entry.asf_committer_email:
            email_to_uid[committer_email.lower()] = uid_lower
    return email_to_uid


def format_datetime(dt_obj: datetime.datetime | int) -> str:
    """Format a datetime object or Unix timestamp into a human readable datetime string."""
    # Integers are unix timestamps
    if isinstance(dt_obj, int):
        dt_obj = datetime.datetime.fromtimestamp(dt_obj, tz=datetime.UTC)

    # Ensure UTC native timezone awareness
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=datetime.UTC)
    else:
        # Convert to UTC if not already
        dt_obj = dt_obj.astimezone(datetime.UTC)

    return dt_obj.strftime("%Y-%m-%d %H:%M:%S")


def format_file_size(size_in_bytes: int) -> str:
    """Format a file size with appropriate units and comma-separated digits."""
    # Format the raw bytes with commas
    formatted_bytes = f"{size_in_bytes:,}"

    # Calculate the appropriate unit
    if size_in_bytes >= 1_000_000_000:
        size_in_gb = size_in_bytes // 1_000_000_000
        return f"{size_in_gb:,} GB ({formatted_bytes} bytes)"
    elif size_in_bytes >= 1_000_000:
        size_in_mb = size_in_bytes // 1_000_000
        return f"{size_in_mb:,} MB ({formatted_bytes} bytes)"
    elif size_in_bytes >= 1_000:
        size_in_kb = size_in_bytes // 1_000
        return f"{size_in_kb:,} KB ({formatted_bytes} bytes)"
    else:
        return f"{formatted_bytes} bytes"


def format_permissions(mode: int) -> str:
    """Format Unix file permissions in ls -l style."""
    # File type
    if mode & 0o040000:
        # Directory
        perms = "d"
    elif mode & 0o0100000:
        # Regular file
        perms = "-"
    elif mode & 0o020000:
        # Character special
        perms = "c"
    elif mode & 0o060000:
        # Block special
        perms = "b"
    elif mode & 0o010000:
        # FIFO
        perms = "p"
    elif mode & 0o0140000:
        # Socket
        perms = "s"
    else:
        perms = "?"

    # Owner permissions
    perms += "r" if (mode & 0o400) else "-"
    perms += "w" if (mode & 0o200) else "-"
    perms += "x" if (mode & 0o100) else "-"

    # Group permissions
    perms += "r" if (mode & 0o040) else "-"
    perms += "w" if (mode & 0o020) else "-"
    perms += "x" if (mode & 0o010) else "-"

    # Others permissions
    perms += "r" if (mode & 0o004) else "-"
    perms += "w" if (mode & 0o002) else "-"
    perms += "x" if (mode & 0o001) else "-"

    return perms


async def get_asf_id_or_die() -> str:
    web_session = getattr(quart.g, "_user_session", None)
    if not isinstance(web_session, sql.UserSession):
        web_session = await session.read()
        if not isinstance(web_session, sql.UserSession):
            raise base.ASFQuartException("Not authenticated", errorcode=401)
        quart.g._user_session = web_session
    return web_session.uid


async def get_release_stats(release: sql.Release) -> tuple[int, int, str]:
    """Calculate file count, total byte size, and formatted size for a release."""
    base_dir = paths.release_directory(release)
    count = 0
    total_bytes = 0
    try:
        async for rel_path in paths_recursive(base_dir):
            full_path = base_dir / rel_path
            if await aiofiles.os.path.isfile(full_path):
                try:
                    size = await aiofiles.os.path.getsize(full_path)
                    count += 1
                    total_bytes += size
                except OSError:
                    ...
    except FileNotFoundError:
        ...

    formatted_size = format_file_size(total_bytes)
    return count, total_bytes, formatted_size


async def get_urls_as_completed(urls: Sequence[str]) -> AsyncGenerator[tuple[str, int | str | None, bytes]]:
    """GET a list of URLs in parallel and yield (url, status, content_bytes) as they become available."""
    async with create_secure_session(timeout=LISTS_APACHE_TIMEOUT) as session:

        async def _fetch(one_url: str) -> tuple[str, int | str | None, bytes]:
            try:
                async with session.get(one_url) as resp:
                    try:
                        resp.raise_for_status()
                        return (str(resp.url), resp.status, await resp.read())
                    except aiohttp.ClientResponseError as e:
                        url = str(e.request_info.real_url)
                        if e.status == 200:
                            return (url, str(e), b"")
                        return (url, e.status, b"")
            except Exception as exc:
                return ("", str(exc), b"")

        tasks = [asyncio.create_task(_fetch(u)) for u in urls]
        for future in asyncio.as_completed(tasks):
            yield await future


async def has_files(release: sql.Release) -> bool:
    """Check if a release has any files."""
    base_dir = paths.release_directory(release)
    try:
        async for rel_path in paths_recursive(base_dir):
            full_path = base_dir / rel_path
            if await aiofiles.os.path.isfile(full_path):
                return True
    except FileNotFoundError:
        ...
    return False


def intersect_algs(policy: dict[str, Any], policy_key: str, supported: set[bytes]) -> list[str]:
    algs = policy[policy_key]
    if not isinstance(algs, list):
        raise TypeError(f"ssh-audit policy '{policy_key}' is not a list")
    return [a for a in algs if isinstance(a, str) and (a.encode("ascii") in supported)]


def is_automated_release_signing_uid(uid: str | None, committee_key: str) -> bool:
    if not uid:
        return False
    if not any(label in uid for label in AUTOMATED_RELEASE_SIGNING_LABELS):
        return False
    return email_from_uid(uid) == f"private@{committee_key}.apache.org"


async def is_dir_resolve(path: os.PathLike) -> pathlib.Path | None:
    try:
        resolved_path = await asyncio.to_thread(pathlib.Path(path).resolve)
        if not await aiofiles.os.path.isdir(resolved_path):
            return None
    except (FileNotFoundError, OSError):
        return None
    return resolved_path


def is_disallowed_dotfile(segment: str) -> bool:
    if not segment.startswith("."):
        return False
    if segment.startswith(".atr"):
        return False
    # Temporary, and only due to issues #757 and #769
    if segment == ".gitkeep":
        return False
    return True


def is_user_session_downgraded() -> bool:
    """Check whether a user session is downgraded from active admin privileges."""
    try:
        return bool(getattr(quart.g, "is_session_downgraded", False))
    except RuntimeError:
        return False


def is_user_viewing_as_admin(uid: str | None) -> bool:
    """Check whether a user is currently viewing the site with active admin privileges."""
    return user.is_admin(uid)


def json_for_script_element(value: Any) -> markupsafe.Markup:
    """Serialise JSON safely for use inside a script element."""
    return jinja2.utils.htmlsafe_json_dumps(value, dumps=json.dumps, ensure_ascii=False)


def key_ssh_fingerprint(ssh_key_string: str) -> str:
    try:
        return key_ssh_fingerprint_core(ssh_key_string)
    except ValueError as e:
        raise SshFingerprintError(str(e)) from e


def key_ssh_fingerprint_core(ssh_key_string: str) -> str:
    # The format should be as in *.pub or authorized_keys files
    # I.e. TYPE DATA COMMENT
    ssh_key_parts = ssh_key_string.strip().split()
    if len(ssh_key_parts) >= 2:
        # We discard the type, which is ssh_key_parts[0]
        key_data = ssh_key_parts[1]
        # We discard the comment, which is ssh_key_parts[2]

        # Standard fingerprint calculation
        try:
            decoded_key_data = base64.b64decode(key_data)
        except binascii.Error as e:
            raise ValueError(f"Invalid base64 encoding in key data: {e}") from e

        digest = hashlib.sha256(decoded_key_data).digest()
        fingerprint_b64 = base64.b64encode(digest).decode("utf-8").rstrip("=")

        # Prefix follows the standard format
        return f"SHA256:{fingerprint_b64}"

    raise ValueError("Invalid SSH key format")


def match_ignore_pattern(pattern: str | None, value: str | None) -> bool:

    if pattern == "!":
        # Special case, "!" matches None
        return value is None
    if (pattern is None) or (value is None):
        return False
    negate = False
    raw_pattern = pattern
    if raw_pattern.startswith("!"):
        raw_pattern = raw_pattern[1:]
        negate = True
    try:
        regex = validation.compile_ignore_pattern(raw_pattern)
    except ValueError:
        return False
    matched = regex.search(value) is not None
    if negate:
        return not matched
    return matched


def missing_concern_groups(
    groups: Sequence[ConcernGroup],
    submitted: Iterable[str],
) -> list[ConcernGroup]:
    submitted_set = set(submitted)
    return [group for group in groups if (group.checker not in submitted_set)]


def normalized_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    default_port = {"http": 80, "https": 443}.get(scheme)
    netloc = host if (port is None) or (port == default_port) else f"{host}:{port}"
    return urllib.parse.urlunsplit((scheme, netloc, parts.path.rstrip("/"), parts.query, parts.fragment))


async def number_of_release_files(release: sql.Release) -> int:
    """Return the number of files in a release."""
    if (path := paths.release_directory_revision(release)) is None:
        return 0
    if (resolved_path := await is_dir_resolve(path)) is None:
        return 0
    count = 0
    async for rel_path in paths_recursive_all(resolved_path):
        abs_path_to_check = resolved_path / rel_path
        with contextlib.suppress(FileNotFoundError, OSError):
            if await aiofiles.os.path.isfile(abs_path_to_check):
                count += 1
    return count


def openpgp_member_ids(key: openpgp.PublicKey) -> set[str]:
    member_ids = {key.fingerprint.lower(), key.key_id.lower()}
    for binding in key.subkey_bindings():
        member_ids.add(binding.fingerprint.lower())
        member_ids.add(binding.key_id.lower())
    return member_ids


def parse_key_blocks(keys_text: str) -> list[str]:
    """Extract OpenPGP key blocks from a KEYS file."""
    key_blocks = []
    current_block = []
    in_key_block = False

    for line in keys_text.splitlines():
        if line.strip() == "-----BEGIN PGP PUBLIC KEY BLOCK-----":
            in_key_block = True
            current_block = [line]
        elif (line.strip() == "-----END PGP PUBLIC KEY BLOCK-----") and in_key_block:
            current_block.append(line)
            key_blocks.append("\n".join(current_block))
            in_key_block = False
        elif in_key_block:
            current_block.append(line)

    return key_blocks


def parse_key_blocks_bytes(keys_data: bytes) -> list[str]:
    """Extract OpenPGP key blocks from a KEYS file."""
    key_blocks = []
    current_block = []
    in_key_block = False

    for line in keys_data.splitlines():
        if line.strip() == b"-----BEGIN PGP PUBLIC KEY BLOCK-----":
            in_key_block = True
            current_block = [line]
        elif (line.strip() == b"-----END PGP PUBLIC KEY BLOCK-----") and in_key_block:
            current_block.append(line)
            key_blocks.append(b"\n".join(current_block))
            in_key_block = False
        elif in_key_block:
            current_block.append(line)

    return key_blocks


def parse_npm_pack_info(raw: bytes, filename_basename: str | None = None) -> tuple[NpmPackInfo | None, str | None]:
    """Parse npm pack info from package.json content."""
    parsed, error = _npm_pack_parse_package_json(raw)
    if (error is not None) or (parsed is None):
        return None, error

    name, version, error = _npm_pack_extract_name_version(parsed)
    if (error is not None) or (name is None) or (version is None):
        return None, error

    filename_match = _npm_pack_filename_match(filename_basename, name, version)
    return NpmPackInfo(name=name, version=version, filename_match=filename_match), None


async def paths_recursive(base_path: pathlib.Path | safe.StatePath) -> AsyncGenerator[safe.RelPath]:
    """Yield all file paths recursively within a base path, relative to the base path."""
    if (resolved_base_path := await is_dir_resolve(base_path)) is None:
        return
    async for rel_path in paths_recursive_all(base_path):
        abs_path_to_check = resolved_base_path / rel_path
        with contextlib.suppress(FileNotFoundError, OSError):
            if await aiofiles.os.path.isfile(abs_path_to_check):
                try:
                    yield safe.RelPath.from_path(rel_path)
                except ValueError as err:
                    msg = f"Unsafe relative path {str(rel_path)!r}: {err}"
                    raise ValueError(msg) from err


async def paths_recursive_all(base_path: os.PathLike) -> AsyncGenerator[pathlib.Path]:
    """Yield all file and directory paths recursively within a base path, relative to the base path."""
    if (resolved_base_path := await is_dir_resolve(base_path)) is None:
        return
    queue: list[pathlib.Path] = [resolved_base_path]
    visited_abs_paths: set[pathlib.Path] = set()
    while queue:
        current_abs_item = queue.pop(0)
        try:
            resolved_current_abs_item = await asyncio.to_thread(current_abs_item.resolve)
        except (FileNotFoundError, OSError):
            continue
        if resolved_current_abs_item in visited_abs_paths:
            continue
        visited_abs_paths.add(resolved_current_abs_item)
        with contextlib.suppress(FileNotFoundError, OSError):
            with await aiofiles.os.scandir(current_abs_item) as scan:
                entries = list(scan)
            for entry in entries:
                entry_abs_path = pathlib.Path(entry.path)
                relative_path = entry_abs_path.relative_to(resolved_base_path)
                yield relative_path
                if entry.is_dir():
                    queue.append(entry_abs_path)


def paths_to_inodes(directory: os.PathLike) -> dict[str, int]:
    directory = pathlib.Path(directory)
    result: dict[str, int] = {}
    stack: list[pathlib.Path] = [directory]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    rel_path = str(pathlib.Path(entry.path).relative_to(directory))
                    result[rel_path] = entry.stat(follow_symlinks=False).st_ino
                elif entry.is_dir(follow_symlinks=False):
                    stack.append(pathlib.Path(entry.path))
    return result


def permitted_announce_recipients(
    asf_uid: str, committee_key: str | None = None, *, project: sql.Project | None = None
) -> list[str]:
    if config.get().ATR_STATUS == "ALPHA":
        return [USER_TESTS_ADDRESS, f"{asf_uid}@apache.org"]
    recipients = ["announce@apache.org"]
    if committee_key is not None:
        recipients.extend(
            [
                f"announce@{committee_key}.apache.org",
                f"dev@{committee_key}.apache.org",
                f"user@{committee_key}.apache.org",
                f"private@{committee_key}.apache.org",
            ]
        )
    _add_policy_recipients(recipients, project, sql.RecipientAction.ANNOUNCE)
    return recipients


def permitted_archive_roots(basename_from_filename: str) -> list[str]:
    # TODO: Airavata uses "-source-release"
    for suffix in ARCHIVE_ROOT_SUFFIXES:
        if basename_from_filename.endswith(suffix):
            expected_root_base = basename_from_filename.removesuffix(suffix)
            return [expected_root_base, f"{expected_root_base}{suffix}"]
    return [basename_from_filename]


def permitted_podling_first_round_recipients(
    asf_uid: str, committee_key: str, *, is_podling: bool, project: sql.Project | None = None
) -> list[str]:
    recipients = permitted_voting_recipients(asf_uid, committee_key, project=project)
    if not is_podling:
        return recipients
    return [recipient for recipient in recipients if (recipient != f"private@{committee_key}.apache.org")]


def permitted_podling_second_round_recipients(asf_uid: str) -> list[str]:
    recipients = [INCUBATOR_GENERAL_ADDRESS]
    if config.get().ATR_STATUS == "ALPHA":
        recipients.append(USER_TESTS_ADDRESS)
        recipients.append(f"{asf_uid}@apache.org")
    return recipients


def permitted_voting_recipients(asf_uid: str, committee_key: str, *, project: sql.Project | None = None) -> list[str]:
    recipients = [
        f"dev@{committee_key}.apache.org",
        f"private@{committee_key}.apache.org",
    ]
    if config.get().ATR_STATUS == "ALPHA":
        recipients.append(USER_TESTS_ADDRESS)
        recipients.append(f"{asf_uid}@apache.org")
    _add_policy_recipients(recipients, project, sql.RecipientAction.VOTE)
    return recipients


def plural(count: int, singular: str, plural_form: str | None = None, *, include_count: bool = True) -> str:
    if plural_form is None:
        plural_form = singular + "s"
    word = singular if (count == 1) else plural_form
    if include_count:
        return f"{count} {word}"
    return word


def publication_check_url(
    committee: sql.Committee,
    suffix: safe.RelPath | None,
    kind: DownloadFile,
    filename: str | None = None,
) -> str:
    # Where a file published to the configured SVN target can be checked before announcement.
    relpath = paths.committee_dist_relpath(committee, suffix, filename)
    if svn_publish_target() is SvnPublishTarget.ATR:
        return f"{config.get().SVN_DIST_PUBLIC_URL.rstrip('/')}/{relpath}"
    return download_url_for_path(relpath, kind)


async def read_file_for_viewer(full_path: safe.StatePath, max_size: int) -> tuple[str | None, bool, bool, str | None]:
    """Read file content for viewer."""
    content: str | None = None
    is_text = False
    is_truncated = False
    error_message: str | None = None

    try:
        if not await aiofiles.os.path.exists(full_path):
            return None, False, False, "File does not exist"
        if not await aiofiles.os.path.isfile(full_path):
            return None, False, False, "Path is not a file"

        file_size = await aiofiles.os.path.getsize(full_path)
        read_size = min(file_size, max_size)

        if file_size > max_size:
            is_truncated = True

        if file_size == 0:
            is_text = True
            content = "(Empty file)"
            raw_content = b""
        else:
            async with aiofiles.open(full_path, "rb") as f:
                raw_content = await f.read(read_size)

        if file_size > 0:
            try:
                if b"\x00" in raw_content:
                    raise UnicodeDecodeError("utf-8", b"", 0, 1, "Null byte found")
                content = raw_content.decode("utf-8")
                is_text = True
            except UnicodeDecodeError:
                is_text = False
                content = _generate_hexdump(raw_content)

    except Exception as e:
        error_message = f"An error occurred reading the file: {e!s}"

    return content, is_text, is_truncated, error_message


async def session_cache_read() -> dict[str, dict]:
    cache_path = pathlib.Path(config.get().STATE_DIR) / "cache" / "user_session_cache.json"
    try:
        async with aiofiles.open(cache_path) as f:
            content = await f.read()
            return json.loads(content)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


async def session_cache_write(cache_data: dict[str, dict]) -> None:
    cache_path = pathlib.Path(config.get().STATE_DIR) / "cache" / "user_session_cache.json"
    await atomic_write_file(cache_path, json.dumps(cache_data, indent=2))


def static_path(*args: str) -> str:
    filename = str(pathlib.PurePosixPath(*args))
    return quart.url_for("static", filename=filename)


def static_url(filename: str) -> str:
    """Return the URL for a static file."""
    return quart.url_for("static", filename=filename)


def submitted_concerns_from_flash(flash_data: dict[str, Any]) -> list[str]:
    entry = flash_data.get("concerns_noted")
    if entry is None:
        entry = flash_data.get("!concerns_noted")
    if entry is None:
        return []
    original = entry.get("original")
    if isinstance(original, list):
        return [value for value in original if isinstance(value, str)]
    if isinstance(original, str):
        return [original]
    return []


def svn_publish_internal_url(
    committee: sql.Committee,
    download_path_suffix: safe.RelPath | None,
) -> str:
    publish_url = config.get().SVN_PUBLISH_URL
    if not publish_url:
        raise ValueError("SVN_PUBLISH_URL is not configured")
    relpath = paths.committee_dist_relpath(committee, download_path_suffix)
    return f"{publish_url.rstrip('/')}/{relpath}"


def svn_publish_target() -> SvnPublishTarget:
    public_path = urllib.parse.urlparse(config.get().SVN_DIST_PUBLIC_URL).path.rstrip("/")
    if public_path.endswith("/atr"):
        return SvnPublishTarget.ATR
    if public_path.endswith("/release"):
        return SvnPublishTarget.RELEASE
    raise ValueError("SVN_DIST_PUBLIC_URL must be an atr or release dist URL")


async def task_archive_url(task_mid: str, recipient: str | None = None) -> str | None:
    try:
        return await task_archive_url_strict(task_mid, recipient)
    except FetchError:
        log.exception(f"Failed to get archive URL for task {task_mid}")
        return None


async def task_archive_url_strict(task_mid: str, recipient: str | None = None) -> str | None:
    if "@" not in task_mid:
        return None

    if config.is_dev_environment() and (task_mid in DEV_THREAD_URLS):
        return DEV_THREAD_URLS[task_mid]

    recipient_address = recipient or USER_TESTS_ADDRESS
    lid = urllib.parse.quote(recipient_address.replace("@", "."))
    url = f"https://lists.apache.org/api/email.json?id=%3C{urllib.parse.quote(task_mid)}%3E&listid=%3C{lid}%3E"
    try:
        async with create_secure_session(timeout=LISTS_APACHE_TIMEOUT) as session:
            async with session.get(url) as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                email_data = await response.json()
    except Exception as exc:
        raise FetchError(f"Failed to look up archive URL for {task_mid}: {exc}", url=url) from exc
    mid = email_data.get("mid") if isinstance(email_data, dict) else None
    if not isinstance(mid, str):
        return None
    return "https://lists.apache.org/thread/" + urllib.parse.quote(mid)


async def thread_messages(  # noqa: C901
    thread_id: str,
    *,
    strict: bool = False,
    source: bool = False,
) -> AsyncGenerator[tuple[str, dict[str, Any]]]:
    """Iterate over mailing list thread messages in chronological order."""

    thread_url = f"https://lists.apache.org/api/thread.json?id={urllib.parse.quote(thread_id)}"

    try:
        async with create_secure_session(timeout=LISTS_APACHE_TIMEOUT) as session:
            async with session.get(thread_url) as resp:
                resp.raise_for_status()
                thread_data: Any = await resp.json(content_type=None)
    except Exception as exc:
        raise FetchError(f"Failed fetching thread metadata for {thread_id}: {exc}", url=thread_url) from exc

    message_ids: set[str] = set()

    if isinstance(thread_data, dict):
        for email_entry in thread_data.get("emails", []):
            if isinstance(email_entry, dict) and (mid := email_entry.get("id")):
                message_ids.add(str(mid))
        _thread_messages_walk(thread_data.get("thread"), message_ids)

    if not message_ids:
        return

    url_to_mid: dict[str, str] = {}
    if source:
        for mid in message_ids:
            source_url = f"https://lists.apache.org/api/source.lua?id={urllib.parse.quote(mid)}"
            url_to_mid[source_url] = mid
        email_urls = list(url_to_mid.keys())
    else:
        email_urls = [f"https://lists.apache.org/api/email.json?id={urllib.parse.quote(mid)}" for mid in message_ids]

    messages: list[dict[str, Any]] = []

    async for url, status, content in get_urls_as_completed(email_urls):
        if (status != 200) or (not content):
            if strict:
                if source:
                    raise FetchError(f"Failed to fetch email source from {url}: {status}", url=url)
                raise FetchError(f"Failed to fetch email data from {url}: {status}", url=url)
            if source:
                log.warning(f"Failed to fetch email source from {url}: {status}")
            else:
                log.warning(f"Failed to fetch email data from {url}: {status}")
            continue
        if source:
            try:
                msg_dict = _thread_message_from_source(url_to_mid[url], content)
                messages.append(msg_dict)
            except Exception as exc:
                if strict:
                    raise FetchError(f"Failed to parse email source from {url}: {exc}", url=url) from exc
                log.warning(f"Failed to parse email source from {url}: {exc}")
        else:
            try:
                msg_json = json.loads(content.decode())
                messages.append(msg_json)
            except Exception as exc:
                if strict:
                    raise FetchError(f"Failed to parse email JSON from {url}: {exc}", url=url) from exc
                log.warning(f"Failed to parse email JSON from {url}: {exc}")

    messages.sort(key=lambda m: m.get("epoch", 0))

    for msg_json in messages:
        msg_id = str(msg_json.get("id", ""))
        yield msg_id, msg_json


def unwrap[T](value: T | None, error_message: str = "unexpected None when unwrapping value") -> T:
    """
    Will unwrap the given value or raise a ValueError if it is None

    :param value: the optional value to unwrap
    :param error_message: the error message when failing to unwrap
    :return: the value or a ValueError if it is None
    """
    if value is None:
        raise ValueError(error_message)
    else:
        return value


def unwrap_type[T](value: T | None, t: type[T], error_message: str = "unexpected None when unwrapping value") -> T:
    """
    Will unwrap the given value or raise a TypeError if it is not of the expected type

    :param value: the optional value to unwrap
    :param t: the expected type of the value
    :param error_message: the error message when failing to unwrap
    """
    if value is None:
        raise ValueError(error_message)
    if not isinstance(value, t):
        raise ValueError(f"Expected {t}, got {type(value)}")
    return value


async def update_atomic_symlink(link_path: pathlib.Path, target_path: pathlib.Path | str) -> None:
    """Atomically update or create a symbolic link at link_path pointing to target_path."""
    target_str = str(target_path)

    # Generate a temporary path name for the new link
    link_dir = link_path.parent
    temp_link_path = link_dir / f".{link_path.name}.{uuid.uuid4()}.tmp"

    try:
        await aiofiles.os.symlink(target_str, temp_link_path)
        # Atomically rename the temporary link to the final link path
        # This overwrites link_path if it exists
        await aiofiles.os.rename(temp_link_path, link_path)
        log.info(f"Atomically updated symlink {link_path} -> {target_str}")
    except Exception as e:
        # Don't bother with log.exception here
        log.error(f"Failed to update atomic symlink {link_path} -> {target_str}: {e}")
        # Clean up temporary link if rename failed
        try:
            await aiofiles.os.remove(temp_link_path)
        except FileNotFoundError:
            # TODO: Use with contextlib.suppress(FileNotFoundError) for these sorts of blocks?
            pass
        raise


def user_releases(asf_uid: str, releases: Sequence[sql.Release]) -> list[sql.Release]:
    """Return a list of releases for which the user is a committee member or committer."""
    # TODO: This should probably be a session method instead
    user_releases = []
    for release in releases:
        if release.committee is None:
            continue
        if (asf_uid in release.committee.committee_members) or (asf_uid in release.committee.committers):
            user_releases.append(release)
    return user_releases


def validate_as_type[T](value: Any, t: type[T]) -> T:
    """Validate the given value as the given type."""
    if not isinstance(value, t):
        raise ValueError(f"Expected {t}, got {type(value)}")
    return value


def validate_distribution_owner_namespace(platform: sql.DistributionPlatform, namespace: Any):
    default_owner_namespace = platform.value.default_owner_namespace
    requires_owner_namespace = platform.value.requires_owner_namespace

    if requires_owner_namespace and (not namespace):
        raise ValueError(f'Platform "{platform.value.name}" requires an owner or namespace.', "owner_namespace")

    if (not requires_owner_namespace) and (not default_owner_namespace) and namespace:
        raise ValueError(f'Platform "{platform.value.name}" does not require an owner or namespace.', "owner_namespace")


def validate_email_recipients(recipients: EmailRecipients) -> None:
    if not recipients.email_to:
        raise ValueError("At least one To recipient is required")
    validate_no_duplicate_recipients([recipients.email_to, *recipients.email_cc, *recipients.email_bcc])


def validate_filename(filename: str) -> str:
    return validate_path_segment(filename, "Filename")


def validate_no_duplicate_recipients(addresses: list[str]) -> None:
    seen: set[str] = set()
    for address in addresses:
        lower = address.lower()
        if lower in seen:
            raise ValueError(f"Duplicate recipient: {address}")
        seen.add(lower)


def validate_path(path: pathlib.Path) -> pathlib.Path:
    for segment in path.parts:
        validate_path_segment(segment)
    return path


def validate_path_segment(path_segment: str, position: str = "Path segment") -> str:
    if not path_segment:
        raise ValueError(f"{position} cannot be empty")

    if "\0" in path_segment:
        raise ValueError(f"{position} cannot contain null bytes")

    if path_segment != unicodedata.normalize("NFC", path_segment):
        raise ValueError(f"{position} must be in Unicode Normalization Form C (NFC)")

    # TODO: Check relevant constants too?
    if ("/" in path_segment) or ("\\" in path_segment):
        raise ValueError(f"{position} cannot contain path separators")

    if ("<" in path_segment) or (">" in path_segment) or ("&" in path_segment):
        raise ValueError(f"{position} cannot contain markup characters")

    if path_segment in (".", ".."):
        raise ValueError(f"{position} cannot be a directory traversal")

    if path_segment in (".git", ".svn"):
        raise ValueError(f"{position} cannot be a SCM directory")

    if is_disallowed_dotfile(path_segment):
        raise ValueError(f"{position} cannot be a DOT file")

    return path_segment


def validate_trusted_publishing_constraints(
    github_repository_name: str | None,
    github_repository_branch: str | None,
    all_paths: list[str],
) -> None:
    if all_paths and (not github_repository_name):
        raise ValueError("GitHub repository name is required when any workflow path is set.")

    if github_repository_branch and (not github_repository_name):
        raise ValueError("GitHub repository name is required when a GitHub branch is set.")

    validation.validate_github_repository_name(github_repository_name)
    validation.validate_trusted_publishing_workflow_paths(all_paths)


def validate_vote_duration(duration: int):
    if duration == 0:
        return
    if duration > 0:
        if (duration < 72) or (duration > 144):
            raise ValueError("Vote duration must be 0 or between 72 and 144 hours inclusive.")


# TODO: AM put these rules into safe.versionkey
def version_key_error(version_key: str) -> str | None:
    """Check if the given version name is valid."""
    if version_key == "":
        return "Must not be empty"
    if version_key.lower() == "version":
        return "Must not be 'version'"
    if not re.match(r"^[a-zA-Z0-9]", version_key):
        return "Must start with a letter or number"
    if not re.search(r"[a-zA-Z0-9]$", version_key):
        return "Must end with a letter or number"
    if re.search(r"[+.-]{2,}", version_key):
        return "Must not contain multiple consecutive plus, full stop, or hyphen"
    if not re.match(r"^[a-zA-Z0-9+.-]+$", version_key):
        return "Must contain only letters, numbers, plus, full stop, or hyphen"
    return None


def version_sort_key(version: str) -> bytes:
    """
    Convert a version string into a sortable byte sequence.
    Prefixes each digit sequence with its length as u16 little-endian.
    Strips leading zeros and appends a byte for the count of leading zeros.
    """
    result = []
    i = 0
    length = len(version)
    while i < length:
        if version[i].isdigit():
            # Find the end of this digit sequence
            j = i
            while (j < length) and version[j].isdigit():
                j += 1

            digit_sequence = version[i:j]

            # Count leading zeros
            leading_zeros = 0
            for char in digit_sequence:
                if char == "0":
                    leading_zeros += 1
                else:
                    break

            # Strip leading zeros (but keep at least one digit if all zeros)
            stripped = digit_sequence.lstrip("0")

            # Count the stripped digits and encode as u16 little-endian
            digit_count = len(stripped)
            length_bytes = digit_count.to_bytes(2)

            # Add length prefix + stripped digits + leading zero count
            result.extend(length_bytes)
            result.extend(stripped.encode("utf-8"))
            result.append(leading_zeros)

            i = j
        else:
            # Non-digit character, just add it
            result.extend(version[i].encode("utf-8"))
            i += 1

    return bytes(result)


def warn_default_tls_settings_if_changed() -> None:
    ctx = ssl.create_default_context()
    current_cipher_names = tuple(cipher["name"] for cipher in ctx.get_ciphers())
    if (
        (ctx.check_hostname == EXPECTED_DEFAULT_TLS_CHECK_HOSTNAME)
        and (ctx.verify_mode == EXPECTED_DEFAULT_TLS_VERIFY_MODE)
        and (ctx.minimum_version == EXPECTED_DEFAULT_TLS_MINIMUM_VERSION)
        and (current_cipher_names == EXPECTED_DEFAULT_TLS_CIPHER_NAMES)
    ):
        return
    log.warning(
        "Python default TLS settings changed: "
        f"expected check_hostname={EXPECTED_DEFAULT_TLS_CHECK_HOSTNAME}, got {ctx.check_hostname}; "
        f"expected verify_mode={EXPECTED_DEFAULT_TLS_VERIFY_MODE}, got {ctx.verify_mode}; "
        f"expected minimum_version={EXPECTED_DEFAULT_TLS_MINIMUM_VERSION}, got {ctx.minimum_version}; "
        f"expected ciphers={EXPECTED_DEFAULT_TLS_CIPHER_NAMES}, got {current_cipher_names}"
    )


async def write_session(user_session: sql.UserSession) -> None:
    await session.areplace(user_session)
    quart.g._user_session = user_session


def _add_policy_recipients(recipients: list[str], project: sql.Project | None, action: sql.RecipientAction) -> None:
    # Append any recipients a project has configured in policy (eg via
    # .asf.yaml) that aren't already permitted, so a saved list is offered as a
    # choice and accepted when the email is sent.
    if project is None:
        return
    to, cc, bcc = project.policy_recipients(action)
    for address in (to, *cc, *bcc):
        if address and (address not in recipients):
            recipients.append(address)


def _asf_uid_from_email(email: str) -> str | None:
    ldap_params = ldap.SearchParameters(email_query=email)
    ldap.search(ldap_params)
    if not (ldap_params.results_list and ldap_params.results_list[0].uid):
        return None
    return ldap_params.results_list[0].uid[0]


async def _create_hard_link_clone_checks(
    source_dir: safe.StatePath,
    dest_dir: safe.StatePath,
    do_not_create_dest_dir: bool = False,
    dry_run: bool = False,
) -> None:
    if dry_run and (not do_not_create_dest_dir):
        raise ValueError("Cannot dry run and create destination directory")

    # Ensure source exists and is a directory
    if (not dry_run) and (not await aiofiles.os.path.isdir(source_dir)):
        raise ValueError(f"Source path is not a directory or does not exist: {source_dir}")

    # Create destination directory
    if do_not_create_dest_dir is False:
        await aiofiles.os.makedirs(dest_dir, exist_ok=True)


def _generate_hexdump(data: bytes) -> str:
    """Generate a formatted hexdump string from bytes."""
    hex_lines = []
    for i in range(0, len(data), 16):
        chunk = data[i : i + 16]
        hex_part = binascii.hexlify(chunk).decode("ascii")
        hex_part = hex_part.ljust(32)
        hex_part_spaced = " ".join(hex_part[j : j + 2] for j in range(0, len(hex_part), 2))
        ascii_part = "".join(chr(b) if (32 <= b < 127) else "." for b in chunk)
        line_num = f"{i:08x}"
        hex_lines.append(f"{line_num}  {hex_part_spaced}  |{ascii_part}|")
    return "\n".join(hex_lines)


def _npm_pack_extract_name_version(parsed: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    name = parsed.get("name")
    version = parsed.get("version")

    if (not isinstance(name, str)) or (not name.strip()):
        return None, None, "package/package.json missing or invalid 'name'"
    if (not isinstance(version, str)) or (not version.strip()):
        return None, None, "package/package.json missing or invalid 'version'"

    return name.strip(), version.strip(), None


def _npm_pack_filename_match(filename_basename: str | None, name: str, version: str) -> bool | None:
    if not filename_basename:
        return None
    if "/" in name:
        return None
    return filename_basename == f"{name}-{version}"


def _npm_pack_parse_package_json(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "package/package.json is not valid UTF-8"

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        return None, f"package/package.json is not valid JSON: {exc}"

    if not isinstance(parsed, dict):
        return None, "package/package.json is not a JSON object"

    return parsed, None


async def _propagation_probe(
    http_session: aiohttp.ClientSession,
    rel_path: str,
    public_url: str,
) -> PropagationOutcome:
    try:
        async with asyncio.timeout(PROPAGATION_PROBE_DEADLINE):
            async with http_session.head(public_url, allow_redirects=True) as resp:
                status = resp.status
                ok = 200 <= status < 300
                return PropagationOutcome(
                    rel_path=rel_path,
                    public_url=public_url,
                    ok=ok,
                    status=status,
                    error=None if ok else f"HTTP {status}",
                )
    except aiohttp.ClientError as exc:
        return PropagationOutcome(rel_path=rel_path, public_url=public_url, ok=False, status=None, error=str(exc))
    except TimeoutError as exc:
        return PropagationOutcome(
            rel_path=rel_path, public_url=public_url, ok=False, status=None, error=f"timeout: {exc}"
        )


def _propagation_public_url(public_base_url: str, rel_path: str) -> str:
    base = public_base_url.rstrip("/")
    quoted = urllib.parse.quote(rel_path)
    return f"{base}/{quoted}"


def _thread_message_from_source(mid: str, content: bytes) -> dict[str, Any]:
    parser = email.parser.BytesParser(policy=email.policy.default)
    message = parser.parsebytes(content)

    cc_pairs = email.utils.getaddresses(message.get_all("Cc", []))
    cc_combined = ",\n".join(
        (email.utils.formataddr((name, addr)) if name else f"<{addr}>") for name, addr in cc_pairs if (name or addr)
    )

    epoch: int = 0
    date_raw = message.get("Date")
    if date_raw:
        try:
            parsed_dt = email.utils.parsedate_to_datetime(str(date_raw))
        except (TypeError, ValueError):
            parsed_dt = None
        if parsed_dt is not None:
            try:
                epoch = int(parsed_dt.timestamp())
            except (OverflowError, OSError, ValueError):
                epoch = 0

    body_text = ""
    body_part = message.get_body(preferencelist=("plain",))
    if (body_part is None) and (not message.is_multipart()):
        body_part = message
    if body_part is not None:
        content_value = body_part.get_content()
        if isinstance(content_value, str):
            body_text = content_value

    return {
        "id": mid,
        "mid": mid,
        "from_raw": str(message.get("From", "")),
        "list_raw": str(message.get("List-Id", "")).strip().strip("<>"),
        "cc": cc_combined,
        "epoch": epoch,
        "subject": str(message.get("Subject", "")),
        "body": body_text,
        "message-id": str(message.get("Message-ID", "")),
        "date": str(message.get("Date", "")),
        "received_spf": [str(value) for value in message.get_all("Received-SPF", [])],
    }


def _thread_messages_walk(node: dict[str, Any] | None, message_ids: set[str]) -> None:
    if not isinstance(node, dict):
        return
    if mid := node.get("id"):
        message_ids.add(str(mid))
    for child in node.get("children", []):
        _thread_messages_walk(child, message_ids)
