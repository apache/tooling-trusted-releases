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
import dataclasses
import datetime
import email.utils
import re
from collections.abc import AsyncIterator, Generator, Sequence
from typing import Any, Final, Literal, Protocol

import atr.cache as cache
import atr.config as config
import atr.db as db
import atr.db.interaction as interaction
import atr.models as models
import atr.models.sql as sql
import atr.user as user
import atr.util as util

MAX_THREAD_MESSAGES: Final[int] = 10000

_NON_BINDING_STATUSES: Final = frozenset(
    {
        models.tabulate.VoteStatus.FRACTIONAL,
        models.tabulate.VoteStatus.COMMITTER,
        models.tabulate.VoteStatus.CONTRIBUTOR,
    }
)

# A signed decimal such as "+0.5" or "-.5", but not a version number such as "+1.86.0"
_FRACTIONAL_VOTE_RE: Final = re.compile(r"(?:^|\s)([+-]\d*\.\d+)(?!\.?\d)")

SPF_ENVELOPE_FROM_RE: Final = re.compile(
    r"(?:^|[;\s])envelope-from\s*=\s*(\"[^\"]*\"|<[^>]*>|[^;\s]+)",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True, kw_only=True)
class ConfirmableEmail:
    asf_eid: str
    email_timestamp: datetime.datetime
    voter_asf_uid: str | None
    voter_email: str
    voter_fullname: str


class CountedVoteEntryData(Protocol):
    @property
    def choice(self) -> sql.VoteChoice: ...

    @property
    def confirmed_by(self) -> str | None: ...

    @property
    def is_binding(self) -> bool: ...

    @property
    def source(self) -> Literal["atr", "email"]: ...

    @property
    def voter_asf_uid(self) -> str | None: ...

    @property
    def voter_email(self) -> str | None: ...


class CountedVoteSummaryData(Protocol):
    @property
    def binding_votes(self) -> int: ...

    @property
    def binding_votes_abstain(self) -> int: ...

    @property
    def binding_votes_no(self) -> int: ...

    @property
    def binding_votes_yes(self) -> int: ...

    @property
    def non_binding_votes(self) -> int: ...

    @property
    def non_binding_votes_abstain(self) -> int: ...

    @property
    def non_binding_votes_no(self) -> int: ...

    @property
    def non_binding_votes_yes(self) -> int: ...


def all_ballots_yes(summary: CountedVoteSummaryData) -> bool:
    return (
        summary.binding_votes_no
        + summary.binding_votes_abstain
        + summary.non_binding_votes_no
        + summary.non_binding_votes_abstain
    ) == 0


def binding_vote_passes(binding_plus_one: int, binding_minus_one: int) -> bool:
    return (binding_plus_one >= 3) and (binding_plus_one > binding_minus_one)


async def email_for_confirmation(
    thread_id: str,
    asf_eid: str,
    *,
    excluded_message_ids: set[str] | None = None,
) -> ConfirmableEmail:
    """Resolve sender identity and capped UTC email timestamp for a specific thread message."""
    _validate_thread_id(thread_id)
    if not asf_eid.strip():
        raise interaction.InteractionError("An archive message ID is required")
    try:
        email_to_uid = await cache.email_uid_view_or_live()
    except Exception as exc:
        raise interaction.InteractionError(f"email-to-UID lookup unavailable for confirmation: {exc}") from exc

    target = _message_id_normalize(asf_eid)
    excluded_normalized = {_message_id_normalize(mid) for mid in (excluded_message_ids or ()) if mid}
    async for msg in _thread_vote_messages(thread_id):
        if _message_id_normalize(msg.get("mid", "")) != target:
            continue
        if _is_vote_root(msg, thread_id):
            raise interaction.InteractionError("The opening vote message cannot be confirmed as a ballot")
        if _message_id_normalize(msg.get("message-id", "")) in excluded_normalized:
            raise interaction.InteractionError("An ATR ballot receipt cannot be confirmed as an email vote")
        ok, name, from_email_lower, asf_uid = _message_identity(msg, email_to_uid)
        if not ok:
            raise interaction.InteractionError("Could not identify the sender of the email vote")
        return ConfirmableEmail(
            asf_eid=str(msg.get("mid", "")) or asf_eid,
            email_timestamp=_email_timestamp(msg),
            voter_asf_uid=asf_uid,
            voter_email=from_email_lower,
            voter_fullname=name or asf_uid or from_email_lower.split("@", 1)[0],
        )
    raise interaction.InteractionError(f"Message {asf_eid} was not found in thread {thread_id}")


