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

"""server.py"""

import asyncio
import contextlib
import datetime
import fcntl
import multiprocessing
import os
import pathlib
import queue
import stat
import sys
import urllib.parse
import uuid
from collections.abc import Iterable
from typing import Any, Final

import asfquart
import asfquart.base as base
import asfquart.generics
import asfquart.session
import blockbuster
import quart
import quart_schema
import quart_wtf
import werkzeug.routing as routing

import atr
import atr.blueprints as blueprints
import atr.config as config
import atr.db as db
import atr.db.interaction as interaction
import atr.filters as filters
import atr.log as log
import atr.manager as manager
import atr.models.sql as sql
import atr.preload as preload
import atr.ssh as ssh
import atr.svn.pubsub as pubsub
import atr.tasks as tasks
import atr.template as template
import atr.user as user
import atr.util as util

# TODO: Technically this is a global variable
# We should probably find a cleaner way to do this
app: base.QuartApp | None = None

# The order of these migrations must be checked carefully to avoid conflicts
_MIGRATIONS: Final[list[tuple[str, str]]] = [
    # Audit
    ("storage-audit.log", "audit/storage-audit.log"),
    # Cache
    ("routes.json", "cache/routes.json"),
    ("user_session_cache.json", "cache/user_session_cache.json"),
    # Database
    ("atr.db", "database/atr.db"),
    ("atr.db-shm", "database/atr.db-shm"),
    ("atr.db-wal", "database/atr.db-wal"),
    # Logs
    ("atr-worker.log", "logs/atr-worker.log"),
    ("atr-worker-error.log", "logs/atr-worker-error.log"),
    ("keys_import.log", "logs/keys-import.log"),
    ("route-performance.log", "logs/route-performance.log"),
    # Secrets
    ("secrets.ini", "secrets/curated/secrets.ini"),
    ("apptoken.txt", "secrets/generated/apptoken.txt"),
    ("ssh_host_key", "secrets/generated/ssh_host_key"),
    # Subversion
    ("svn", "subversion"),
    # Temporary
    ("tmp", "temporary"),
]

_SWAGGER_UI_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <link type="text/css" rel="stylesheet" href="{{ swagger_css_url }}">
  <title>{{ title }}</title>
</head>
<body>
  <div id="swagger-ui" data-openapi-url="{{ openapi_url }}"></div>
  <script src="{{ swagger_js_url }}"></script>
  <script src="{{ swagger_init_url }}"></script>
