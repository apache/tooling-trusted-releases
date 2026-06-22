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

"""SSH server module for ATR."""

import asyncio
import asyncio.subprocess
import collections
import datetime
import glob
import os
import stat
import string
import time
from typing import Final

import aiofiles
import aiofiles.os
import asyncssh
import asyncssh.encryption as encryption
import asyncssh.kex as kex
import asyncssh.mac as mac
import ssh_audit.builtin_policies as builtin_policies

import atr.attestable as attestable
import atr.config as config
import atr.db as db
import atr.ldap as ldap
import atr.log as log
import atr.models.github as github
import atr.models.safe as safe
import atr.models.sql as sql
import atr.paths as paths
import atr.storage as storage
import atr.storage.datatypes as datatypes
import atr.user as user
import atr.util as util

_CONFIG: Final = config.get()
_SSH_AUDIT_POLICY: Final = builtin_policies.BUILTIN_POLICIES["Hardened OpenSSH Server v9.9 (version 1)"]
_ASYNCSSH_SUPPORTED_ENC: Final = {bytes(a) for a in encryption.get_encryption_algs()}
_ASYNCSSH_SUPPORTED_KEX: Final = {bytes(a) for a in kex.get_kex_algs()}
_ASYNCSSH_SUPPORTED_MAC: Final = {bytes(a) for a in mac.get_mac_algs()}
_APPROVED_CIPHERS: Final = util.intersect_algs(_SSH_AUDIT_POLICY, "ciphers", _ASYNCSSH_SUPPORTED_ENC)
_APPROVED_KEX: Final = util.intersect_algs(_SSH_AUDIT_POLICY, "kex", _ASYNCSSH_SUPPORTED_KEX)
_APPROVED_MACS: Final = util.intersect_algs(_SSH_AUDIT_POLICY, "macs", _ASYNCSSH_SUPPORTED_MAC)

_PATH_ALPHANUM: Final = frozenset(string.ascii_letters + string.digits + "-")
# From a survey of version numbers we find that only . and - are used
# We also allow + which is in common use
_PATH_VERSION_CHARS: Final = _PATH_ALPHANUM | frozenset(".-+")

_RATE_LIMIT_IP: Final = 100
_RATE_LIMIT_USER: Final = 10
_RATE_WINDOW: Final = 60.0
_RSYNC_TIMEOUT: Final = 90 * 60
_RSYNC_MAX_UPLOAD_SIZE: Final = 2_000_000_000

# Keyed by IP address; catches all connections including failed auth
global_ip_rate_buckets: dict[str, collections.deque[float]] = {}
# Keyed by ASF UID; only incremented on successful authentication
global_user_rate_buckets: dict[str, collections.deque[float]] = {}


class RsyncArgsError(Exception):
    """Exception raised when the rsync arguments are invalid."""

    pass


