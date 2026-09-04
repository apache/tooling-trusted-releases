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
import datetime
import pathlib
import re
import urllib.parse
from typing import Final, Self

import defusedxml.ElementTree as ElementTree
import pydantic
import pydantic_xml

import atr.config as config
import atr.constants as constants
import atr.log as log

ASF_TOOL: Final[str] = "atr"
EXPORT_TIMEOUT_SECONDS: Final[float] = 240.0
INFO_TIMEOUT_SECONDS: Final[float] = 30.0
KEYS_TIMEOUT_SECONDS: Final[float] = 60.0
LIST_TIMEOUT_SECONDS: Final[float] = 120.0
PUBLISH_TIMEOUT_SECONDS: Final[float] = 240.0
_COMMITTED_REVISION_RE: Final = re.compile(r"^Committed revision (\d+)\.\s*$", re.MULTILINE)
# svnmucc reports a commit as `r<N> committed by <author> at <date>`, a different
# shape from the `Committed revision <N>.` line svn import and commit emit.
_SVNMUCC_REVISION_RE: Final = re.compile(r"^r(\d+) committed by ", re.MULTILINE)
_CONNECTION_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {"E000110", "E000111", "E120108", "E170013", "E175002", "E175012"}
)
_ERROR_CODE_PRIORITY: Final[tuple[str, ...]] = (
    "E160020",
    "E215004",
    "E170001",
    "E000110",
    "E000111",
    "E120108",
    "E175012",
    "E175002",
    "E170013",
)
_ERROR_CODE_RE: Final = re.compile(r"\bE\d{6}\b")
# Codes svn emits when a path or URL genuinely isn't in the repository, as
# opposed to a connection or auth failure.
_MISSING_PATH_CODES: Final[frozenset[str]] = frozenset({"E160013", "E200009", "W160013", "W170000"})
_ERROR_SUMMARIES: Final[dict[str, str]] = {
    "E000110": "The connection to the SVN server was reset",
    "E000111": "The connection to the SVN server was refused",
    "E120108": "The SVN server unexpectedly closed the connection",
    "E160020": "A file already exists in the SVN target area",
    "E170001": "The SVN server rejected the ATR credentials",
    "E170013": "The SVN server could not be reached",
    "E175002": "The SVN server returned an unexpected HTTP response",
    "E175012": "The connection to the SVN server timed out",
    "E215004": "Authentication to the SVN server failed",
}
_STATUS_HINT: Final[str] = "If dist.apache.org is down, see https://status.apache.org/"


class CommandExecutionError(RuntimeError):
    returncode: int
    output: str

    def __init__(self, returncode: int, output: str) -> None:
        super().__init__(output)
        self.returncode = returncode
        self.output = output


class CommandTimeoutError(CommandExecutionError):
    timeout: float

    def __init__(self, timeout: float) -> None:
        super().__init__(-1, f"svn timed out after {int(timeout)} seconds")
        self.timeout = timeout


class SvnInfo(pydantic.BaseModel):
    """A dataclass to hold information about a file in a subversion repository."""

    path: str
    name: str | None = None
    url: str
    relative_url: str
    repository_root: str
    revision: str
    last_changed_author: str | None = None
    last_changed_rev: str
    last_changed_date: str
    checksum: str | None = None
    text_last_updated: str | None = None

    @property
    def revision_number(self) -> int:
        return int(self.revision)

    @property
    def last_changed_rev_number(self) -> int:
        return int(self.last_changed_rev)

    @classmethod
    async def from_url(cls, url: str) -> Self:
        output = await info_authenticated(url)

        nfo = {}
        for line in output.split("\n"):
            # TODO: Might break on IPv6 hosts, or hosts with a port?
            k, v = line.split(":", 1)
            nfo[k.replace(" ", "_").lower()] = v.strip()

        return cls.model_validate(nfo)

    @classmethod
    async def from_path(cls, path: pathlib.Path) -> Self:
        return await cls.from_url(str(path))