async def vote_committee(thread_id: str, release: sql.Release) -> sql.Committee | None:
    committee = release.project.committee
    if config.is_dev_environment():
        message_count = 0
        async for _mid, msg in util.thread_messages(thread_id, strict=True):
            message_count += 1
            if message_count > MAX_THREAD_MESSAGES:
                raise ValueError(f"Thread exceeds maximum of {MAX_THREAD_MESSAGES} messages")
            list_raw = msg.get("list_raw", "")
            committee_label = list_raw.split(".apache.org", 1)[0].split(".", 1)[-1]
            async with db.session() as data:
                committee = await data.committee(key=committee_label).get()
            break
    elif (committee is not None) and committee.is_podling and (release.podling_thread_id is not None):
        async with db.session() as data:
            committee = await data.committee(key="incubator").get()
    return committee


async def vote_details(
    committee: sql.Committee | None,
    thread_id: str,
    release: sql.Release,
    *,
    excluded_message_ids: set[str] | None = None,
) -> models.tabulate.VoteDetails:
    interpretations = await interaction.vote_interpretations(release.key, release.current_vote_seq)
    start_unixtime, tabulated_votes = await votes(
        committee,
        thread_id,
        excluded_message_ids=excluded_message_ids,
        interpretations={interpretation.asf_eid: interpretation for interpretation in interpretations},
    )
    summary = vote_summary(tabulated_votes)
    return models.tabulate.VoteDetails(
        start_unixtime=start_unixtime,
        votes=tabulated_votes,
        summary=summary,
    )


def vote_result_subject(release: sql.Release, resolution: str) -> str:
    return f"[RESULT] [VOTE] Release {release.project.display_name} {release.version} {resolution.upper()}"


def vote_summary(tabulated_votes: dict[str, models.tabulate.VoteEmail]) -> dict[str, int]:
    result = {
        "binding_votes": 0,
        "binding_votes_yes": 0,
        "binding_votes_no": 0,
        "binding_votes_abstain": 0,
        "non_binding_votes": 0,
        "non_binding_votes_yes": 0,
        "non_binding_votes_no": 0,
        "non_binding_votes_abstain": 0,
        "unknown_votes": 0,
        "unknown_votes_yes": 0,
        "unknown_votes_no": 0,
        "unknown_votes_abstain": 0,
    }
    for vote_email in tabulated_votes.values():
        _vote_summary_add(result, vote_email)
    return result


def vote_tally_block(
    summary: CountedVoteSummaryData,
    binding_label: str = "Binding",
    non_binding_label: str = "Non-binding",
    thread_id: str | None = None,
    podling_thread_id: str | None = None,
    *,
    ballots: Sequence[CountedVoteEntryData] = (),
) -> str:
    return "\n".join(
        _vote_tally_block(
            summary,
            binding_label,
            non_binding_label,
            thread_id,
            podling_thread_id,
            ballots=ballots,
        )
    )


async def votes(
    committee: sql.Committee | None,
    thread_id: str,
    *,
    excluded_message_ids: set[str] | None = None,
    interpretations: dict[str, sql.VoteInterpretation | sql.VoteChoice] | None = None,
) -> tuple[int | None, dict[str, models.tabulate.VoteEmail]]:
    """Tabulate heuristic and confirmed mailing-list replies, excluding ATR receipt messages."""
    _validate_thread_id(thread_id)
    excluded_normalized = {_message_id_normalize(mid) for mid in (excluded_message_ids or ()) if mid}
    try:
        email_to_uid = await cache.email_uid_view_or_live()
    except Exception as exc:
        raise ValueError(f"email-to-UID lookup unavailable for tabulation: {exc}") from exc

    start_unixtime = None
    tabulated_votes: dict[str, models.tabulate.VoteEmail] = {}
    async for msg in _thread_vote_messages(thread_id):
        if start_unixtime is None:
            start_unixtime = _parse_epoch(msg.get("epoch"))
        if "[RESULT]" in msg.get("subject", ""):
            break
        if _is_vote_root(msg, thread_id) or (_message_id_normalize(msg.get("message-id", "")) in excluded_normalized):
            continue
        interpretation = (interpretations or {}).get(msg.get("mid", ""))
        await _tabulate_message(msg, committee, email_to_uid, interpretation, tabulated_votes)
    return start_unixtime, tabulated_votes