class SSHServer(asyncssh.SSHServer):
    """Simple SSH server that handles connections."""

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        """Called when a connection is established."""
        self._conn = conn
        self._github_asf_uid: str | None = None
        self._github_payload: github.TrustedPublisherPayload | None = None
        peer_addr = conn.get_extra_info("peername")[0]
        log.info(f"SSH connection received from {peer_addr}")
        if not _rate_limit_check(global_ip_rate_buckets, peer_addr, _RATE_LIMIT_IP):
            log.warning(f"IP rate limit exceeded for {peer_addr}")
            conn.disconnect(asyncssh.DISC_TOO_MANY_CONNECTIONS, "Rate limit exceeded")  # type: ignore[reportPrivateImportUsage]
            return

    def connection_lost(self, exc: Exception | None) -> None:
        """Called when a connection is lost or closed."""
        if exc:
            log.error(f"SSH connection error: {exc}")
        else:
            log.info("SSH connection closed")

    async def begin_auth(self, username: str) -> bool:
        """Begin authentication for the specified user."""
        log.info(f"Beginning auth for user {username}")

        if username == "github":
            log.info("GitHub authentication will use validate_public_key")
            return True

        if not await ldap.is_active(username):
            log.auth_failure("ssh", "ssh_account_disabled", username)
            raise asyncssh.PermissionDenied("Account disabled")

        try:
            # Load SSH keys for this user from the database
            async with db.session() as data:
                user_keys = await data.ssh_key(asf_uid=username).all()

                if not user_keys:
                    log.warning(f"No SSH keys found for user: {username}")
                    log.auth_failure("ssh", "no_keys_found", username)
                    # Still require authentication, but it will fail
                    return True

                # Create an authorized_keys file as a string
                auth_keys_lines = []
                for user_key in user_keys:
                    auth_keys_lines.append(user_key.key)

                auth_keys_data = "\n".join(auth_keys_lines)
                log.info(f"Loaded {len(user_keys)} SSH keys for user {username}")

                # Set the authorized keys in the connection
                try:
                    authorized_keys = asyncssh.import_authorized_keys(auth_keys_data)
                    self._conn.set_authorized_keys(authorized_keys)
                    log.info(f"Successfully set authorized keys for {username}")
                except Exception as e:
                    log.error(f"Error setting authorized keys: {e}")

        except Exception as e:
            log.error(f"Database error loading SSH keys: {e}")

        # Always require authentication
        return True

    def auth_completed(self) -> None:
        username = self._conn.get_extra_info("username")
        auth_type = "ssh"
        if username == "github":
            auth_type = "githubssh"
            username = self._github_asf_uid
        if username and not _rate_limit_check(global_user_rate_buckets, username, _RATE_LIMIT_USER):
            log.warning(f"User rate limit exceeded for {username}")
            log.auth_failure(auth_type, "rate_limit_exceeded", username)
            self._conn.disconnect(asyncssh.DISC_TOO_MANY_CONNECTIONS, "Rate limit exceeded")  # type: ignore[reportPrivateImportUsage]
            return
        log.auth_success(auth_type, username)

    def public_key_auth_supported(self) -> bool:
        """Indicate whether public key authentication is supported."""
        return True

    async def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        # This method is not called when username is not "github" - as long as the key was valid
        # Also, this SSHServer.validate_public_key method does not perform signature verification
        # The SSHServerConnection.validate_public_key method performs signature verification
        if username != "github":
            # We log an authentication failure in here because if we're here with a different username the key failed
            log.auth_failure("ssh", "public_key_invalid", username)
            return False

        fingerprint = key.get_fingerprint()

        async with db.session() as data:
            workflow_key = await data.workflow_ssh_key(fingerprint=fingerprint).get()

        if workflow_key is None:
            return False

        if workflow_key.revoked:
            log.auth_failure("githubssh", "workflow_key_revoked", workflow_key.asf_uid)
            return False

        # In some cases this will be a service account
        self._github_asf_uid = workflow_key.asf_uid
        log.set_asf_uid(self._github_asf_uid)

        if not await ldap.is_active(self._github_asf_uid):
            log.auth_failure("githubssh", "ssh_workflow_account_disabled", self._github_asf_uid)
            return False

        now = int(time.time())
        # audit_guidance this application is not concerned with checking for a not_before flag on the workflow_key
        if workflow_key.expires < now:
            log.auth_failure("githubssh", "public_key_expired", self._github_asf_uid)
            return False

        self._github_payload = github.TrustedPublisherPayload.model_validate(workflow_key.github_payload)
        return True

    def _get_asf_uid(self, process: asyncssh.SSHServerProcess) -> str:
        username = process.get_extra_info("username")
        if username == "github":
            if self._github_asf_uid is None:
                raise RsyncArgsError("GitHub authentication did not resolve ASF UID")
            return self._github_asf_uid
        return username

    def _get_github_payload(self, process: asyncssh.SSHServerProcess) -> github.TrustedPublisherPayload | None:
        username = process.get_extra_info("username")
        if username != "github":
            return None
        return self._github_payload


async def rate_limit_cleanup_loop() -> None:
    """Periodically remove stale entries from the rate limit buckets."""
    while True:
        await asyncio.sleep(_RATE_WINDOW)
        now = time.monotonic()
        for bucket in (global_ip_rate_buckets, global_user_rate_buckets):
            stale_keys = [key for key, window in bucket.items() if not window or (now - window[-1]) > _RATE_WINDOW]
            if len(stale_keys) == len(bucket):
                bucket.clear()
            else:
                for key in stale_keys:
                    del bucket[key]


