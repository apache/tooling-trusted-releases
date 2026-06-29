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

"""Date-format compiler for calendar-versioned projects.

A calver project declares the shape of its version strings as a date format
rather than a raw regex. The format is a sequence of fixed tokens with literal
separators between them:

    YYYY / YY   four- or two-digit year
    MM / M      zero-padded or 1-2 digit month
    DD / D      zero-padded or 1-2 digit day
    N           a numeric serial (a patch ordinal within a calendar line)

A single parenthesised span marks which part forms the cycle key. Trailing
tokens are optional, so one format covers mixed-granularity histories: for
example YYYYMMDD matches both 20130710 and 201407.

The format compiles two ways. cycle_regex renders the cycle span as capture
group 1, for storing in Project.cycle_match so ordinary cycle resolution
handles membership with no calver-specific code. order_key renders a
named-group regex and extracts a comparable (year, month, day, serial) tuple
for ordering releases within a cycle.
"""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Sequence

# Longest tokens first so YYYY wins over YY and MM over M during the scan.
_TOKENS: Final[list[tuple[str, str, str]]] = [
    ("YYYY", r"\d{4}", "year"),
    ("YY", r"\d{2}", "year"),
    ("MM", r"\d{2}", "month"),
    ("DD", r"\d{2}", "day"),
    ("M", r"\d{1,2}", "month"),
    ("D", r"\d{1,2}", "day"),
    ("N", r"\d+", "serial"),
]

_CALENDAR_ROLES: Final[frozenset[str]] = frozenset({"year", "month", "day"})


class _Atom(NamedTuple):
    regex: str  # the regex fragment matching this token or literal
    role: str | None  # the ordering role, or None for a literal separator
    in_cycle: bool  # whether it sits inside the parenthesised cycle span


def cycle_regex(date_format: str) -> str | None:
    """Compile the cycle span to a regex with the cycle as group 1.

    Returns None when the format has no parenthesised span - a linear calver
    project that keeps a single default cycle, for which `cycle_match` stays
    unset.
    """
    atoms = _parse(date_format)
    cycle_indices = [i for i, atom in enumerate(atoms) if atom.in_cycle]
    if not cycle_indices:
        return None
    first, last = cycle_indices[0], cycle_indices[-1]
    before = "".join(atom.regex for atom in atoms[:first])
    span = "".join(atom.regex for atom in atoms[first : last + 1])
    tail = _nested_optional(_coalesce(atoms[last + 1 :]))
    return f"^{before}({span}){tail}$"


def order_key(date_format: str, version: str) -> tuple[int, int, int, int] | None:
    """Resolve a version to a `(year, month, day, serial)` ordering tuple.

    Absent fields read as 0, so a coarser version sorts below a finer one in
    the same period. Returns None when the version doesn't fit the format.
    """
    match = _order_pattern(date_format).fullmatch(version)
    if match is None:
        return None
    fields = match.groupdict()

    def field(name: str) -> int:
        captured = fields.get(name)
        return int(captured) if captured else 0

    return (field("year"), field("month"), field("day"), field("serial"))


def validate(date_format: str) -> None:
    """Raise ValueError if the format is malformed or carries no calendar field."""
    if not date_format.strip():
        raise ValueError("Calver format is empty")
    atoms = _parse(date_format)
    roles = [atom.role for atom in atoms if atom.role is not None]
    if not any(role in _CALENDAR_ROLES for role in roles):
        raise ValueError("Calver format needs at least one of YYYY/YY, MM/M or DD/D")
    if len(roles) != len(set(roles)):
        raise ValueError("Calver format repeats a field")
    # Compile both regexes now, so an invalid format is caught here rather than
    # when we first come to resolve a version against it.
    re.compile(_order_regex(date_format))
    compiled_cycle = cycle_regex(date_format)
    if compiled_cycle is not None:
        re.compile(compiled_cycle)


def _coalesce(atoms: Sequence[_Atom]) -> list[str]:
    # Fold each separator run onto the token that follows it, so a separator
    # and its token become optional together rather than independently.
    chunks: list[str] = []
    pending = ""
    for atom in atoms:
        if atom.role is None:
            pending += atom.regex
            continue
        chunks.append(pending + atom.regex)
        pending = ""
    if pending:
        chunks.append(pending)
    return chunks


def _nested_optional(chunks: list[str]) -> str:
    if not chunks:
        return ""
    return f"(?:{chunks[0]}{_nested_optional(chunks[1:])})?"


@functools.lru_cache(maxsize=256)
def _order_pattern(date_format: str) -> re.Pattern[str]:
    return re.compile(_order_regex(date_format))


def _order_regex(date_format: str) -> str:
    chunks: list[str] = []
    pending = ""
    for atom in _parse(date_format):
        if atom.role is None:
            pending += atom.regex
            continue
        chunks.append(f"{pending}(?P<{atom.role}>{atom.regex})")
        pending = ""
    if pending:
        chunks.append(pending)
    if not chunks:
        raise ValueError("Calver format has no fields")
    # The leading field is required and the rest optional, which is what lets
    # one format match both coarse and fine versions.
    return f"^{chunks[0]}{_nested_optional(chunks[1:])}$"


@functools.lru_cache(maxsize=256)
def _parse(date_format: str) -> tuple[_Atom, ...]:
    atoms: list[_Atom] = []
    in_cycle = False
    seen_cycle = False
    i = 0
    while i < len(date_format):
        char = date_format[i]
        if char == "(":
            if seen_cycle:
                raise ValueError("Calver format may contain at most one (cycle) span")
            in_cycle = True
            seen_cycle = True
            i += 1
            continue
        if char == ")":
            if not in_cycle:
                raise ValueError("Calver format has an unmatched )")
            in_cycle = False
            i += 1
            continue
        token = _match_token(date_format, i)
        if token is not None:
            fragment, role, length = token
            atoms.append(_Atom(fragment, role, in_cycle))
            i += length
            continue
        atoms.append(_Atom(re.escape(char), None, in_cycle))
        i += 1
    if in_cycle:
        raise ValueError("Calver format has an unmatched (")
    return tuple(atoms)


def _match_token(date_format: str, at: int) -> tuple[str, str, int] | None:
    for token, fragment, role in _TOKENS:
        if date_format.startswith(token, at):
            return fragment, role, len(token)
    return None