def _email_timestamp(msg: dict[str, Any]) -> datetime.datetime:
    received = msg.get("received")
    parsed = None
    if isinstance(received, list) and received:
        _trace, separator, date = str(received[0]).rpartition(";")
        if separator:
            parsed = _parse_mail_date(date.strip())
    if parsed is None:
        parsed = _parse_mail_date(msg.get("date", ""), allow_iso=True)
    if parsed is None:
        epoch = _parse_epoch(msg.get("epoch"))
        if epoch is not None:
            try:
                parsed = datetime.datetime.fromtimestamp(epoch, datetime.UTC)
            except OverflowError, OSError, ValueError:
                pass
    now = datetime.datetime.now(datetime.UTC)
    return min(parsed or now, now)


def _is_vote_root(msg: dict[str, Any], thread_id: str) -> bool:
    mid = _message_id_normalize(str(msg.get("mid") or ""))
    subject = str(msg.get("subject") or "")
    return (mid == _message_id_normalize(thread_id)) or subject.lstrip().startswith("[VOTE]")


def _message_id_normalize(message_id: str) -> str:
    return message_id.strip().removeprefix("<").removesuffix(">")


def _message_identity(msg: dict[str, Any], email_to_uid: cache.EmailUidLookup) -> tuple[bool, str, str, str | None]:
    received_spf = msg.get("received_spf", [])
    return _vote_identity(
        msg.get("from_raw", ""),
        email_to_uid,
        msg.get("list_raw", ""),
        msg.get("cc", "").split(",\n"),
        received_spf if isinstance(received_spf, list) else [],
    )


def _name_from_raw(from_raw: str) -> str:
    angle = from_raw.find("<")
    if angle <= 0:
        return ""
    name = from_raw[:angle].strip().strip('"')
    via_index = name.find(" via ")
    if via_index >= 0:
        name = name[:via_index]
    return name.strip()


def _parse_epoch(value: object) -> int | None:
    if isinstance(value, bool) or (not isinstance(value, (int, float, str))):
        return None
    try:
        return int(value)
    except ValueError, OverflowError:
        return None


def _parse_mail_date(value: str, *, allow_iso: bool = False) -> datetime.datetime | None:
    if (not isinstance(value, str)) or (not value):
        return None
    try:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except ValueError:
            if not allow_iso:
                return None
            parsed = datetime.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=datetime.UTC)
        return parsed.astimezone(datetime.UTC)
    except TypeError, ValueError, OverflowError:
        return None


def _received_spf_envelope_from(received_spf: list[object]) -> str | None:
    for value in received_spf:
        candidate = _spf_candidate(value)
        if candidate is not None:
            return candidate
    return None