async def server_start() -> asyncssh.SSHAcceptor:
    """Start the SSH server."""
    # TODO: Where do we actually do this?
    # await aiofiles.os.makedirs(_CONFIG.STATE_DIR, exist_ok=True)

    # Generate temporary host key if it doesn't exist
    key_path = os.path.join(_CONFIG.STATE_DIR, "secrets", "generated", "ssh_host_key")
    if not await aiofiles.os.path.exists(key_path):
        private_key = asyncssh.generate_private_key("ssh-ed25519")
        private_key.write_private_key(key_path)
        log.info(f"Generated SSH host key at {key_path}")

    permissions = stat.S_IMODE(os.stat(key_path).st_mode)
    if permissions != 0o400:
        os.chmod(key_path, 0o400)
        log.warning("Set permissions of SSH host key to 0o400")

    def process_factory(process: asyncssh.SSHServerProcess) -> asyncio.Task[None]:
        connection = process.get_extra_info("connection")
        server_instance = connection.get_owner()
        return asyncio.create_task(_step_01_handle_client(process, server_instance))

    server = await asyncssh.create_server(
        SSHServer,
        server_host_keys=[key_path],
        process_factory=process_factory,
        host=_CONFIG.SSH_HOST,
        port=_CONFIG.SSH_PORT,
        keepalive_interval=30,
        keepalive_count_max=3,
        encoding=None,
        encryption_algs=_APPROVED_CIPHERS,
        kex_algs=_APPROVED_KEX,
        mac_algs=_APPROVED_MACS,
    )

    log.info(f"SSH server started on {_CONFIG.SSH_HOST}:{_CONFIG.SSH_PORT}")
    return server


async def server_stop(server: asyncssh.SSHAcceptor) -> None:
    """Stop the SSH server."""
    server.close()
    await server.wait_closed()
    log.info("SSH server stopped")


def _build_rsync_write_argv(argv: list[str], path: safe.StatePath) -> list[str]:
    """Build the rsync command for a write, adding enforced server side limits."""
    if len(argv) < 2 or argv[-2] != ".":
        raise RuntimeError("Validated rsync write argv must end with '.' and the destination path")
    return [*argv[:-2], f"--max-size={_RSYNC_MAX_UPLOAD_SIZE}", "--info=skip2", ".", str(path)]


def _output_stderr(process: asyncssh.SSHServerProcess, message: str) -> None:
    """Output a message to the client's stderr."""
    message = f"ATR SSH: {message}"
    log.error(message)
    encoded_message = f"{message}\n".encode()
    try:
        process.stderr.write(encoded_message)
    except BrokenPipeError:
        log.warning("Failed to write error to client stderr: broken pipe")
    except Exception as e:
        log.exception(f"Error writing to client stderr: {e}")


def _rate_limit_check(bucket: dict[str, collections.deque[float]], key: str, limit: int) -> bool:
    """Return True if key is within the rate limit, False if exceeded. Mutates bucket."""
    now = time.monotonic()
    window = bucket.get(key)
    if window is not None:
        while window and (now - window[0]) > _RATE_WINDOW:
            window.popleft()
        if window and len(window) >= limit:
            return False
    if window is None:
        window = collections.deque()
        bucket[key] = window
    window.append(now)
    return True


async def _step_01_handle_client(process: asyncssh.SSHServerProcess, server: SSHServer) -> None:
    """Process client command, validating and dispatching to read or write handlers."""
    try:
        await _step_02_handle_safely(process, server)
    except RsyncArgsError as e:
        _output_stderr(process, f"Error: {e}")
        if not process.is_closing():
            process.exit(1)
    except Exception as e:
        log.exception(f"Error during client command processing: {e}")
        _output_stderr(process, f"Exception: {e}")
        if not process.is_closing():
            process.exit(1)
    finally:
        await _wait_for_process_to_close(process)