class SvnLogEntry(pydantic_xml.BaseXmlModel):
    revision: int = pydantic_xml.attr()
    author: str = pydantic_xml.element()
    date: str = pydantic_xml.element()
    msg: str | None = pydantic_xml.element(default=None)

    @property
    def datetime(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.date)


class SvnLog(pydantic_xml.BaseXmlModel, tag="log"):
    entries: list[SvnLogEntry] = pydantic_xml.element(tag="logentry")


async def commit(path: pathlib.Path, url: str, username: str, revision: str, message: str) -> str:
    log.debug(f"running svn commit for user '{username}'")
    # The username here is the ASF UID of the committer
    stdin_bytes = _authentication(url)
    return await _run_svnmucc_command(
        "put",
        str(path),
        url,
        "--username",
        username,
        "--password-from-stdin",
        "--non-interactive",
        "--with-revprop",
        f"asf:tool={ASF_TOOL}",
        "-r",
        revision,
        "-m",
        message,
        stdin_bytes=stdin_bytes,
    )


def error_message(exc: CommandExecutionError) -> str:
    if isinstance(exc, CommandTimeoutError):
        return f"The SVN operation timed out after {int(exc.timeout)} seconds. {_STATUS_HINT}"
    codes = set(_ERROR_CODE_RE.findall(exc.output))
    if code := next((candidate for candidate in _ERROR_CODE_PRIORITY if candidate in codes), None):
        summary = _ERROR_SUMMARIES[code]
        if code in _CONNECTION_ERROR_CODES:
            return f"{summary} ({code}). {_STATUS_HINT}"
        return f"{summary} ({code})"
    if detail := _sanitised_first_line(exc.output):
        return detail
    return f"svn exited with code {exc.returncode}"


async def export(
    url: str,
    revision: int | None,
    destination: pathlib.Path,
    timeout_seconds: float = EXPORT_TIMEOUT_SECONDS,
) -> None:
    stdin_bytes = _authentication(url)
    arguments = ["export", "--non-interactive", "--ignore-externals", "--ignore-keywords"]
    if revision is not None:
        arguments.extend(["-r", str(revision)])
        pegged_url = f"{url}@{revision}"
    else:
        pegged_url = f"{url}@"
    arguments.extend(["--username", ASF_TOOL, "--password-from-stdin"])
    arguments.extend(["--", pegged_url, str(destination)])
    await run_command("svn", *arguments, timeout_seconds=timeout_seconds, stdin_bytes=stdin_bytes)


async def get_diff(path: pathlib.Path, revision: int) -> str:
    log.debug(f"running svn diff for '{path}': r{revision}")
    svn_token = config.get().SVN_TOKEN
    if svn_token is None:
        raise ValueError("SVN_TOKEN must be set")
    # TODO: Or omit username entirely?
    return await _run_svn_command(
        "diff",
        str(path),
        "-c",
        str(revision),
        "--username",
        ASF_TOOL,
        "--password-from-stdin",
        "--non-interactive",
        stdin_bytes=svn_token.encode(),
    )


async def get_log(path: pathlib.Path) -> SvnLog:
    log.debug(f"running svn log for '{path}'")
    svn_token = config.get().SVN_TOKEN
    if svn_token is None:
        raise ValueError("SVN_TOKEN must be set")
    # TODO: Or omit username entirely?
    log_output = await _run_svn_command(
        "log",
        str(path),
        "--xml",
        "--username",
        ASF_TOOL,
        "--password-from-stdin",
        "--non-interactive",
        stdin_bytes=svn_token.encode(),
    )
    root = ElementTree.fromstring(log_output)
    return SvnLog.from_xml_tree(root)


async def info_authenticated(url: str) -> str:
    return await _run_svn_command(
        "info",
        url,
        "--username",
        ASF_TOOL,
        "--password-from-stdin",
        "--non-interactive",
        timeout_seconds=INFO_TIMEOUT_SECONDS,
        stdin_bytes=_authentication(url),
    )