def _spf_candidate(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = SPF_ENVELOPE_FROM_RE.search(value)
    if match is None:
        return None
    candidate = match.group(1).strip().strip('"')
    if candidate.startswith("<") and candidate.endswith(">"):
        candidate = candidate[1:-1]
    candidate = candidate.rstrip(";").strip().lower()
    if (not candidate) or (candidate == "<>") or ("@" not in candidate) or any(ch.isspace() for ch in candidate):
        return None
    return candidate


async def _tabulate_message(
    msg: dict[str, Any],
    committee: sql.Committee | None,
    email_to_uid: cache.EmailUidLookup,
    interpretation: sql.VoteInterpretation | sql.VoteChoice | None,
    tabulated_votes: dict[str, models.tabulate.VoteEmail],
) -> None:
    ok, name, from_email_lower, asf_uid = _message_identity(msg, email_to_uid)
    if not ok:
        return
    castings = _vote_castings(msg.get("body", ""))
    if (not castings) and (interpretation is None):
        return

    status = models.tabulate.VoteStatus.CONTRIBUTOR
    if asf_uid is not None:
        status = await _vote_status(asf_uid, msg.get("list_raw", ""), committee)
    unixtime = _parse_epoch(msg.get("epoch"))
    fraction = None
    confirmed_by = None
    if interpretation is None:
        vote_cast = castings[0][0] if (len(castings) == 1) else models.tabulate.Vote.UNKNOWN
        fraction = _vote_fraction(castings)
        if (fraction is not None) and (status == models.tabulate.VoteStatus.BINDING):
            status = models.tabulate.VoteStatus.FRACTIONAL
    else:
        if isinstance(interpretation, sql.VoteInterpretation):
            choice = interpretation.choice
            confirmed_by = interpretation.confirmed_by
            if unixtime is None:
                unixtime = int(interpretation.email_timestamp.timestamp())
            if interpretation.is_binding_at_confirm:
                status = models.tabulate.VoteStatus.BINDING
        else:
            choice = interpretation
        vote_cast = models.tabulate.Vote[choice.name]

    asf_uid_or_email = asf_uid if asf_uid is not None else from_email_lower
    tabulated_votes[asf_uid_or_email] = models.tabulate.VoteEmail(
        name=name,
        asf_uid_or_email=asf_uid_or_email,
        from_email=from_email_lower,
        status=status,
        asf_eid=msg.get("mid", ""),
        iso_datetime=msg.get("date", ""),
        vote=vote_cast,
        fraction=fraction,
        quotation=" // ".join(line for _vote, line in castings),
        updated=asf_uid_or_email in tabulated_votes,
        asf_uid=asf_uid,
        unixtime=unixtime,
        confirmed=interpretation is not None,
        confirmed_by=confirmed_by,
    )


async def _thread_vote_messages(thread_id: str) -> AsyncIterator[dict[str, Any]]:
    count = 0
    async for _archive_id, msg in util.thread_messages(thread_id, strict=True, source=True):
        count += 1
        if count > MAX_THREAD_MESSAGES:
            raise ValueError(f"Thread exceeds maximum of {MAX_THREAD_MESSAGES} messages")
        yield msg


def _validate_thread_id(thread_id: str) -> None:
    if (not thread_id) or (not isinstance(thread_id, str)):
        raise ValueError(f"Invalid thread_id: {thread_id}")
    if not re.match(r"^[a-zA-Z0-9]{32}$", thread_id):
        raise ValueError(f"Invalid thread_id format: {thread_id}")


def _vote_break(line: str) -> bool:
    if line == "-- ":
        return True
    if line.startswith("On ") and (line[6:8] == ", "):
        return True
    if line.startswith("From: "):
        return True
    if line.startswith("________"):
        return True
    return False


def _vote_castings(body: str) -> list[tuple[models.tabulate.Vote, str]]:
    castings: list[tuple[models.tabulate.Vote, str]] = []
    for line in body.split("\n"):
        if _vote_continue(line):
            continue
        if _vote_break(line):
            break
        casting = _vote_line_casting(line)
        if casting is not None:
            castings.append((casting, line))
    return castings


def _vote_continue(line: str) -> bool:
    explanation_indicators = (
        "[ ] +1",
        "[ ] +0",
        "[ ] -0",
        "[ ] -1",
        "+1 votes",
        "-1 votes",
    )
    if any((indicator in line) for indicator in explanation_indicators):
        return True
    return line.startswith(">")


def _vote_fraction(castings: list[tuple[models.tabulate.Vote, str]]) -> str | None:
    if len(castings) != 1:
        return None
    match = _FRACTIONAL_VOTE_RE.search(castings[0][1])
    return match.group(1) if (match is not None) else None


def _vote_identity(
    from_raw: str,
    email_to_uid: cache.EmailUidLookup,
    list_email: str,
    cc: list[str],
    received_spf: list[object],
) -> tuple[bool, str, str, str | None]:
    from_email_lower = util.email_from_uid(from_raw)
    if not from_email_lower:
        return False, "", "", None
    name = _name_from_raw(from_raw)
    from_email_lower = from_email_lower.removesuffix(".invalid")
    if from_email_lower.endswith("@apache.org"):
        return True, name, from_email_lower, from_email_lower.split("@")[0]
    from_email_lower = _vote_via_address(from_raw, from_email_lower, list_email, cc, received_spf)
    return True, name, from_email_lower, email_to_uid.get(from_email_lower)


def _vote_line_casting(line: str) -> models.tabulate.Vote | None:
    fractional = _FRACTIONAL_VOTE_RE.search(line) is not None
    whole = _FRACTIONAL_VOTE_RE.sub(" ", line)
    plus_one = whole.startswith("+1") or (" +1" in whole) or ("/+1" in whole)
    minus_one = whole.startswith("-1") or (" -1" in whole) or ("/-1" in whole)
    zero = (whole in {"0", "-0", "+0"}) or whole.startswith(("0 ", "+0 ", "-0 "))
    if sum((plus_one, minus_one, zero, fractional)) > 1:
        return None
    if plus_one:
        return models.tabulate.Vote.YES
    if minus_one:
        return models.tabulate.Vote.NO
    if zero or fractional:
        return models.tabulate.Vote.ABSTAIN
    return None


async def _vote_status(asf_uid: str, list_raw: str, committee: sql.Committee | None) -> models.tabulate.VoteStatus:
    status = models.tabulate.VoteStatus.UNKNOWN
    if config.is_dev_environment():
        committee_label = list_raw.split(".apache.org", 1)[0].split(".", 1)[-1]
        async with db.session() as data:
            committee = await data.committee(key=committee_label).get()
    if committee is not None:
        if asf_uid in committee.committee_members:
            status = models.tabulate.VoteStatus.BINDING
        elif asf_uid in committee.committers:
            status = models.tabulate.VoteStatus.COMMITTER
        else:
            status = models.tabulate.VoteStatus.CONTRIBUTOR
    return status


def _vote_summary_add(result: dict[str, int], vote_email: models.tabulate.VoteEmail) -> None:
    if vote_email.status == models.tabulate.VoteStatus.BINDING:
        prefix = "binding_votes"
    elif vote_email.status in _NON_BINDING_STATUSES:
        prefix = "non_binding_votes"
    else:
        prefix = "unknown_votes"
    result[prefix] += 1
    if vote_email.vote == models.tabulate.Vote.YES:
        result[f"{prefix}_yes"] += 1
    elif vote_email.vote == models.tabulate.Vote.NO:
        result[f"{prefix}_no"] += 1
    elif vote_email.vote == models.tabulate.Vote.ABSTAIN:
        result[f"{prefix}_abstain"] += 1


def _vote_tally_block(
    summary: CountedVoteSummaryData,
    binding_label: str,
    non_binding_label: str,
    thread_id: str | None,
    podling_thread_id: str | None,
    *,
    ballots: Sequence[CountedVoteEntryData] = (),
) -> Generator[str]:
    if podling_thread_id:
        yield "The previous round of voting is archived at the following URL:"
        yield ""
        yield f"https://lists.apache.org/thread/{podling_thread_id}"
        yield ""

    if thread_id is not None:
        yield "The vote thread is archived at the following URL:"
        yield ""
        yield f"https://lists.apache.org/thread/{thread_id}"
        yield ""

    if ballots:
        yield from _vote_tally_entry_lines(ballots, binding_label, non_binding_label)
        yield ""

    yield f"There were {summary.binding_votes} {user.binding_label_inline(binding_label)} votes."
    yield (
        f"{binding_label} votes: +1: {summary.binding_votes_yes}, "
        f"0: {summary.binding_votes_abstain}, -1: {summary.binding_votes_no}."
    )
    yield (
        f"{non_binding_label} votes: +1: {summary.non_binding_votes_yes}, "
        f"0: {summary.non_binding_votes_abstain}, -1: {summary.non_binding_votes_no}."
    )


def _vote_tally_entry_lines(
    ballots: Sequence[CountedVoteEntryData],
    binding_label: str,
    non_binding_label: str,
) -> Generator[str]:
    binding_inline = user.binding_label_inline(binding_label)
    non_binding_inline = non_binding_label.lower()
    for entry in ballots:
        voter = entry.voter_asf_uid or entry.voter_email or "unknown"
        potency = binding_inline if entry.is_binding else non_binding_inline
        provenance = "" if (entry.source == "atr") else ", email confirmed"
        yield f"{entry.choice.value} {voter} ({potency}{provenance})"


def _vote_via_address(
    from_raw: str,
    from_email_lower: str,
    list_email: str,
    cc: list[str],
    received_spf: list[object],
) -> str:
    if ("via" not in from_raw) or (from_email_lower.replace("@", ".") not in list_email):
        return from_email_lower
    envelope_from = _received_spf_envelope_from(received_spf)
    if envelope_from is not None:
        return envelope_from
    if cc:
        return util.email_from_uid(cc[-1]) or from_email_lower
    return from_email_lower