async def _step_02_handle_safely(process: asyncssh.SSHServerProcess, server: SSHServer) -> None:
    asf_uid = server._get_asf_uid(process)
    log.set_asf_uid(asf_uid)
    log.info(f"Handling command for authenticated user: {asf_uid}")

    if not await ldap.is_active(asf_uid):
        raise RsyncArgsError("Account disabled")

    if not process.command:
        raise RsyncArgsError("No command specified")

    log.info(f"Received command: {process.command}")
    # TODO: Use shlex.split or similar if commands can contain quoted arguments
    argv = process.command.split()

    ##############################################
    ### Calls _step_03_command_simple_validate ###
    ##############################################
    is_read_request = _step_03_command_simple_validate(argv)

    #######################################
    ### Calls _step_04_command_validate ###
    #######################################
    project_key, version_key, file_patterns, release_obj = await _step_04_command_validate(
        process, argv, is_read_request, server
    )
    # The release object is only present for read requests

    if release_obj is not None:
        log.info(f"Processing READ request for {release_obj.key}")
        ####################################################
        ### Calls _step_07a_process_validated_rsync_read ###
        ####################################################
        await _step_07a_process_validated_rsync_read(process, argv, release_obj, file_patterns)
    else:
        release_key = sql.release_key(str(project_key), str(version_key))
        _output_stderr(process, f"Received write command: {process.command}")
        log.info(f"Processing WRITE request for {release_key}")
        #####################################################
        ### Calls _step_07b_process_validated_rsync_write ###
        #####################################################
        await _step_07b_process_validated_rsync_write(process, argv, project_key, version_key, server)


def _step_03_command_simple_validate(argv: list[str]) -> bool:
    """Validate the basic structure of the rsync command and detect read vs write."""
    # We use our own arg parsing here to be more strict about the syntax
    # READ: ['rsync', '--server', '--sender', '-vlogDtpre.iLsfxCIvu', '.', '/proj/v1/']
    # WRITE: ['rsync', '--server', '-vlogDtpre.iLsfxCIvu', '.', '/proj/v1/']
    argv = argv[:]

    if argv[:2] != ["rsync", "--server"]:
        raise RsyncArgsError("The first two arguments must be rsync and --server")
    argv = argv[2:]

    is_read_request = False
    if argv[:1] == ["--sender"]:
        is_read_request = True
        argv = argv[1:]

    flags = set()
    while argv and argv[0].startswith("-") and (not argv[0].startswith("--")):
        aflags = argv.pop(0)[1:]
        if "e." in aflags:
            aflags = aflags.split("e.", 1)[0]
        flags.update(aflags)
    # The -r flag takes precedence over -d and --dirs
    flags.discard("d")

    if flags != {"D", "g", "l", "o", "p", "r", "t", "v"}:
        raise RsyncArgsError(f"The flags must be -Dgloprtv, got {sorted(flags)}")

    # The -r flag takes precedence over -d and --dirs
    if argv[:1] == ["--dirs"]:
        argv = argv[1:]

    if (not is_read_request) and (argv[:1] == ["--delete"]):
        argv = argv[1:]

    if len(argv) != 2:
        raise RsyncArgsError(f"Expected two path arguments, got {argv}")

    if argv[0] != ".":
        raise RsyncArgsError("The first path argument must be .")
    return is_read_request


async def _step_04_command_validate(
    process: asyncssh.SSHServerProcess, argv: list[str], is_read_request: bool, server: SSHServer
) -> tuple[safe.ProjectKey, safe.VersionKey, list[str] | None, sql.Release | None]:
    """Validate the path and user permissions for read or write."""
    ############################################
    ### Calls _step_05a/b_command_path_validate ###
    ############################################
    if is_read_request:
        project_key, version_key, tag = await _step_05a_command_path_validate_read(argv[-1])
    else:
        project_key, version_key, tag = await _step_05b_command_path_validate_write(argv[-1])

    ssh_uid = server._get_asf_uid(process)
    async with db.session() as data:
        project = await data.project(key=str(project_key), status=sql.ProjectStatus.ACTIVE, _committee=True).get()
        if project is None:
            raise RsyncArgsError(f"Project '{project_key}' does not exist")

        release = await data.release(
            project_key=str(project_key), version=str(version_key), _project_release_policy=True
        ).get()

    if is_read_request:
        #################################################
        ### Calls _step_06a_validate_read_permissions ###
        #################################################
        validated_release, file_patterns = await _step_06a_validate_read_permissions(
            ssh_uid, project, release, project_key, version_key, tag
        )
        return project_key, version_key, file_patterns, validated_release

    ##################################################
    ### Calls _step_06b_validate_write_permissions ###
    ##################################################
    await _step_06b_validate_write_permissions(ssh_uid, project, release)
    return project_key, version_key, None, None