async def list_files(url: str) -> list[str]:
    """List every file below a URL, as paths relative to it. Directories are left out."""
    output = await _run_svn_command(
        "list",
        url,
        "--recursive",
        "--username",
        ASF_TOOL,
        "--password-from-stdin",
        "--non-interactive",
        timeout_seconds=LIST_TIMEOUT_SECONDS,
        stdin_bytes=_authentication(url),
    )
    return [line for line in output.splitlines() if line and (not line.endswith("/"))]


def parse_committed_revision(output: str) -> int | None:
    if (match := _COMMITTED_REVISION_RE.search(output)) is None:
        return None
    return int(match.group(1))


def parse_svnmucc_revision(output: str) -> int | None:
    if (match := _SVNMUCC_REVISION_RE.search(output)) is None:
        return None
    return int(match.group(1))


def path_missing_error(exc: CommandExecutionError) -> bool:
    """Whether the error says the path or URL simply isn't in the repository.

    Matches only on svn's own error codes, never on free text: a connection or
    auth failure, or a timeout (which carries no code), reads as False and stays
    a real failure, since a transient error is no proof that a path has gone.
    """
    return any(code in exc.output for code in _MISSING_PATH_CODES)


async def publish_file(local_path: pathlib.Path, target_url: str, username: str, message: str) -> None:
    log.debug(f"running svnmucc put for user '{username}'")
    stdin_bytes = _authentication(target_url)
    await _run_svnmucc_command(
        "put",
        str(local_path),
        target_url,
        "--username",
        username,
        "--password-from-stdin",
        "--non-interactive",
        "--with-revprop",
        f"asf:tool={ASF_TOOL}",
        "-m",
        message,
        timeout_seconds=KEYS_TIMEOUT_SECONDS,
        stdin_bytes=stdin_bytes,
    )


async def publish_release(source_dir: pathlib.Path, target_url: str, username: str, message: str) -> int | None:
    log.debug(f"running svn import for user '{username}'")
    stdin_bytes = _authentication(target_url)
    output = await run_command(
        "svn",
        "import",
        str(source_dir),
        target_url,
        "--username",
        username,
        "--password-from-stdin",
        "--non-interactive",
        "--no-auth-cache",
        "--no-ignore",
        "--config-option",
        "config:miscellany:enable-auto-props=no",
        "--with-revprop",
        f"asf:tool={ASF_TOOL}",
        "-m",
        message,
        timeout_seconds=PUBLISH_TIMEOUT_SECONDS,
        stdin_bytes=stdin_bytes,
    )
    revision = parse_committed_revision(output)
    if revision is None:
        log.warning(f"svn import did not emit a Committed revision line; output={output!r}")
    return revision


async def publish_revision_matches(info: SvnInfo, author: str, message: str) -> bool:
    stdin_bytes = _authentication(info.url)
    revision = info.last_changed_rev_number
    log_output = await _run_svn_command(
        "log",
        info.url,
        "--xml",
        "--verbose",
        "-r",
        str(revision),
        "--username",
        ASF_TOOL,
        "--password-from-stdin",
        "--non-interactive",
        timeout_seconds=INFO_TIMEOUT_SECONDS,
        stdin_bytes=stdin_bytes,
    )
    root = ElementTree.fromstring(log_output)
    entries = root.findall("logentry")
    if len(entries) != 1:
        return False
    entry = entries[0]
    if entry.get("revision") != str(revision):
        return False
    if config.svn_publish_kind() is config.SvnPublishKind.ASF_DISTRIBUTION:
        if entry.findtext("author") != author:
            return False
    if entry.findtext("msg") != message:
        return False
    target_path = info.relative_url.removeprefix("^")
    created_paths = {(path.text or "").strip() for path in entry.findall("./paths/path") if path.get("action") == "A"}
    if target_path not in created_paths:
        return False
    tool = await _run_svn_command(
        "propget",
        info.url,
        "--revprop",
        "-r",
        str(revision),
        "asf:tool",
        "--username",
        ASF_TOOL,
        "--password-from-stdin",
        "--non-interactive",
        timeout_seconds=INFO_TIMEOUT_SECONDS,
        stdin_bytes=stdin_bytes,
    )
    return tool.strip() == ASF_TOOL


