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

import urllib.parse as parse
from typing import Any, Final

import re2

from . import safe

MAX_IGNORE_PATTERN_LENGTH: Final[int] = 128
MAX_PROJECT_PATTERN_LENGTH: Final[int] = 128
RE2_MAX_MEM: Final[int] = 1 << 20

# Positive per-field allowlists of URI schemes ATR will store and later render as a link. Each keeps
# out schemes that execute in the browser (javascript:, data:) while admitting only what the field
# legitimately carries: repositories are reached over the web or a VCS locator, whereas a standard is
# a page a reader visits
REPOSITORY_URI_SCHEMES: Final[frozenset[str]] = frozenset(
    {"http", "https", "git", "git+ssh", "git+https", "ssh", "svn"}
)
STANDARD_URI_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


def compile_ignore_pattern(pattern: str):
    # TODO: This requires importing re2 in atr/models
    # We want to avoid such dependencies
    # But if we move this out, we can't do full validation in the models
    if len(pattern) > MAX_IGNORE_PATTERN_LENGTH:
        raise ValueError(f"Pattern exceeds {MAX_IGNORE_PATTERN_LENGTH} characters")
    if pattern.startswith("^") or pattern.endswith("$"):
        regex_pattern = pattern
    else:
        regex_pattern = re2.escape(pattern).replace(r"\*", ".*")
        # Should maybe add .replace(r"\?", ".?")
    try:
        return re2.compile(regex_pattern, options=_pattern_options(captures=False))
    except re2.error as exc:
        raise ValueError(f"Invalid ignore pattern: {_pattern_error(exc)}") from exc


def compile_project_pattern(pattern: str, captures: bool = False):
    if len(pattern) > MAX_PROJECT_PATTERN_LENGTH:
        raise ValueError(f"Pattern exceeds {MAX_PROJECT_PATTERN_LENGTH} characters")
    try:
        return re2.compile(pattern, options=_pattern_options(captures=captures))
    except re2.error as exc:
        raise ValueError(_pattern_error(exc)) from exc


def pagination_args_validate(query_args: Any) -> None:
    # Users could request any amount using limit=N with arbitrarily high N
    # We therefore limit the maximum limit to 1000
    if hasattr(query_args, "limit"):
        limit = query_args.limit
        if limit > 1000:
            raise ValueError("Maximum limit of 1000 exceeded")
        elif limit < 1:
            raise ValueError("Minimum limit less than 1 is nonsense")
    # Users could request any amount using offset=N with arbitrarily high N
    # We therefore limit the maximum offset to 1000000
    if hasattr(query_args, "offset"):
        offset = query_args.offset
        if offset > 1000000:
            raise ValueError("Maximum offset of 1000000 exceeded")
        elif offset < 0:
            raise ValueError("Minimum offset less than 0 is nonsense")


def uri_scheme_allowed(uri: str, allowed_schemes: frozenset[str]) -> bool:
    # Case folded, since urlsplit keeps the scheme as written and schemes are case insensitive
    return parse.urlsplit(uri).scheme.lower() in allowed_schemes


def validate_announce_recipients(recipients: list[str]) -> None:
    for recipient in recipients:
        domain = _email_domain(recipient)
        if (domain != "apache.org") and (not domain.endswith(".apache.org")):
            raise ValueError(f"Announce recipient '{recipient}' must be an apache.org address.")


def validate_download_path_suffix(template: str) -> None:
    resolved = template.strip()
    if not resolved:
        return
    # The template is filled in per release, so probe with placeholder stand-ins
    # to check the result is a path we'd accept
    probe = resolved.replace("{{MAJOR_VERSION}}", "x").replace("{{PROJECT_KEY}}", "x").replace("{{VERSION}}", "x")
    try:
        safe.RelPath(probe)
    except ValueError as e:
        raise ValueError(f"Download path suffix is not a valid path: {e}") from e


def validate_github_repository_name(github_repository_name: str | None) -> None:
    if github_repository_name and ("/" in github_repository_name):
        raise ValueError("GitHub repository name must not contain a slash.")


def validate_ignore_pattern(pattern: str) -> None:
    """Raise an exception if the pattern is invalid."""
    if pattern == "!":
        return
    raw_pattern = pattern
    if raw_pattern.startswith("!"):
        raw_pattern = raw_pattern[1:]
    compile_ignore_pattern(raw_pattern)


def validate_policy_min_hours(min_hours: int) -> None:
    if (min_hours != 0) and ((min_hours < 72) or (min_hours > 168)):
        raise ValueError("Minimum voting period must be 0 or between 72 and 168 hours inclusive.")


def validate_security_contact(committee_key: str, security_contact: str | None) -> None:
    if not security_contact:
        return
    allowed = {"security@apache.org", f"security@{committee_key}.apache.org"}
    if security_contact not in allowed:
        raise ValueError(
            f"Security contact '{security_contact}' must be 'security@apache.org' "
            f"or 'security@{committee_key}.apache.org'."
        )


def validate_trusted_publishing_workflow_paths(paths: list[str]) -> None:
    for path in paths:
        if not path.startswith(".github/workflows/"):
            raise ValueError("GitHub workflow paths must start with '.github/workflows/'.")


def validate_uri_list(uris: list[str], allowed_schemes: frozenset[str]) -> None:
    disallowed = [uri for uri in uris if not uri_scheme_allowed(uri, allowed_schemes)]
    if disallowed:
        raise ValueError(
            f"URI{'s' if (len(disallowed) > 1) else ''} with a disallowed or missing scheme: {', '.join(disallowed)}"
        )


def validate_vote_recipients(committee_key: str, recipients: list[str]) -> None:
    expected = f"{committee_key}.apache.org"
    for recipient in recipients:
        if _email_domain(recipient) != expected:
            raise ValueError(f"Vote recipient '{recipient}' must be on '{expected}'.")


def _email_domain(address: str) -> str:
    return address.rpartition("@")[2].lower()


def _pattern_error(exc: re2.error) -> str:
    message = exc.args[0] if exc.args else "invalid pattern"
    if isinstance(message, bytes):
        return message.decode("utf-8", "replace")
    return str(message)


def _pattern_options(captures: bool) -> re2.Options:
    options = re2.Options()
    options.max_mem = RE2_MAX_MEM
    options.log_errors = False
    options.never_capture = not captures
    return options