async def _step_05a_command_path_validate_read(path: str) -> tuple[safe.ProjectKey, safe.VersionKey, str | None]:
    """Validate the path argument for rsync read commands, returning safe datatypes."""
    # READ: rsync --server --sender -vlogDtpre.iLsfxCIvu . /proj/v1/
    # Validating path: /proj/v1/
    _validate_path_common(path)
    if (path.count("/") < 3) or (path.count("/") > 4):
        raise RsyncArgsError("The path argument should be a /PROJECT/VERSION/(tag)/ directory path")

    path_project, path_version, *rest = path.strip("/").split("/", 2)
    tag = rest[0] if rest else None
    try:
        project_key = safe.ProjectKey(path_project)
    except ValueError:
        raise RsyncArgsError("Project is invalid")
    try:
        version_key = safe.VersionKey(path_version)
    except ValueError:
        raise RsyncArgsError("Version is invalid")
    if tag:
        _validate_tag_segment(tag)

    async with db.session() as data:
        release = await data.release(
            project_key=str(project_key),
            version=str(version_key),
        ).get()
        if release is None:
            raise RsyncArgsError(f"Release '{path_project}-{path_version}' does not exist")
    return project_key, version_key, tag


async def _step_05b_command_path_validate_write(path: str) -> tuple[safe.ProjectKey, safe.VersionKey, None]:
    """Validate the path argument for rsync write commands, returning safe datatypes."""
    # WRITE: rsync --server -vlogDtpre.iLsfxCIvu . /proj/v1/
    # Validating path: /proj/v1/
    _validate_path_common(path)
    if path.count("/") != 3:
        raise RsyncArgsError("The path argument should be a /PROJECT/VERSION/ directory path")

    path_project, path_version = path.strip("/").split("/", 1)
    try:
        project_key = safe.ProjectKey(path_project)
    except ValueError:
        raise RsyncArgsError("Project is invalid")
    try:
        version_key = safe.VersionKey(path_version)
    except ValueError:
        raise RsyncArgsError("Version is invalid")

    async with db.session() as data:
        project = await data.project(key=str(project_key)).get()
        if project is None:
            raise RsyncArgsError(f"Project '{project_key}' does not exist")

    return project_key, version_key, None


async def _step_06a_validate_read_permissions(
    ssh_uid: str,
    project: sql.Project,
    release: sql.Release | None,
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    tag: str | None,
) -> tuple[sql.Release | None, list[str] | None]:
    """Validate permissions for a read request."""
    if release is None:
        raise RsyncArgsError(f"Release '{project_key}-{version_key}' does not exist")

    allowed_read_phases = {
        sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
        sql.ReleasePhase.RELEASE_CANDIDATE,
        sql.ReleasePhase.RELEASE_PREVIEW,
    }
    if release.phase not in allowed_read_phases:
        raise RsyncArgsError(f"Release '{release.key}' is not in a readable phase ({release.phase.value})")

    if release.is_embargoed:
        read_allowed = user.can_view_embargoed_release(project.committee, ssh_uid, is_member=False)
    else:
        read_allowed = user.is_committer(project.committee, ssh_uid)
    if not read_allowed:
        raise RsyncArgsError(
            f"You must be a committer or committee member for project '{project.key}' to read this release"
        )

    if tag:
        if (not release.project.release_policy) or (not release.project.release_policy.file_tag_mappings):
            raise RsyncArgsError(f"Release '{release.key}' does not support tags")
        tags = release.project.release_policy.file_tag_mappings.keys()
        if tag not in tags:
            raise RsyncArgsError(f"Tag '{tag}' is not allowed for release '{release.key}'")
        if ".." in "".join(release.project.release_policy.file_tag_mappings[tag]):
            raise RsyncArgsError(f"Tag '{tag}' is misconfigured for release '{release.key}'")
        return release, release.project.release_policy.file_tag_mappings[tag]
    return release, None