</body>
</html>
"""

# Avoid OIDC
asfquart.generics.OAUTH_URL_INIT = "https://oauth.apache.org/auth?state=%s&redirect_uri=%s"
asfquart.generics.OAUTH_URL_CALLBACK = "https://oauth.apache.org/token?code=%s"


class ApiOnlyOpenAPIProvider(quart_schema.OpenAPIProvider):
    def generate_rules(self) -> Iterable[routing.Rule]:
        for rule in super().generate_rules():
            if rule.rule.startswith("/api"):
                yield rule


def _app_create_base(app_config: type[config.AppConfig]) -> base.QuartApp:
    """Create the base Quart application."""
    if asfquart.construct is ...:
        raise ValueError("asfquart.construct is not set")
    app = asfquart.construct(__name__, token_file="secrets/generated/apptoken.txt")
    # ASFQuart sets secret_key from apptoken.txt, or generates a new one
    # We must preserve this because from_object will overwrite it
    # Our AppConfig.SECRET_KEY is None since we no longer support that setting
    asfquart_secret_key = app.secret_key
    app.config.from_object(app_config)
    app.secret_key = asfquart_secret_key
    return app


def _app_dirs_setup(state_dir_str: str, hot_reload: bool) -> None:
    """Setup application directories."""
    if not os.path.isdir(state_dir_str):
        raise RuntimeError(f"State directory not found: {state_dir_str}")
    os.chdir(state_dir_str)
    if hot_reload is False:
        print(f"Working directory changed to: {os.getcwd()}")

    # Note that the hypercorn directories are not managed by ATR
    directories_to_ensure = [
        pathlib.Path(state_dir_str) / "audit",
        pathlib.Path(state_dir_str) / "cache",
        pathlib.Path(state_dir_str) / "database",
        pathlib.Path(state_dir_str) / "hypercorn" / "logs",
        pathlib.Path(state_dir_str) / "hypercorn" / "secrets",
        pathlib.Path(state_dir_str) / "logs",
        pathlib.Path(state_dir_str) / "runtime",
        pathlib.Path(state_dir_str) / "secrets" / "curated",
        pathlib.Path(state_dir_str) / "secrets" / "generated",
        util.get_downloads_dir(),
        util.get_finished_dir(),
        util.get_tmp_dir(),
        util.get_unfinished_dir(),
    ]
    for directory in directories_to_ensure:
        directory.mkdir(parents=True, exist_ok=True)
        util.chmod_directories(directory, permissions=0o755)


def _app_setup_api_docs(app: base.QuartApp) -> None:
    """Configure OpenAPI documentation."""
    import quart_schema

    import atr.metadata as metadata

    app.config["QUART_SCHEMA_SWAGGER_JS_URL"] = "/static/js/min/swagger-ui-bundle.min.js"
    app.config["QUART_SCHEMA_SWAGGER_CSS_URL"] = "/static/css/swagger-ui.min.css"

    quart_schema.QuartSchema(
        app,
        info=quart_schema.Info(
            title="ATR API",
            description="OpenAPI documentation for the Apache Trusted Releases (ATR) platform.",
            version=metadata.version,
        ),
        openapi_provider_class=ApiOnlyOpenAPIProvider,
        swagger_ui_path=None,
        openapi_path="/api/openapi.json",
        security_schemes={
            "BearerAuth": quart_schema.HttpSecurityScheme(
                scheme="bearer",
                bearer_format="JWT",
            )
        },
    )

    @app.route("/api/docs")
    @quart_schema.hide
    async def swagger_ui() -> str:
        return await quart.render_template_string(
            _SWAGGER_UI_TEMPLATE,
            title="ATR API",
            swagger_js_url=app.config["QUART_SCHEMA_SWAGGER_JS_URL"],
            swagger_css_url=app.config["QUART_SCHEMA_SWAGGER_CSS_URL"],
            swagger_init_url="/static/js/src/swagger-init.js",
            openapi_url=quart.url_for("openapi"),
        )


def _app_setup_context(app: base.QuartApp) -> None:
    """Setup application context processor."""

    @app.context_processor
    async def app_wide() -> dict[str, Any]:
        import atr.admin as admin
        import atr.get as get
        import atr.mapping as mapping
        import atr.metadata as metadata
        import atr.post as post

        return {
            "admin": admin,
            "as_url": util.as_url,
            "commit": metadata.commit,
            "current_user": await asfquart.session.read(),
            "get": get,
            "is_admin_fn": user.is_admin,
            "is_viewing_as_admin_fn": util.is_user_viewing_as_admin,
            "is_committee_member_fn": user.is_committee_member,
            "post": post,
            "static_url": util.static_url,
            "unfinished_releases_fn": interaction.unfinished_releases,
            # "user_committees_fn": interaction.user_committees,
            "user_projects_fn": interaction.user_projects,
            "release_as_url": mapping.release_as_url,
            "version": metadata.version,
        }


def _app_setup_lifecycle(app: base.QuartApp, app_config: type[config.AppConfig]) -> None:
    """Setup application lifecycle hooks."""

    @app.before_serving
    async def startup() -> None:
        """Start services before the app starts serving requests."""

        await asyncio.to_thread(_set_file_permissions_to_read_only)

        worker_manager = manager.get_worker_manager()
        await worker_manager.start()

        # Register recurring tasks (metadata updates, workflow status checks, etc.)
        scheduler_task = asyncio.create_task(_register_recurrent_tasks())
        app.extensions["scheduler_task"] = scheduler_task

        await _initialise_test_environment(app_config)

        await _initialise_pubsub(app_config, app)

        ssh_server = await ssh.server_start()
        app.extensions["ssh_server"] = ssh_server

    @app.after_serving
    async def shutdown() -> None:
        """Clean up services after the app stops serving requests."""
        worker_manager = manager.get_worker_manager()
        await worker_manager.stop()

        # Stop the metadata scheduler
        scheduler_task = app.extensions.get("scheduler_task")
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                ...

        ssh_server = app.extensions.get("ssh_server")
        if ssh_server:
            await ssh.server_stop(ssh_server)

        if task := app.extensions.get("svn_listener"):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        await db.shutdown_database()

        if audit_listener := app.extensions.get("audit_listener"):
            audit_listener.stop()
        if listener := app.extensions.get("logging_listener"):
            listener.stop()

        app.background_tasks.clear()


def _app_setup_logging(app: base.QuartApp, config_mode: config.Mode, app_config: type[config.AppConfig]) -> None:
    """Setup application logging with structlog and queue-based handlers."""
    import logging.handlers

    import structlog

    # Shared processors for structlog (run before formatting)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # Output handler: pretty console for dev (Debug and Allow Tests), JSON for non-dev (Docker, etc.)
    output_handler = logging.StreamHandler(sys.stderr)
    if util.is_dev_environment():
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()
    output_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
            foreign_pre_chain=shared_processors,
        )
    )
    # Queue-based logging for thread safety
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    handlers: list[logging.Handler] = [output_handler]
    if util.is_dev_environment():
        handlers.append(log.create_debug_handler())

    listener = logging.handlers.QueueListener(log_queue, *handlers, respect_handler_level=True)
    app.extensions["logging_listener"] = listener
    listener.start()

    logging.basicConfig(
        level=logging.INFO,
        handlers=[log.StructlogQueueHandler(log_queue)],
        force=True,
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Audit logger - JSON to dedicated file via queue
    audit_handler = logging.FileHandler(app_config.STORAGE_AUDIT_LOG_FILE, encoding="utf-8")
    audit_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
            foreign_pre_chain=shared_processors,
        )
    )
    audit_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    audit_listener = logging.handlers.QueueListener(audit_queue, audit_handler)
    audit_listener.start()
    app.extensions["audit_listener"] = audit_listener

    audit_logger = logging.getLogger("atr.storage.audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.handlers.clear()
    audit_logger.addHandler(logging.handlers.QueueHandler(audit_queue))
    audit_logger.propagate = False

    # Enable debug output for atr.* in DEBUG mode
    if config_mode == config.Mode.Debug:
        logging.getLogger(atr.__name__).setLevel(logging.DEBUG)

    # Only log in the worker process
    @app.before_serving
    async def log_debug_info() -> None:
        if (config_mode == config.Mode.Debug) or (config_mode == config.Mode.Profiling):
            log.info(f"DEBUG        = {config_mode == config.Mode.Debug}")
            log.info(f"ENVIRONMENT  = {config_mode.value}")
            log.info(f"STATE_DIR    = {app_config.STATE_DIR}")


def _app_setup_request_lifecycle(app: base.QuartApp) -> None:
    """Setup application request lifecycle hooks."""
    import structlog

    logger = structlog.get_logger("atr.request")

    @app.before_request
    async def bind_request_context_vars() -> None:
        log.clear_context()
        log.add_context(request_id=str(uuid.uuid4()))

        # Bind user_id if authenticated
        session = await asfquart.session.read()
        if session is not None:
            log.add_context(user_id=session.uid)

    @app.after_request
    async def log_request(response: quart.Response) -> quart.Response:
        logger.info(
            "request",
            method=quart.request.method,
            path=quart.request.path,
            status=response.status_code,
            remote_addr=quart.request.remote_addr,
        )
        return response


def _app_setup_security_headers(app: base.QuartApp) -> None:
    """Setup security headers including a Content Security Policy."""

    # Both object-src 'none' and base-uri 'none' are required by ASVS v5 3.4.3 (L2)
    # The frame-ancestors 'none' directive is required by ASVS v5 3.4.6 (L2)
    # Bootstrap uses data: URLs extensively, so we need to include that in img-src
    # The script hash allows window.location.reload() and nothing else
    csp_directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' https://apache.org https://incubator.apache.org https://www.apache.org data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    csp_header = "; ".join(csp_directives)

    permissions_policy = ", ".join(
        [
            "accelerometer=()",
            "autoplay=()",
            "camera=()",
            "clipboard-read=()",
            "clipboard-write=(self)",
            "display-capture=()",
            "geolocation=()",
            "gyroscope=()",
            "magnetometer=()",
            "microphone=()",
            "midi=()",
            "payment=()",
            "usb=()",
            "xr-spatial-tracking=()",
        ]
    )

    # X-Content-Type-Options: nosniff is required by ASVS v5 3.4.4 (L2)
    # A strict Referrer-Policy is required by ASVS v5 3.4.5 (L2)
    # HSTS is required by ASVS v5 9.2.1 (L1)
    # ASVS does not specify exactly what is meant by strict
    # We can't use Referrer-Policy: no-referrer because it breaks form redirection
    # TODO: We could automatically include a form field noting the form action URL
    @app.after_request
    async def add_security_headers(response: quart.Response) -> quart.Response:
        response.headers["Content-Security-Policy"] = csp_header
        response.headers["Permissions-Policy"] = permissions_policy
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def _create_app(app_config: type[config.AppConfig]) -> base.QuartApp:
    """Create and configure the application."""
    if os.sep != "/":
        raise RuntimeError('ATR requires a POSIX compatible filesystem where os.sep is "/"')
    config_mode = config.get_mode()
    hot_reload = _is_hot_reload()
    _validate_config(app_config, hot_reload)
    _migrate_state(app_config.STATE_DIR, hot_reload)
    _app_dirs_setup(app_config.STATE_DIR, hot_reload)
    _validate_secrets_permissions(pathlib.Path(app_config.STATE_DIR))
    log.performance_init()
    app = _app_create_base(app_config)

    _app_setup_api_docs(app)
    quart_wtf.CSRFProtect(app)
    _app_setup_logging(app, config_mode, app_config)
    db.init_database(app)
    _register_routes(app)
    blueprints.register(app)
    filters.register_filters(app)
    _app_setup_context(app)
    _app_setup_security_headers(app)
    _app_setup_request_lifecycle(app)
    _app_setup_lifecycle(app, app_config)

    # _register_recurrent_tasks()

    # do not enable template pre-loading if we explicitly want to reload templates
    if not app_config.TEMPLATES_AUTO_RELOAD:
        preload.setup_template_preloading(app)

    @app.before_serving
    async def start_blockbuster() -> None:
        # "I'll have a P, please, Bob."
        bb: blockbuster.BlockBuster | None = None
        if config_mode == config.Mode.Profiling:
            bb = blockbuster.BlockBuster()
        app.extensions["blockbuster"] = bb
        if bb is not None:
            bb.activate()
            log.info("Blockbuster activated to detect blocking calls")

    @app.after_serving
    async def stop_blockbuster() -> None:
        bb = app.extensions.get("blockbuster")
        if bb is not None:
            bb.deactivate()
            log.info("Blockbuster deactivated")

    return app


async def _initialise_pubsub(conf: type[config.AppConfig], app: base.QuartApp):
    pubsub_url = conf.PUBSUB_URL
    pubsub_user = conf.PUBSUB_USER
    pubsub_password = conf.PUBSUB_PASSWORD
    parsed_pubsub_url = urllib.parse.urlparse(pubsub_url) if pubsub_url else None
    valid_pubsub_url = bool(parsed_pubsub_url and parsed_pubsub_url.scheme and parsed_pubsub_url.netloc)

    if valid_pubsub_url and pubsub_url and pubsub_user and pubsub_password:
        log.info("Starting PubSub SVN listener")
        listener = pubsub.SVNListener(
            working_copy_root=conf.SVN_STORAGE_DIR,
            url=pubsub_url,
            username=pubsub_user,
            password=pubsub_password,
        )
        task = asyncio.create_task(listener.start())
        app.extensions["svn_listener"] = task
        log.info("PubSub SVN listener task created")
    else:
        log.info(
            "PubSub SVN listener not started: "
            f"pubsub_url={bool(valid_pubsub_url)} "
            f"pubsub_user={bool(pubsub_user)} "
            # Essential to use bool(...) here to avoid logging the password
            # TODO: We plan to add secret scanning when we migrate to t-strings
            f"pubsub_password={bool(pubsub_password)}",
        )


async def _initialise_test_environment(conf: type[config.AppConfig]) -> None:
    if not conf.ALLOW_TESTS:
        return

    async with db.session() as data:
        test_committee = await data.committee(name="test").get()
        if not test_committee:
            test_committee = sql.Committee(
                name="test",
                full_name="Test Committee",
                is_podling=False,
                committee_members=["test"],
                committers=["test"],
                release_managers=["test"],
            )
            data.add(test_committee)
            await data.commit()

        test_project = await data.project(name="test").get()
        if not test_project:
            test_project = sql.Project(
                name="test",
                full_name="Apache Test",
                status=sql.ProjectStatus.ACTIVE,
                committee_name="test",
                created=datetime.datetime.now(datetime.UTC),
                created_by="test",
            )
            data.add(test_project)
            await data.commit()


def _is_hot_reload() -> bool:
    proc = multiprocessing.current_process()
    if proc.name == "MainProcess":
        # Reloading is on, but this is the parent process
        return False
    if "--reload" not in sys.argv:
        # Reloading is off
        return False
    return True


def _migrate_path(old_path: pathlib.Path, new_path: pathlib.Path) -> None:
    # Keep track of ancestor directories that we create
    root_to_leaf_created: list[pathlib.Path] = []

    try:
        # Create all ancestor directories of new_path if they do not exist
        # We keep track of this so that we can attempt to roll back on failure
        focused_ancestor_directory = new_path.parent
        leaf_to_root_to_create = []
        while not focused_ancestor_directory.exists():
            leaf_to_root_to_create.append(focused_ancestor_directory)
            focused_ancestor_directory = focused_ancestor_directory.parent

        # It is not safe to run the rest of this function across filesystems
        # Now that we have the closest existing ancestor, we can check its device ID
        if os.stat(old_path).st_dev != os.stat(focused_ancestor_directory).st_dev:
            raise RuntimeError(f"Cannot migrate across filesystems: {old_path} -> {new_path}")

        # Start from the root, and create towards the leaf
        for ancestor_directory in reversed(leaf_to_root_to_create):
            ancestor_directory.mkdir()
            root_to_leaf_created.append(ancestor_directory)

        # Perform the actual migration as safely as possible
        _migrate_path_by_type(old_path, new_path)

    except Exception as e:
        # Roll back any created directories from leaf to root
        for created_directory in reversed(root_to_leaf_created):
            created_directory.rmdir()

        if isinstance(e, FileNotFoundError):
            # We check all paths before attempting to migrate
            # So if a file mysteriously disappears, we should raise an error
            raise RuntimeError(f"Migration path disappeared before migration: {old_path}") from e
        raise


def _migrate_path_by_type(old_path: pathlib.Path, new_path: pathlib.Path) -> None:
    # Migrate a regular file
    if old_path.is_file():
        try:
            # Hard linking fails if new_path already exists
            os.link(old_path, new_path)
        except FileExistsError:
            # If the migration was interrupted, there may be two hard links
            # If they link to the same inode, we can remove old_path
            # If not, then it's a real conflict
            if not os.path.samefile(old_path, new_path):
                # The inodes are different, so this is a real conflict
                raise RuntimeError(f"Migration conflict: {new_path} already exists")
            # Otherwise, the inodes are the same, so this is a partial migration
            # We fall through to complete the migration, but report the detection first
            print(f"Partial migration detected: {old_path} -> {new_path}")

        # Hard linking was successful, so we can remove old_path
        try:
            os.unlink(old_path)
        except FileNotFoundError:
            # Some other process must have deleted old_path
            print(f"Migration path removed by a third party during migration: {old_path}")
            # We do not return here, because the file is migrated
        print(f"Migrated file: {old_path} -> {new_path}")

    # Migrate a directory
    elif old_path.is_dir():
        if new_path.exists():
            # This is a TOCTOU susceptible check, but os.rename has further safeguards
            raise RuntimeError(f"Migration conflict: {new_path} already exists")
        try:
            # We assume that old_path is not replaced by a file before this rename
            # If new_path is a file, this raises a NotADirectoryError
            # If new_path is a directory and not empty, this raises an OSError
            # If new_path is an empty directory, it will be replaced
            # (We accept this behaviour, but also have a TOCTOU susceptible check above)
            os.rename(old_path, new_path)
            print(f"Migrated directory: {old_path} -> {new_path}")
        except OSError as e:
            # In this case, new_path was probably a directory and not empty
            raise RuntimeError(f"Migration conflict: {new_path} already exists") from e

    else:
        raise RuntimeError(f"Migration path is neither a file nor a directory: {old_path}")


def _migrate_state(state_dir_str: str, hot_reload: bool) -> None:
    # It's okay to use synchronous code in this function and in any functions that it calls
    state_dir = pathlib.Path(state_dir_str)

    # Are there migrations to apply?
    pending_migrations = _pending_migrations(state_dir)
    if not pending_migrations:
        return

    # Are we hot reloading?
    if hot_reload is True:
        print("!!!", file=sys.stderr)
        print("ERROR: Cannot migrate files during hot reload!", file=sys.stderr)
        print("The following files need to be migrated:", file=sys.stderr)
        for old_path, new_path in sorted(pending_migrations):
            print(f"  - {old_path} -> {new_path}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Restart the server to apply the migrations", file=sys.stderr)
        print("!!!", file=sys.stderr)
        sys.exit(1)

    # Are we already migrating?
    runtime_dir = state_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_dir / "migration.lock"

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _migrate_state_files(state_dir, pending_migrations)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _migrate_state_files(state_dir: pathlib.Path, pending_migrations: set[tuple[str, str]]) -> None:
    for old_path, new_path in _MIGRATIONS:
        if (old_path, new_path) not in pending_migrations:
            continue
        _migrate_path(state_dir / old_path, state_dir / new_path)


def _pending_migrations(state_dir: pathlib.Path) -> set[tuple[str, str]]:
    pending: set[tuple[str, str]] = set()
    for old_path, new_path in _MIGRATIONS:
        if (state_dir / old_path).exists():
            pending.add((old_path, new_path))
    return pending


async def _register_recurrent_tasks() -> None:
    """Schedule recurring tasks"""
    # Start scheduled tasks 5 min after server start
    await asyncio.sleep(300)
    try:
        await tasks.clear_scheduled()
        metadata = await tasks.metadata_update(asf_uid="system", schedule_next=True)
        log.info(f"Scheduled remote metadata update with ID {metadata.id}")
        await asyncio.sleep(60)
        workflow = await tasks.workflow_update(asf_uid="system", schedule_next=True)
        log.info(f"Scheduled workflow status update with ID {workflow.id}")

    except Exception as e:
        log.exception(f"Failed to schedule recurrent tasks: {e!s}")


def _register_routes(app: base.QuartApp) -> None:
    # Add a global error handler to show helpful error messages with tracebacks
    @app.errorhandler(Exception)
    async def handle_any_exception(error: Exception) -> Any:
        if quart.request.path.startswith("/api"):
            status_code = getattr(error, "code", 500) if isinstance(error, Exception) else 500
            return quart.jsonify({"error": str(error)}), status_code
        log.exception("Unhandled exception")
        return await template.render("error.html", error=str(error), status_code=500), 500

    @app.errorhandler(base.ASFQuartException)
    async def handle_asfquart_exception(error: base.ASFQuartException) -> Any:
        # TODO: Figure out why pyright doesn't know about this attribute
        if quart.request.path.startswith("/api"):
            errorcode = getattr(error, "errorcode", 500)
            return quart.jsonify({"error": str(error)}), errorcode
        if not hasattr(error, "errorcode"):
            errorcode = 500
        else:
            errorcode = getattr(error, "errorcode")
        return await template.render("error.html", error=str(error), status_code=errorcode), errorcode

    # Add a global error handler for payload too large which will normally be handled in front in httpd server
    @app.errorhandler(413)
    async def handle_payload_too_large(error: Exception) -> Any:
        log.error("Payload_too_large")
        log.error("Ignore any following stack traces from form parsing")
        if quart.request.path.startswith("/api"):
            return quart.jsonify({"error": "413 Payload Too Large"}), 413
        return await template.render("error.html", error="413 Payload Too Large", traceback="", status_code=413), 413

    # Add a global error handler in case a page does not exist.
    @app.errorhandler(404)
    async def handle_not_found(error: Exception) -> Any:
        # Serve JSON for API endpoints, HTML otherwise
        if quart.request.path.startswith("/api"):
            return quart.jsonify({"error": "404 Not Found"}), 404
        return await template.render("notfound.html", error="404 Not Found", traceback="", status_code=404), 404


def _set_file_permissions_to_read_only() -> None:
    """Set permissions of all files in the unfinished and finished directories to read only."""
    # TODO: After a migration period, incorrect permissions should be an error
    directories = [util.get_unfinished_dir(), util.get_finished_dir()]
    fixed_count = 0
    for directory in directories:
        if not directory.exists():
            continue
        for file_path in directory.rglob("*"):
            if not file_path.is_file():
                continue
            mode = stat.S_IMODE(file_path.stat().st_mode)
            if mode != 0o444:
                os.chmod(file_path, 0o444)
                fixed_count += 1
    if fixed_count > 0:
        log.info(f"Set permissions of {fixed_count} files to read only (0o444)")


def _validate_config(app_config: type[config.AppConfig], hot_reload: bool) -> None:
    # Custom configuration for the database path is no longer supported
    configured_path = app_config.SQLITE_DB_PATH
    if configured_path != "database/atr.db":
        print("!!!", file=sys.stderr)
        print("ERROR: Custom values of SQLITE_DB_PATH are no longer supported!", file=sys.stderr)
        print("Please unset SQLITE_DB_PATH to allow the server to start", file=sys.stderr)
        print("!!!", file=sys.stderr)
        sys.exit(1)

    # Configuring the SECRET_KEY outside of ASFQuart is no longer supported
    if (app_config.SECRET_KEY is not None) and (hot_reload is False):
        print("!!!", file=sys.stderr)
        print("WARNING: SECRET_KEY is no longer supported", file=sys.stderr)
        print("Please unset SECRET_KEY", file=sys.stderr)
        print("We are considering making this mandatory", file=sys.stderr)
        print("!!!", file=sys.stderr)
        # sys.exit(1)


def _validate_secrets_permissions(state_dir: pathlib.Path) -> None:
    secrets_dirs = [
        state_dir / "secrets",
        state_dir / "hypercorn" / "secrets",
    ]
    incorrect_files: list[tuple[pathlib.Path, int, int]] = []

    for secrets_dir in secrets_dirs:
        if not secrets_dir.exists():
            continue
        for path in secrets_dir.rglob("*"):
            if not path.is_file():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            expected = 0o600 if (path.name == "apptoken.txt") else 0o400
            if mode != expected:
                incorrect_files.append((path, mode, expected))

    if incorrect_files:
        print("!!!", file=sys.stderr)
        print("ERROR: Secrets files have incorrect permissions", file=sys.stderr)
        for path, mode, expected in incorrect_files:
            print(f"  {path}: {oct(mode)} (expected {oct(expected)})", file=sys.stderr)
        print("!!!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    raise RuntimeError("Call hypercorn directly with atr.server:app instead")
else:
    app = _create_app(config.get())
