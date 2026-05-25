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
import collections
import dataclasses
import ssl
from typing import Final, Literal

import ldap3
import ldap3.utils.conv as conv
import ldap3.utils.dn as dn

import atr.models.schema as schema

LDAP_ROOT_BASE: Final[str] = "cn=infrastructure-root,ou=groups,ou=services,dc=apache,dc=org"
LDAP_SEARCH_BASE: Final[str] = "ou=people,dc=apache,dc=org"
LDAP_SERVER_HOST: Final[str] = "ldap-eu.apache.org"
LDAP_TOOLING_BASE: Final[str] = "cn=tooling,ou=groups,ou=services,dc=apache,dc=org"

RESULT_ATTRIBUTES: Final[list[str]] = [
    "asf-altEmail",
    "asf-banned",
    "asf-committer-email",
    "cn",
    "mail",
    "member",
    "memberUid",
    "uid",
]


class PubSubAttributes(schema.Subset):
    """LDAP attributes as they appear in pubsub old_attributes/new_attributes."""

    asf_alt_email: list[str] = schema.Field(default_factory=list, alias="asf-altEmail")
    asf_banned: list[str] = schema.Field(default_factory=list, alias="asf-banned")
    asf_committer_email: list[str] = schema.Field(default_factory=list, alias="asf-committer-email")
    mail: list[str] = schema.Field(default_factory=list)
    uid: list[str] = schema.Field(default_factory=list)


class PubSubPayload(schema.Subset):
    """An LDAP change event from the ASF pubsub stream."""

    dn: str
    change_type: str
    old_attributes: PubSubAttributes = schema.Field(default_factory=PubSubAttributes)
    new_attributes: PubSubAttributes = schema.Field(default_factory=PubSubAttributes)


class Result(schema.Strict):
    model_config = schema.pydantic.ConfigDict(
        extra="forbid", strict=True, validate_assignment=True, populate_by_name=True
    )

    dn: str
    asf_alt_email: list[str] = schema.Field(default_factory=list, alias="asf-altEmail")
    asf_banned: list[str] = schema.Field(default_factory=list, alias="asf-banned")
    asf_committer_email: list[str] = schema.Field(default_factory=list, alias="asf-committer-email")
    cn: list[str] = schema.Field(default_factory=list)
    mail: list[str] = schema.Field(default_factory=list)
    member: list[str] = schema.Field(default_factory=list)
    member_uid: list[str] = schema.Field(default_factory=list, alias="memberUid")
    uid: list[str] = schema.Field(default_factory=list)


_tls_config = ldap3.Tls(
    validate=ssl.CERT_REQUIRED,
)