async def _step_06b_validate_write_permissions(
    ssh_uid: str,
    project: sql.Project,
    release: sql.Release | None,
) -> None:
    """Validate permissions for a write request."""
    if release is None:
        # Creating a new release requires committee membership
        if not user.is_committee_member(project.committee, ssh_uid):
            raise RsyncArgsError(f"You must be a member of project '{project.key}' committee to create a release")
    else:
        # Uploading to existing release, requires DRAFT and participant status
        if release.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            raise RsyncArgsError(
                f"Cannot upload: Release '{release.key}' is no longer in draft phase ({release.phase.value})"
            )

        if not user.is_committer(project.committee, ssh_uid):
            raise RsyncArgsError(
                f"You must be a committer or committee member for project '{project.key}' to upload to this release"
            )


async def _step_07a_process_validated_rsync_read(
    process: asyncssh.SSHServerProcess,
    argv: list[str],
    release: sql.Release,
    file_patterns: list[str] | None,
) -> None:
    """Handle a validated rsync read request."""
    exit_status = 1
    try:
        # Determine the source directory based on the release phase and revision
        source_dir = paths.release_directory(release)
        log.info(
            f"Identified source directory for read: {source_dir} for release "
            f"{release.key} (phase {release.phase.value})"
        )

        # Check whether the source directory actually exists before proceeding
        if not await aiofiles.os.path.isdir(source_dir):
            raise RsyncArgsError(f"Source directory '{source_dir}' not found for release {release.key}")

        if file_patterns is None:
            # Update the rsync command path to the determined source directory
            argv[-1] = str(source_dir)
            if not argv[-1].endswith("/"):
                argv[-1] += "/"
        else:
            files = [
                f for pattern in file_patterns for f in await asyncio.to_thread(glob.glob, f"{source_dir}/{pattern}")
            ]
            argv[-1:] = files

        ###################################################
        ### Calls _step_08_execute_rsync_sender_command ###
        ###################################################
        exit_status = await _step_08_execute_rsync(process, argv)
        if exit_status != 0:
            log.error(
                f"rsync --sender failed with exit status {exit_status} for release {release.key}. "
                f"Command: {process.command} (run as {' '.join(argv)})"
            )

        if not process.is_closing():
            process.exit(exit_status)

    except Exception as e:
        log.exception(f"Error during rsync read processing for {release.key}")
        raise RsyncArgsError(f"Internal error processing read request: {e}")


async def _step_07b_process_validated_rsync_write(
    process: asyncssh.SSHServerProcess,
    argv: list[str],
    project_key: safe.ProjectKey,
    version_key: safe.VersionKey,
    server: SSHServer,
) -> None:
    """Handle a validated rsync write request."""
    asf_uid = server._get_asf_uid(process)
    exit_status = 0
    release_key = sql.release_key(project_key, version_key)

    # Ensure the release object exists or is created
    # This must happen before creating the revision directory
    #######################################################
    ### Calls _step_07c_ensure_release_object_for_write ###
    #######################################################
    await _step_07c_ensure_release_object_for_write(project_key, version_key)

    # Create the draft revision directory structure
    description = "File synchronisation through ssh, using rsync"
    async with storage.write(asf_uid) as write:
        wacp = await write.as_project_committee_participant(project_key)

        async def modify(path: safe.StatePath, old_rev: sql.Revision | None) -> None:
            nonlocal exit_status
            if old_rev is not None:
                log.info(f"Using old revision {old_rev.number} and interim path {path}")
            rsync_argv = _build_rsync_write_argv(argv, path)

            ###################################################
            ### Calls _step_08_execute_rsync_upload_command ###
            ###################################################
            exit_status = await _step_08_execute_rsync(process, rsync_argv)
            if exit_status != 0:
                if old_rev is not None:
                    for_revision = f"successor of revision {old_rev.number}"
                else:
                    for_revision = f"initial revision for release {release_key}"
                log.error(
                    f"rsync upload failed with exit status {exit_status} for {for_revision}. "
                    f"Command: {process.command} (run as {' '.join(rsync_argv)})"
                )
                raise datatypes.FailedError(f"rsync upload failed with exit status {exit_status} for {for_revision}")

        try:
            result = await wacp.revision.create_revision_with_quarantine(
                project_key,
                version_key,
                asf_uid,
                allowed_phases=frozenset({sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT}),
                description=description,
                modify=modify,
            )
            if isinstance(result, sql.Quarantined):
                log.info(f"rsync upload quarantined for release {release_key}")
                message = f"\nATR: Upload received for {project_key} {version_key}. Archive validation in progress.\n"
            else:
                github_payload = server._get_github_payload(process)
                if github_payload is not None:
                    await attestable.github_tp_payload_write(
                        project_key, version_key, result.safe_number, github_payload
                    )
                log.info(f"rsync upload successful for revision {result.number}")
                host = config.get().APP_HOST
                message = f"\nATR: Created revision {result.number} of {project_key} {version_key}\n"
                message += f"ATR: https://{host}/compose/{project_key}/{version_key}\n"
            if not process.stderr.is_closing():
                process.stderr.write(message.encode())
                await process.stderr.drain()
        except datatypes.FailedError:
            log.info(f"rsync upload unsuccessful for release {release_key}")

        # If we got here, there was no exception
        if not process.is_closing():
            process.exit(exit_status)