async def remove_files(base_url: str, rel_paths: list[str], username: str, message: str) -> str:
    log.debug(f"running svnmucc rm for user '{username}'")
    stdin_bytes = _authentication(base_url)
    actions: list[str] = []
    for rel_path in rel_paths:
        actions.extend(["rm", f"{base_url}/{rel_path}"])
    return await _run_svnmucc_command(
        *actions,
        "--username",
        username,
        "--password-from-stdin",
        "--non-interactive",
        "--with-revprop",
        f"asf:tool={ASF_TOOL}",
        "-m",
        message,
        timeout_seconds=PUBLISH_TIMEOUT_SECONDS,
        stdin_bytes=stdin_bytes,
    )


async def run_command(
    cmd: str, *args: str, timeout_seconds: float | None = None, stdin_bytes: bytes | None = None
) -> str:
    """Run a svn command asynchronously.

    Arguments:
        cmd (str): the command to run
        *args (str): arguments to pass to the command
    """
    proc = await asyncio.create_subprocess_exec(
        cmd,
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    communicate = asyncio.create_task(proc.communicate(stdin_bytes))
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communicate), timeout_seconds)
    except TimeoutError:
        await _terminate_process(proc, communicate)
        timeout = timeout_seconds if timeout_seconds is not None else 0.0
        raise CommandTimeoutError(timeout)
    except asyncio.CancelledError:
        await asyncio.shield(_terminate_process(proc, communicate))
        raise

    # if the proc.communicate() call returns an error
    # print the error out and return an empty string.
    if proc.returncode:
        raise CommandExecutionError(proc.returncode, stderr.decode())
    else:
        output = stdout.decode().strip()
        return output


async def update(path: pathlib.Path) -> str:
    log.debug(f"running svn update for '{path}'")
    return await _run_svn_command("update", str(path), "--parents")


def _authentication(url: str) -> bytes:
    svn_token = config.get().SVN_TOKEN
    if svn_token is None:
        raise ValueError("SVN_TOKEN must be set")
    if not _is_trusted_url(url):
        raise ValueError(f"Refusing to send SVN credentials to {url}")
    return svn_token.encode()


def _is_trusted_url(url: str) -> bool:
    target = urllib.parse.urlsplit(url)
    if target.query or target.fragment:
        return False
    for base in (config.get().SVN_PUBLISH_URL, constants.SVN_DIST_ROOT_URL):
        if not base:
            continue
        trusted = urllib.parse.urlsplit(base)
        if (target.scheme.lower(), target.netloc.lower()) != (trusted.scheme.lower(), trusted.netloc.lower()):
            continue
        trusted_path = trusted.path.rstrip("/")
        if (target.path == trusted_path) or target.path.startswith(trusted_path + "/"):
            return True
    return False


async def _run_svn_command(
    sub_cmd: str, path: str, *args: str, timeout_seconds: float | None = None, stdin_bytes: bytes | None = None
) -> str:
    # Do not log this command, as it may contain a password or secret token
    return await run_command("svn", *[sub_cmd, *args, path], timeout_seconds=timeout_seconds, stdin_bytes=stdin_bytes)


async def _run_svnmucc_command(
    *args: str, timeout_seconds: float | None = None, stdin_bytes: bytes | None = None
) -> str:
    return await run_command("svnmucc", *args, timeout_seconds=timeout_seconds, stdin_bytes=stdin_bytes)


def _sanitised_first_line(output: str) -> str:
    lines = output.strip().splitlines()
    if not lines:
        return ""
    line = lines[0].strip()
    if publish_url := config.get().SVN_PUBLISH_URL:
        line = line.replace(publish_url.rstrip("/"), "")
    if len(line) > 200:
        line = line[:197] + "..."
    return line


async def _terminate_process(
    proc: asyncio.subprocess.Process,
    communicate: asyncio.Task[tuple[bytes, bytes]],
) -> None:
    try:
        proc.terminate()
    except ProcessLookupError:
        ...
    try:
        await asyncio.wait_for(asyncio.shield(communicate), 5)
        return
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            ...
    await communicate