class Search:
    def __init__(self, ldap_bind_dn: str, ldap_bind_password: str):
        self._bind_dn = ldap_bind_dn
        self._bind_password = ldap_bind_password
        self._conn: ldap3.Connection | None = None

    def __enter__(self):
        server = ldap3.Server(LDAP_SERVER_HOST, use_ssl=True, tls=_tls_config)
        self._conn = ldap3.Connection(
            server,
            user=self._bind_dn,
            password=self._bind_password,
            auto_bind=True,
            check_names=False,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn and self._conn.bound:
            self._conn.unbind()

    def search(
        self,
        ldap_base: str,
        ldap_scope: Literal["BASE", "LEVEL", "SUBTREE"],
        ldap_query: str = "(objectClass=*)",
        ldap_attrs: list[str] | None = None,
    ) -> list[Result]:
        if not self._conn:
            raise RuntimeError("LDAP connection not available")

        attributes = ldap_attrs if ldap_attrs else RESULT_ATTRIBUTES
        self._conn.search(
            search_base=ldap_base,
            search_filter=ldap_query,
            search_scope=ldap_scope,
            attributes=attributes,
        )
        results: list[Result] = []
        for entry in self._conn.entries:
            results.append(Result.model_validate({"dn": entry.entry_dn, **entry.entry_attributes_as_dict}))
        return results


class LookupError(Exception):
    pass


# We use a dataclass to support ldap3.Connection objects
@dataclasses.dataclass
class SearchParameters:
    uid_query: str | None = None
    email_query: str | None = None
    github_username_query: str | None = None
    github_nid_query: int | None = None
    bind_dn_from_config: str | None = None
    bind_password_from_config: str | None = None
    results_list: list[Result] = dataclasses.field(default_factory=list)
    err_msg: str | None = None
    srv_info: str | None = None
    detail_err: str | None = None
    connection: ldap3.Connection | None = None


async def account_lookup(asf_uid: str) -> Result | None:
    """
    Look up an account in LDAP by ASF UID.

    Returns the account details if found, None if the account does not exist.
    If LDAP is not configured, returns None to avoid breaking functionality.
    """
    credentials = get_bind_credentials()
    if credentials is None:
        return None

    bind_dn, bind_password = credentials
    params = SearchParameters(
        uid_query=asf_uid,
        bind_dn_from_config=bind_dn,
        bind_password_from_config=bind_password,
    )
    await asyncio.to_thread(search, params)

    if not params.results_list:
        return None

    return params.results_list[0]


async def fetch_admin_users() -> frozenset[str]:
    import atr.log as log

    credentials = get_bind_credentials()
    if credentials is None:
        log.warning("LDAP bind DN or password not configured, returning empty admin set")
        return frozenset()

    bind_dn, bind_password = credentials

    def _query_ldap() -> frozenset[str]:
        users: set[str] = set()
        with Search(bind_dn, bind_password) as ldap_search:
            for base in (LDAP_ROOT_BASE, LDAP_TOOLING_BASE):
                try:
                    result = ldap_search.search(ldap_base=base, ldap_scope="BASE")
                    if (not result) or (len(result) != 1):
                        continue
                    for member_dn in result[0].member:
                        parsed = parse_dn(member_dn)
                        uids = parsed.get("uid", [])
                        if uids:
                            users.add(uids[0])
                except Exception as e:
                    log.warning(f"Failed to query LDAP group {base}: {e}")
        return frozenset(users)

    return await asyncio.to_thread(_query_ldap)


async def fetch_tooling_users(extra: set[str]) -> set[str]:
    import atr.log as log

    credentials = get_bind_credentials()
    if credentials is None:
        log.warning("LDAP bind DN or password not configured, returning extra tooling users only")
        return extra

    bind_dn, bind_password = credentials

    def _query_ldap() -> set[str]:
        users: set[str] = set()
        with Search(bind_dn, bind_password) as ldap_search:
            for base in (LDAP_TOOLING_BASE,):
                try:
                    result = ldap_search.search(ldap_base=base, ldap_scope="BASE")
                    if (not result) or (len(result) != 1):
                        continue
                    for member_dn in result[0].member:
                        parsed = parse_dn(member_dn)
                        uids = parsed.get("uid", [])
                        if uids:
                            users.add(uids[0])
                except Exception as e:
                    log.warning(f"Failed to query LDAP group {base}: {e}")
        return users

    tooling = await asyncio.to_thread(_query_ldap)
    return tooling | extra


def get_bind_credentials() -> tuple[str, str] | None:
    import atr.config as config

    conf = config.get()
    if conf.LDAP_BIND_DN and conf.LDAP_BIND_PASSWORD:
        return (conf.LDAP_BIND_DN, conf.LDAP_BIND_PASSWORD)
    return None


async def github_to_apache(github_numeric_uid: int) -> str:
    import atr.config as config

    # We need to lookup the ASF UID from the GitHub NID
    conf = config.get()
    bind_dn = conf.LDAP_BIND_DN
    bind_password = conf.LDAP_BIND_PASSWORD
    ldap_params = SearchParameters(
        bind_dn_from_config=bind_dn,
        bind_password_from_config=bind_password,
        github_nid_query=github_numeric_uid,
    )
    await asyncio.to_thread(search, ldap_params)
    if not (ldap_params.results_list and ldap_params.results_list[0].uid):
        raise LookupError(f"GitHub NID {github_numeric_uid} not registered with the ATR")
    return ldap_params.results_list[0].uid[0]


async def handle_update(payload: dict) -> None:
    import atr.cache as cache
    import atr.log as log

    try:
        parsed = PubSubPayload.model_validate(payload)
    except schema.pydantic.ValidationError:
        log.warning(f"Failed to parse LDAP pubsub payload with DN: {payload.get('dn', '<missing>')}")
        return

    uid = _extract_uid_from_pubsub(parsed)
    if uid is None:
        log.debug(f"Ignoring LDAP pubsub event with no uid: {parsed.dn}")
        return

    was_banned = bool(parsed.old_attributes.asf_banned)
    now_banned = bool(parsed.new_attributes.asf_banned)

    if now_banned and (not was_banned):
        log.info(f"LDAP pubsub: user {uid} has been deactivated")
        log.auth_event("account_deactivated", uid)
        await _revoke_all_credentials(uid, log)
    elif was_banned and (not now_banned):
        log.info(f"LDAP pubsub: user {uid} has been reactivated")
        log.auth_event("account_reactivated", uid)

    change_type = parsed.change_type.lower()
    if change_type == "delete":
        if cache.email_uid_purge_uid(uid):
            await cache.email_uid_save_current_to_file()
            log.info(f"LDAP pubsub: purged email cache entries for deleted user {uid}")
        return

    old_emails = _emails_from_pubsub(parsed.old_attributes)
    new_emails = _emails_from_pubsub(parsed.new_attributes)
    if cache.email_uid_apply_delta(uid, old_emails, new_emails):
        await cache.email_uid_save_current_to_file()
        log.info(f"LDAP pubsub: applied email cache delta for {uid}")


async def is_active(asf_uid: str) -> bool:
    import atr.config as config

    if config.is_test_mode():
        if asf_uid == "test":
            return True
        if asf_uid == "test-banned":
            return False
    if get_bind_credentials() is None and not config.is_production_mode():
        return True
    account = await account_lookup(asf_uid)
    if account is None:
        return False
    return not is_banned(account)


def is_banned(account: Result) -> bool:
    # In ASF LDAP, non banned accounts do not carry this attribute
    # Therefore, we treat any present value as banned
    return bool(account.asf_banned)


def parse_dn(dn_string: str) -> dict[str, list[str]]:
    parsed = collections.defaultdict(list)
    parts = dn.parse_dn(dn_string)
    for attr, value, _ in parts:
        parsed[attr].append(value)
    return dict(parsed)


def search(params: SearchParameters) -> None:
    try:
        _search_core(params)
    except Exception as e:
        params.err_msg = f"An unexpected error occurred: {e!s}"
        params.detail_err = f"Details: {e.args}"
    finally:
        if params.connection and params.connection.bound:
            try:
                params.connection.unbind()
            except Exception:
                ...


def _emails_from_pubsub(attributes: PubSubAttributes) -> list[str]:
    emails: list[str] = []
    for email in attributes.mail:
        if email:
            emails.append(email)
    for email in attributes.asf_alt_email:
        if email:
            emails.append(email)
    for email in attributes.asf_committer_email:
        if email:
            emails.append(email)
    return emails


def _extract_uid_from_pubsub(payload: PubSubPayload) -> str | None:
    """Extract the ASF UID from a pubsub payload, preferring new_attributes.uid then the DN."""
    if payload.new_attributes.uid:
        return payload.new_attributes.uid[0]
    parsed = parse_dn(payload.dn)
    uids = parsed.get("uid", [])
    if uids:
        return uids[0]
    return None


async def _revoke_all_credentials(uid: str, log) -> None:
    """Revoke all sessions, PATs, and SSH keys for a banned user."""
    import asfquart
    import sqlmodel

    import atr.db as db
    import atr.models.sql as sql

    session_count = await asfquart.APP.sessions.revoke_by_uid(uid)
    if session_count > 0:
        log.info(f"LDAP pubsub: revoked {session_count} session(s) for banned user {uid}")
        log.auth_event("sessions_revoked", uid)

    async with db.session() as data:
        # OR on created_by so a banned admin's system PATs go too.
        via = sql.validate_instrumented_attribute
        stmt = sqlmodel.select(sql.PersonalAccessToken).where(
            sqlmodel.or_(
                via(sql.PersonalAccessToken.asfuid) == uid,
                via(sql.PersonalAccessToken.created_by) == uid,
            )
        )
        tokens = list((await data.execute(stmt)).scalars().all())
        for token in tokens:
            await data.delete(token)

        ssh_result = await data.execute(sqlmodel.select(sql.SSHKey).where(sql.SSHKey.asf_uid == uid))
        ssh_keys = list(ssh_result.scalars().all())
        for ssh_key in ssh_keys:
            await data.delete(ssh_key)

        if tokens or ssh_keys:
            await data.commit()

    if tokens:
        log.info(f"LDAP pubsub: revoked {len(tokens)} PAT(s) for banned user {uid}")
        log.auth_event("tokens_revoked", uid)
    if ssh_keys:
        log.info(f"LDAP pubsub: revoked {len(ssh_keys)} SSH key(s) for banned user {uid}")
        log.auth_event("ssh_keys_revoked", uid)


def _search_core(params: SearchParameters) -> None:
    params.results_list = []
    params.err_msg = None
    params.srv_info = None
    params.detail_err = None
    params.connection = None

    server = ldap3.Server(LDAP_SERVER_HOST, use_ssl=True, tls=_tls_config, get_info=ldap3.ALL)
    params.srv_info = repr(server)

    if params.bind_dn_from_config and params.bind_password_from_config:
        params.connection = ldap3.Connection(
            server,
            user=params.bind_dn_from_config,
            password=params.bind_password_from_config,
            auto_bind=True,
            check_names=False,
        )
    else:
        params.connection = ldap3.Connection(server, auto_bind=True, check_names=False)

    filters: list[str] = []
    if params.uid_query:
        if params.uid_query == "*":
            filters.append("(uid=*)")
        else:
            filters.append(f"(uid={conv.escape_filter_chars(params.uid_query)})")

    if params.email_query:
        escaped_email = conv.escape_filter_chars(params.email_query)
        if params.email_query.endswith("@apache.org"):
            filters.append(f"(mail={escaped_email})")
        else:
            filters.append(f"(asf-altEmail={escaped_email})")

    if params.github_username_query:
        filters.append(f"(asf-githubStringID={conv.escape_filter_chars(params.github_username_query)})")

    if params.github_nid_query:
        filters.append(f"(asf-githubNumericID={params.github_nid_query})")

    if not filters:
        params.err_msg = "Please provide a UID, an email address, or a GitHub username to search."
        return

    _search_core_2(params, filters)


def _search_core_2(params: SearchParameters, filters: list[str]) -> None:
    search_filter = f"(&{''.join(filters)})" if (len(filters) > 1) else filters[0]

    if not params.connection:
        params.err_msg = "LDAP Connection object not established or auto_bind failed."
        return

    params.connection.search(
        search_base=LDAP_SEARCH_BASE,
        search_filter=search_filter,
        attributes=RESULT_ATTRIBUTES,
    )
    for entry in params.connection.entries:
        params.results_list.append(Result.model_validate({"dn": entry.entry_dn, **entry.entry_attributes_as_dict}))

    if (not params.results_list) and (not params.err_msg):
        params.err_msg = "No results found for the given criteria."