async def _step_07c_ensure_release_object_for_write(project_key: safe.ProjectKey, version_key: safe.VersionKey) -> None:
    """Ensure the release object exists or create it for a write operation."""
    release_key = sql.release_key(str(project_key), str(version_key))
    async with db.session() as data:
        release = await data.release(key=release_key, _committee=True).get()
        if release is None:
            project = await data.project(key=str(project_key), status=sql.ProjectStatus.ACTIVE, _committee=True).demand(
                RuntimeError("Project not found after validation")
            )
            if version_key_error := util.version_key_error(str(version_key)):
                # This should ideally be caught by path validation, but double check
                raise RuntimeError(f'Invalid version name "{version_key}": {version_key_error}')
            # Create a new release object
            log.info(f"Creating new release object for {release_key}")
            release = sql.Release(
                project_key=project.key,
                project=project,
                version=str(version_key),
                phase=sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT,
                created=datetime.datetime.now(datetime.UTC),
            )
            data.add(release)
        elif release.phase != sql.ReleasePhase.RELEASE_CANDIDATE_DRAFT:
            raise RsyncArgsError(f"Release '{release.key}' is no longer in draft phase ({release.phase.value})")
        await data.commit()


async def _step_08_execute_rsync(process: asyncssh.SSHServerProcess, argv: list[str]) -> int:
    """Execute the modified rsync command."""
    log.info(f"Executing modified rsync command: {' '.join(argv)}")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Redirect the client's streams to the rsync process
    # TODO: Do we instead need send_eof=False on stderr only?
    # , stdout=proc.stdout
    # , stderr=proc.stderr
    # , send_eof=False
    await process.redirect(stdin=proc.stdin, stdout=proc.stdout, send_eof=False)
    # Wait for rsync to finish and get its exit status
    try:
        exit_status = await asyncio.wait_for(proc.wait(), timeout=_RSYNC_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RsyncArgsError(f"rsync operation timed out after {_RSYNC_TIMEOUT} seconds")
    log.info(f"Rsync finished with exit status {exit_status}")
    return exit_status


def _validate_path_common(path: str) -> None:
    """Validate the common structural requirements for an rsync path argument."""
    if not path.startswith("/"):
        raise RsyncArgsError("The path argument should be an absolute path")
    if not path.endswith("/"):
        # Technically we could ignore this, because we rewrite the path anyway for writes
        # But we should enforce good rsync usage practices
        raise RsyncArgsError("The path argument should be a directory path, ending with a /")
    if "//" in path:
        raise RsyncArgsError("The path argument should not contain //")


def _validate_tag_segment(segment: str) -> None:
    if not all(c in _PATH_ALPHANUM for c in segment):
        raise RsyncArgsError("The tag should contain only alphanumeric characters or hyphens")


async def _wait_for_process_to_close(process: asyncssh.SSHServerProcess) -> None:
    """Ensure that SSH process cleanup tasks run to avoid unawaited coroutines."""
    try:
        await process.wait_closed()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Error while waiting for SSH process to close")
