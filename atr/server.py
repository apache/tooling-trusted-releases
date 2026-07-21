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
import logging
import multiprocessing
import os
import pathlib
import queue
import resource
import stat
import sys
import time
import urllib.parse
import uuid
from collections.abc import Iterable
from typing import Any, Final

import asfquart
import asfquart.base as base
import asfquart.generics
import asfquart.session
import blockbuster
import hypercorn.middleware.proxy_fix as proxy_fix
import quart
import quart_rate_limiter as rate_limiter
import quart_schema
import quart_wtf
import werkzeug.exceptions as exceptions
import werkzeug.routing as routing

import atr
import atr.analysis as analysis
import atr.blueprints as blueprints
import atr.cache as cache
import atr.config as config
import atr.constants as constants
import atr.db as db
import atr.db.interaction as interaction
import atr.errors as errors
import atr.filters as filters
import atr.form as form
import atr.jwtoken as jwtoken
import atr.ldap as ldap
import atr.log as log
import atr.manager as manager
import atr.models.sql as sql
import atr.paths as paths
import atr.preload as preload
import atr.pubsub as pubsub
import atr.sessions as sessions
import atr.ssh as ssh
import atr.storage as storage
import atr.tasks as tasks
import atr.tasks.quarantine as quarantine
import atr.template as template
import atr.user as user
import atr.util as util
import atr.web as web

# TODO: Technically this is a global variable
# We should probably find a cleaner way to do this
app: base.QuartApp | None = None

_RESOURCES_GAUGE_INTERVAL_SECONDS: Final = 300

# The order of these migrations must be checked carefully to avoid conflicts
_MIGRATIONS: Final[list[tuple[str, str]]] = [
    # Archives
    ("cache/archives", "archives"),
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

asfquart.generics.OAUTH_URL_INIT = "https://oauth.apache.org/auth-oidc?state=%s&redirect_uri=%s"
asfquart.generics.OAUTH_URL_CALLBACK = "https://oauth.apache.org/token-oidc?code=%s"


class ApiOnlyOpenAPIProvider(quart_schema.OpenAPIProvider):
    def generate_rules(self) -> Iterable[routing.Rule]:
        for rule in super().generate_rules():
            if rule.rule.startswith("/api"):
                yield rule


class SSLShutdownFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not record.exc_info:
            return True
        exc = record.exc_info[1]
        if not isinstance(exc, TimeoutError):
            return True
        if not exc.args:
            return True
        if exc.args[0] == "SSL shutdown timed out":
            return False
        return True


def _app_create_base(app_config: type[config.AppConfig]) -> base.QuartApp:
    """Create the base Quart application."""
    if asfquart.construct is ...:
        raise ValueError("asfquart.construct is not set")
    app = asfquart.construct(__name__, token_file="secrets/generated/apptoken.txt", basic_auth=False)
    app.jinja_environment = template.SyncEnvironment
    # ASFQuart sets secret_key from apptoken.txt, or generates a new one
    # We must preserve this because from_object will overwrite it
    # Our AppConfig.SECRET_KEY is None since we no longer support that setting
    asfquart_secret_key = app.secret_key
    app.config.from_object(app_config)
    app.cfg["MAX_SESSION_AGE"] = app.config.get("MAX_SESSION_AGE", 0)
    app.secret_key = asfquart_secret_key

    if not config.is_dev_environment():
        app.asgi_app = proxy_fix.ProxyFixMiddleware(app.asgi_app, mode="legacy", trusted_hops=1)

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
        pathlib.Path(state_dir_str) / "secrets" / "cached",
        pathlib.Path(state_dir_str) / "secrets" / "curated",
        pathlib.Path(state_dir_str) / "secrets" / "generated",
        pathlib.Path(paths.get_archives_dir()),
        pathlib.Path(paths.get_finished_dir()),
        pathlib.Path(paths.get_quarantined_dir()),
        pathlib.Path(paths.get_tmp_dir()),
        pathlib.Path(paths.get_unfinished_dir()),
    ]
    archives_dir = pathlib.Path(paths.get_archives_dir())
    unfinished_dir = pathlib.Path(paths.get_unfinished_dir())
    enforce_permissions = not config.is_dev_environment()
    for directory in directories_to_ensure:
        directory.mkdir(parents=True, exist_ok=True)
        if not enforce_permissions:
            continue
        # Some directories need custom permissions
        if directory == archives_dir:
            _enforce_archives_permissions(archives_dir)
        elif directory == unfinished_dir:
            _enforce_unfinished_permissions(unfinished_dir)
        else:
            util.chmod_directories(directory, permissions=0o755)


def _app_setup_api_docs(app: base.QuartApp) -> None:
    """Configure OpenAPI documentation."""
    import quart_schema

    import atr.metadata as metadata

    app.config["QUART_SCHEMA_SWAGGER_JS_URL"] = "/static/js/min/swagger-ui-bundle.min.js"
    app.config["QUART_SCHEMA_SWAGGER_CSS_URL"] = "/static/css/swagger-ui.min.css"
    # quart_schemas response pipeline (convert_response_return_value) calls
    # model.model_dump() on returned Pydantic models with the default
    # mode="python". That leaves custom types such as safe.ProjectKey as
    # raw Python objects in the response dict, which Quarts JSON encoder
    # then cannot serialise. Force mode="json" globally so model_dump
    # produces JSON-friendly primitives. This matches what every endpoint
    # actually needs on the wire, including endpoints that use SafeType
    # subclasses (ProjectKey, ReleaseKey, etc.) and StrEnum fields.
    app.config["QUART_SCHEMA_PYDANTIC_DUMP_OPTIONS"] = {"mode": "json"}

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

    # audit_guidance /api/docs and /api/openapi.json are intentionally public: ATR exposes a public API
    # and publishing its documentation is deliberate policy, consistent with open-source ASF practice;
    # admin routes are filtered from the spec by ApiOnlyOpenAPIProvider, so no internal surface is exposed
    @app.route("/api/docs")
    @quart_schema.hide
    async def swagger_ui() -> str:
        return await template.render_string(
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

        current_user = await sessions.read()
        topnav_unfinished_releases: list[tuple[str, str, list[sql.Release]]] = []
        topnav_user_projects: list[tuple[str, str]] = []
        colour_blindness_mode = sql.ColourBlindnessMode.NONE
        nav_pinned = True
        user_notifications: list[sql.Notification] = []
        if current_user is not None:
            current_uid = current_user.uid
            topnav_unfinished_releases, topnav_user_projects = await interaction.user_topnav(
                current_uid, current_user.is_member
            )
            async with db.session() as data:
                db_user = await data.user(asf_uid=current_uid).get()
                if db_user:
                    colour_blindness_mode = db_user.preferences.colour_blindness_mode
                    nav_pinned = db_user.preferences.nav_pinned
            try:
                async with storage.read(web.Committer(current_user)) as read:
                    rafc = read.as_foundation_committer()
                    user_notifications = await rafc.notifications.pending()
            except Exception:
                log.exception("Failed to load notifications for user context")

        return {
            "colour_blindness_mode": colour_blindness_mode,
            "nav_pinned": nav_pinned,
            "admin": admin,
            "as_url": util.as_url,
            "commit": metadata.commit,
            "csrf_input_fn": lambda: form.csrf_input(),
            "current_user": current_user,
            "get": get,
            "is_admin_fn": user.is_admin,
            "is_viewing_as_admin_fn": util.is_user_viewing_as_admin,
            "is_committee_member_fn": user.is_committee_member,
            "is_cyclonedx_json_fn": analysis.is_cyclonedx_json,
            "is_cyclonedx_xml_fn": analysis.is_cyclonedx_xml,
            "is_test_mode": config.is_test_mode(),
            "post": post,
            "static_url": util.static_url,
            "topnav_unfinished_releases": topnav_unfinished_releases,
            "topnav_user_projects": topnav_user_projects,
            "release_as_url": mapping.release_as_url,
            "user_notifications": user_notifications,
            "version": metadata.version,
        }


def _app_setup_lifecycle(app: base.QuartApp, app_config: type[config.AppConfig]) -> None:
    """Setup application lifecycle hooks."""

    @app.before_serving
    async def startup() -> None:
        """Start services before the app starts serving requests."""

        util.warn_default_tls_settings_if_changed()

        await asyncio.to_thread(_set_file_permissions_to_read_only)

        await _backfill_archive_cache()

        await cache.admins_startup_load()
        admins_task = asyncio.create_task(cache.admins_refresh_loop())
        app.extensions["admins_task"] = admins_task

        await cache.project_version_startup_load()
        project_version_task = asyncio.create_task(cache.project_version_refresh_loop())
        app.extensions["project_version_task"] = project_version_task

        await cache.email_uid_startup_load()

        await cache.banner_startup_load()

        worker_manager = manager.get_worker_manager()
        await worker_manager.start()

        # Register recurring tasks (metadata updates, workflow status checks, etc.)
        scheduler_task = asyncio.create_task(_register_recurrent_tasks())
        app.extensions["scheduler_task"] = scheduler_task

        await _initialise_test_environment(app_config)

        await _initialise_pubsub(app_config, app)

        ssh_server = await ssh.server_start()
        app.extensions["ssh_server"] = ssh_server

        ssh_rate_limit_task = asyncio.create_task(ssh.rate_limit_cleanup_loop())
        app.extensions["ssh_rate_limit_task"] = ssh_rate_limit_task

        resources_gauge_task = asyncio.create_task(_resources_gauge_loop())
        app.extensions["resources_gauge_task"] = resources_gauge_task

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

        if ssh_cleanup_task := app.extensions.get("ssh_rate_limit_task"):
            ssh_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ssh_cleanup_task

        if resources_gauge_task := app.extensions.get("resources_gauge_task"):
            resources_gauge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await resources_gauge_task

        ssh_server = app.extensions.get("ssh_server")
        if ssh_server:
            await ssh.server_stop(ssh_server)

        if task := app.extensions.get("pubsub_listener"):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if task := app.extensions.get("admins_task"):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        cache.email_uid_erase()

        await db.shutdown_database()

        await _app_shutdown_log_listeners(app)

        app.background_tasks.clear()


def _app_setup_logging(app: base.QuartApp, config_mode: config.Mode, app_config: type[config.AppConfig]) -> None:
    """Setup application logging with structlog and queue-based handlers."""
    import logging.handlers

    import structlog

    import atr.loggers as loggers

    shared_processors = loggers.shared_processors()

    # Output handler: pretty console for dev (Debug and Allow Tests), JSON for non-dev (Docker, etc.)
    output_handler = logging.StreamHandler(sys.stderr)
    use_json_output = app_config.LOG_JSON or (not config.is_dev_environment())
    if use_json_output:
        # JSON output should include rendered exceptions
        output_handler.setFormatter(loggers.create_json_formatter(shared_processors))
    else:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
        # Queue-based logging for thread safety
        output_handler.setFormatter(loggers.create_output_formatter(shared_processors, renderer))

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    handlers: list[logging.Handler] = [output_handler]
    if config.is_dev_environment():
        handlers.append(log.create_debug_handler())

    listener = logging.handlers.QueueListener(log_queue, *handlers, respect_handler_level=True)
    app.extensions["logging_listener"] = listener
    listener.start()

    logging.basicConfig(
        level=logging.getLevelNamesMapping()[app_config.LOG_LEVEL],
        handlers=[log.StructlogQueueHandler(log_queue)],
        force=True,
    )
    # Silence noisy dependency loggers even in DEBUG
    logging.getLogger("aiosqlite").setLevel(logging.INFO)
    logging.getLogger("hpack.hpack").setLevel(logging.INFO)

    loggers.configure_structlog(shared_processors)

    # Ignore SSL shutdown timeout errors from asyncio in Hypercorn
    ssl_shutdown_filter = SSLShutdownFilter()
    logging.getLogger("asyncio").addFilter(ssl_shutdown_filter)

    # Audit logger - JSON to dedicated file via queue
    storage_audit_listener = loggers.setup_dedicated_file_logger(
        "atr.storage.audit",
        app_config.STORAGE_AUDIT_LOG_FILE,
        shared_processors,
    )
    app.extensions["storage_audit_listener"] = storage_audit_listener

    # Auth audit logger - JSON to dedicated file via queue
    auth_audit_listener = loggers.setup_dedicated_file_logger(
        "atr.auth",
        app_config.AUTH_AUDIT_LOG_FILE,
        shared_processors,
    )
    app.extensions["auth_audit_listener"] = auth_audit_listener

    # Request logs
    request_listener = loggers.setup_dedicated_file_logger(
        "atr.request",
        app_config.REQUEST_LOG_FILE,
        shared_processors,
        queue_handler_class=log.StructlogQueueHandler,
    )
    app.extensions["request_listener"] = request_listener

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


def _app_setup_rate_limits(app: base.QuartApp, conf: type[config.AppConfig]):
    async def get_rate_limit_key() -> str:
        """Authenticated users -> pool per user"""
        session = await sessions.read()
        if isinstance(session, sql.UserSession):
            return f"user:{session.uid}"
        return f"ip:{quart.request.remote_addr}"

    if not config.is_test_mode():
        rate_limiter.RateLimiter(
            app,
            default_limits=[
                rate_limiter.RateLimit(100, datetime.timedelta(minutes=1)),
                rate_limiter.RateLimit(1000, datetime.timedelta(hours=1)),
            ],
            key_function=get_rate_limit_key,
        )


def _app_setup_request_lifecycle(app: base.QuartApp) -> None:
    """Setup application request lifecycle hooks."""
    import structlog

    logger = structlog.get_logger("atr.request")

    app.before_request(_apply_upload_body_timeout)

    @app.before_request
    async def bind_request_context_vars() -> None:
        await _reset_request_log_context()

    @app.before_request
    async def validate_session() -> None:
        """
        Check account is still active via periodic LDAP liveness checks.
        Absolute session max lifetime (MAX_SESSION_AGE) and idle timeout are
        enforced by the session store during validate().
        """
        session = await sessions.read()
        if not isinstance(session, sql.UserSession):
            return

        quart.g.is_session_downgraded = session.downgrade_admin_to_user

        conf = config.get()
        account_check_interval = conf.ACCOUNT_CHECK_INTERVAL

        # Check if session has a check timestamp
        last_check = session.last_account_check
        current_time = time.time()
        uid = session.uid

        if last_check is None or (current_time - last_check > account_check_interval):
            admin_uid = session.admin_uid

            if isinstance(admin_uid, str) and bool(admin_uid):
                user_active, admin_active = await asyncio.gather(
                    ldap.is_active(uid),
                    ldap.is_active(admin_uid),
                )
                if not admin_active:
                    await sessions.deleted_or_banned(admin_uid)
                    raise base.ASFQuartException("Account is disabled", errorcode=401)
            else:
                user_active = await ldap.is_active(uid)
            if not user_active:
                await sessions.deleted_or_banned(uid)
                raise base.ASFQuartException("Account is disabled", errorcode=401)

            session.last_account_check = current_time
            await asfquart.APP.sessions.save(session, {"last_account_check"})

        if last_check is None:
            log.auth_success("oauth", uid)

    @app.after_request
    async def log_request(response: quart.Response) -> quart.Response:
        # request_id is bound in _reset_request_log_context
        # atr.loggers.shared_processors merges it into this event
        logger.info(
            "request",
            method=quart.request.method,
            path=quart.request.path,
            status=response.status_code,
            remote_addr=quart.request.remote_addr,
            user_agent=quart.request.user_agent.string,
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

    @app.before_request
    async def validate_sec_fetch_headers() -> None:
        if quart.request.method not in ("GET", "HEAD", "OPTIONS"):
            sec_fetch_mode = quart.request.headers.get("Sec-Fetch-Mode")
            sec_fetch_site = quart.request.headers.get("Sec-Fetch-Site")

            # Apart from PAT hashes and PII, all data in ATR is public
            # Therefore we are only concerned here with non-GET API requests
            if (sec_fetch_mode == "navigate") and quart.request.path.startswith("/api/"):
                raise base.ASFQuartException(
                    "Forbidden: non-GET/HEAD/OPTIONS browser navigation to API endpoint", errorcode=403
                )

            # This is in addition to our existing CSRF protection
            if sec_fetch_site == "cross-site":
                raise base.ASFQuartException("Forbidden: cross-site non-GET/HEAD/OPTIONS request", errorcode=403)

    # X-Content-Type-Options: nosniff is required by ASVS v5 3.4.4 (L2)
    # A strict Referrer-Policy is required by ASVS v5 3.4.5 (L2)
    # HSTS is required by ASVS v5 9.2.1 (L1)
    # ASVS does not specify exactly what is meant by strict
    # We can't use Referrer-Policy: no-referrer because it breaks form redirection
    # TODO: We could automatically include a form field noting the form action URL
    @app.after_request
    async def add_security_headers(response: quart.Response) -> quart.Response:
        if response.headers.get("Cache-Control") is None:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = csp_header
        response.headers["Permissions-Policy"] = permissions_policy
        # audit_guidance we set Referrer-Policy: same-origin in our frontend proxy
        # audit_guidance we set X-Content-Type-Options: nosniff in our frontend proxy
        # audit_guidance we set X-Frame-Options: DENY in our frontend proxy
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        # audit_guidance we set Strict-Transport-Security: max-age=63072000; includeSubDomains in our frontend proxy
        return response


async def _app_shutdown_log_listeners(app):
    if storage_audit_listener := app.extensions.get("storage_audit_listener"):
        storage_audit_listener.stop()
    if auth_audit_listener := app.extensions.get("auth_audit_listener"):
        auth_audit_listener.stop()
    if request_listener := app.extensions.get("request_listener"):
        request_listener.stop()
    if listener := app.extensions.get("logging_listener"):
        listener.stop()
    log.listeners_stop()


async def _apply_upload_body_timeout() -> None:
    if not _uses_upload_body_timeout(quart.request.method, quart.request.path):
        return
    quart.request.body_timeout = config.get().UPLOAD_BODY_TIMEOUT


async def _backfill_archive_cache() -> None:
    backfill_results = await asyncio.to_thread(quarantine.backfill_archive_cache)
    if backfill_results:
        total_duration = sum(d for _, _, d in backfill_results)
        log.info(f"Backfilled {len(backfill_results)} archive cache entries in {total_duration:.1f}s")
        for archive_path, cache_dir, duration in backfill_results:
            log.info(f"  {cache_dir} ({duration:.1f}s) from {archive_path}")


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
    log.resources_init()
    app = _app_create_base(app_config)
    app.sessions = sessions.Store()
    jwtoken.setup_signing_key(app)

    _app_setup_api_docs(app)
    quart_wtf.CSRFProtect(app)

    _app_setup_rate_limits(app, app_config)
    _app_setup_logging(app, config_mode, app_config)
    db.init_database(app)
    _register_routes(app)
    blueprints.register(app)
    _unique_routes_check(app)
    filters.register_filters(app)
    _app_setup_context(app)
    _app_setup_security_headers(app)
    _app_setup_request_lifecycle(app)
    _app_setup_lifecycle(app, app_config)
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


def _enforce_archives_permissions(archives_dir: pathlib.Path) -> None:
    if not archives_dir.exists():
        return
    fixed_files = 0
    fixed_dirs = 0

    # Set ancestor directories of archive files to 755
    for dirpath, _, _ in os.walk(archives_dir, topdown=True):
        path = pathlib.Path(dirpath)
        depth = len(path.relative_to(archives_dir).parts)
        if depth < 3:
            os.chmod(path, 0o755)

    # Set archive files to 444
    for file_path in archives_dir.rglob("*"):
        if not file_path.is_file():
            continue
        depth = len(file_path.relative_to(archives_dir).parts)
        if (depth >= 3) and (stat.S_IMODE(file_path.stat().st_mode) != 0o444):
            os.chmod(file_path, 0o444)
            fixed_files += 1

    # Set archive directories to 555
    for dirpath, _, _ in os.walk(archives_dir, topdown=False):
        path = pathlib.Path(dirpath)
        depth = len(path.relative_to(archives_dir).parts)
        if (depth >= 3) and (stat.S_IMODE(path.stat().st_mode) != 0o555):
            os.chmod(path, 0o555)
            fixed_dirs += 1

    if (fixed_files > 0) or (fixed_dirs > 0):
        log.info(f"Fixed archive permissions: {fixed_files} files to 0o444, {fixed_dirs} directories to 0o555")


def _enforce_unfinished_permissions(unfinished_dir: pathlib.Path) -> None:
    # Set ancestor directories of revisions to 755
    for dirpath, _dirnames, _filenames in os.walk(unfinished_dir, topdown=True):
        path = pathlib.Path(dirpath)
        depth = len(path.relative_to(unfinished_dir).parts)
        if depth < 3:
            os.chmod(path, 0o755)

    # Set revision directories and their descendants to 555
    for dirpath, _dirnames, _filenames in os.walk(unfinished_dir, topdown=False):
        path = pathlib.Path(dirpath)
        depth = len(path.relative_to(unfinished_dir).parts)
        if depth >= 3:
            os.chmod(path, 0o555)


async def _initialise_pubsub(conf: type[config.AppConfig], app: base.QuartApp):
    pubsub_url = conf.PUBSUB_URL
    pubsub_user = conf.PUBSUB_USER
    pubsub_password = conf.PUBSUB_PASSWORD
    parsed_pubsub_url = urllib.parse.urlparse(pubsub_url) if pubsub_url else None
    valid_pubsub_url = bool(parsed_pubsub_url and parsed_pubsub_url.scheme and parsed_pubsub_url.netloc)

    if valid_pubsub_url and pubsub_url and pubsub_user and pubsub_password:
        log.info("Starting PubSub listener")
        listener = pubsub.PubSubListener(
            url=pubsub_url,
            username=pubsub_user,
            password=pubsub_password,
        )
        task = asyncio.create_task(listener.start())
        app.extensions["pubsub_listener"] = task
        log.info("PubSub listener task created")
    else:
        log.info(
            "PubSub listener not started: "
            f"pubsub_url={bool(valid_pubsub_url)} "
            f"pubsub_user={bool(pubsub_user)} "
            # Essential to use bool(...) here to avoid logging the password
            # TODO: We plan to add secret scanning when we migrate to t-strings
            f"pubsub_password={bool(pubsub_password)}",
        )


async def _initialise_test_environment(conf: type[config.AppConfig]) -> None:
    if not config.is_test_mode():
        return

    async with db.session() as data:
        test_committee = await data.committee(key="test").get()
        if not test_committee:
            test_committee = sql.Committee(
                key="test",
                name="Test Committee",
                is_podling=False,
                committee_members=["test"],
                committers=["test"],
                release_managers=["test"],
            )
            data.add(test_committee)
            await data.commit()

        test_project = await data.project(key="test").get()
        if not test_project:
            test_project = sql.Project(
                key="test",
                name="Apache Test",
                status=sql.ProjectStatus.ACTIVE,
                committee_key="test",
                created=datetime.datetime.now(datetime.UTC),
                created_by="test",
            )
            data.add(test_project)
            await data.commit()

        test_client_project = await data.project(key="test-client").get()
        if not test_client_project:
            test_client_project = sql.Project(
                key="test-client",
                name="Apache Test Client",
                status=sql.ProjectStatus.ACTIVE,
                committee_key="test",
                created=datetime.datetime.now(datetime.UTC),
                created_by="test",
            )
            data.add(test_client_project)
            await data.commit()

        # A podling equivalent, so we can exercise incubator/podling behaviours in tests
        test_podling_committee = await data.committee(key="test-podling").get()
        if not test_podling_committee:
            test_podling_committee = sql.Committee(
                key="test-podling",
                name="Test Podling",
                is_podling=True,
                committee_members=["test"],
                committers=["test"],
                release_managers=["test"],
            )
            data.add(test_podling_committee)
            await data.commit()

        test_podling_project = await data.project(key="test-podling").get()
        if not test_podling_project:
            test_podling_project = sql.Project(
                key="test-podling",
                name="Apache Test Podling",
                status=sql.ProjectStatus.ACTIVE,
                committee_key="test-podling",
                created=datetime.datetime.now(datetime.UTC),
                created_by="test",
            )
            data.add(test_podling_project)
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
    await tasks.clear_scheduled()
    # Run maintenance task immediately on server startup
    maintenance = await tasks.run_maintenance(asf_uid=constants.SYSTEM_SERVICE_UID, schedule_next=True)
    log.info(f"Scheduled maintenance with ID {maintenance.id}")
    # Start other tasks 5 min after server start
    await asyncio.sleep(300)
    try:
        await asyncio.sleep(60)
        metadata = await tasks.metadata_update(asf_uid=constants.SYSTEM_SERVICE_UID, schedule_next=True)
        log.info(f"Scheduled remote metadata update with ID {metadata.id}")
        await asyncio.sleep(60)
        workflow = await tasks.workflow_update(asf_uid=constants.SYSTEM_SERVICE_UID, schedule_next=True)
        log.info(f"Scheduled workflow status update with ID {workflow.id}")
        await asyncio.sleep(60)
        dist_check = await tasks.distribution_status_check(asf_uid=constants.SYSTEM_SERVICE_UID, schedule_next=True)
        log.info(f"Scheduled distribution status update with ID {dist_check.id}")

    except Exception as e:
        log.exception(f"Failed to schedule recurrent tasks: {e!s}")


def _register_routes(app: base.QuartApp) -> None:  # noqa: C901
    # Preserve HTTP status codes for errors that are raised outside blueprint-specific handlers
    @app.errorhandler(exceptions.HTTPException)
    async def handle_http_exception(error: exceptions.HTTPException) -> Any:
        status_code = errors.response_status_code(error)
        name = error.name or "HTTP error"
        description = error.description or name
        message = f"{status_code} {name}: {description}"
        if status_code >= 500:
            log.error("HTTP server exception", exc_info=(type(error), error, error.__traceback__))
        if quart.request.path.startswith("/api"):
            payload = {"error": message}
            payload.update(log.request_context_fields())
            payload.update(errors.traceback_fields(error, status_code))
            return quart.jsonify(payload), status_code
        return await template.render(
            "error.html",
            error=message,
            # audit_guidance HTML tracebacks expose public ATR code locations but not frame locals
            traceback=errors.traceback_text(error) if errors.should_show_traceback(status_code) else "",
            request_id=log.get_request_id(),
            status_code=status_code,
        ), status_code

    @app.errorhandler(Exception)
    async def handle_any_exception(error: Exception) -> Any:
        exc_info = (type(error), error, error.__traceback__)
        message = errors.message(error)

        if quart.request.path.startswith("/api"):
            log.error("Unhandled exception", exc_info=exc_info)
            payload = {"error": message}
            payload.update(log.request_context_fields())
            payload.update(errors.traceback_fields(error, 500))
            return quart.jsonify(payload), 500

        log.error("Unhandled exception", exc_info=exc_info)
        return await template.render(
            "error.html",
            error=message,
            traceback=errors.traceback_text(error),
            request_id=log.get_request_id(),
            status_code=500,
        ), 500

    @app.errorhandler(base.ASFQuartException)
    async def handle_asfquart_exception(error: base.ASFQuartException) -> Any:
        status_code = errors.response_status_code(error)
        message = errors.message(error)
        if status_code >= 500:
            log.error("Application server exception", exc_info=(type(error), error, error.__traceback__))
        if quart.request.path.startswith("/api"):
            payload = {"error": message}
            payload.update(log.request_context_fields())
            payload.update(errors.traceback_fields(error, status_code))
            return quart.jsonify(payload), status_code
        return await template.render(
            "error.html",
            error=message,
            # audit_guidance HTML tracebacks expose public ATR code locations but not frame locals
            traceback=errors.traceback_text(error) if errors.should_show_traceback(status_code) else "",
            request_id=log.get_request_id(),
            status_code=status_code,
        ), status_code

    # Add a global error handler for payload too large which will normally be handled in front in httpd server
    @app.errorhandler(413)
    async def handle_payload_too_large(error: Exception) -> Any:
        log.error("Payload_too_large")
        log.error("Ignore any following stack traces from form parsing")
        if quart.request.path.startswith("/api"):
            payload = {"error": "413 Payload Too Large"}
            payload.update(log.request_context_fields())
            return quart.jsonify(payload), 413
        return await template.render(
            "error.html",
            error="413 Payload Too Large",
            traceback="",
            request_id=log.get_request_id(),
            status_code=413,
        ), 413

    @app.errorhandler(408)
    async def handle_request_timeout(error: Exception) -> Any:
        timeout = quart.request.body_timeout
        log.warning(f"Request timed out while reading request body after {timeout} seconds")
        message = f"408 Request Timeout: request body did not finish before the server timeout of {timeout} seconds."
        wants_json = quart.request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json"
        if quart.request.path.startswith("/api") or wants_json:
            payload = {"error": message}
            payload.update(log.request_context_fields())
            return quart.jsonify(payload), 408
        return await template.render(
            "error.html",
            error=message,
            traceback="",
            request_id=log.get_request_id(),
            status_code=408,
        ), 408

    # Add a global error handler in case a page does not exist.
    @app.errorhandler(404)
    async def handle_not_found(error: Exception) -> Any:
        # Serve JSON for API endpoints, HTML otherwise
        if quart.request.path.startswith("/api"):
            payload = {"error": "404 Not Found"}
            payload.update(log.request_context_fields())
            return quart.jsonify(payload), 404
        return await template.render(
            "notfound.html",
            error="404 Not Found",
            traceback="",
            request_id=log.get_request_id(),
            status_code=404,
        ), 404

    @app.errorhandler(429)
    async def handle_rate_limit(e):
        # Set up logging context since before_request doesn't run for rate-limited requests
        await _reset_request_log_context()

        if quart.request.path.startswith("/api"):
            payload = {
                "error": "rate_limit_exceeded",
                "detail": "Too many requests, please retry later.",
                "retry_after": getattr(e, "retry_after", None),
            }
            payload.update(log.request_context_fields())
            return quart.jsonify(payload), 429
        return await template.render(
            "error.html",
            error="429 Too Many Requests",
            traceback="",
            request_id=log.get_request_id(),
            status_code=429,
        ), 429


async def _reset_request_log_context():
    log.clear_context()
    log.add_context(request_id=str(uuid.uuid4()))
    log.add_context(source_ip=quart.request.remote_addr)
    session = await sessions.read()
    if isinstance(session, sql.UserSession):
        log.add_context(user_id=session.uid)
        if session.admin_uid:
            log.add_context(admin_id=session.admin_uid)
    elif hasattr(quart.g, "jwt_claims"):
        claims = getattr(quart.g, "jwt_claims", {})
        asf_uid = claims.get("sub")
        log.add_context(user_id=asf_uid)


async def _resources_gauge_loop() -> None:
    while True:
        await asyncio.sleep(_RESOURCES_GAUGE_INTERVAL_SECONDS)
        try:
            rss, hwm = await asyncio.to_thread(util.proc_memory_kb, os.getpid())
            fds: int | str
            try:
                fds = len(await asyncio.to_thread(os.listdir, "/proc/self/fd"))
            except OSError:
                fds = "?"
            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            log.resources(
                f"app rss={'?' if (rss is None) else rss} hwm={'?' if (hwm is None) else hwm} fds={fds}"
                f" cutime={children.ru_utime:.3f} cstime={children.ru_stime:.3f}"
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Failed to log app resources gauge")


def _set_file_permissions_to_read_only() -> None:
    """Set permissions of all files in the unfinished and finished directories to read only."""
    # TODO: After a migration period, incorrect permissions should be an error
    directories = [pathlib.Path(paths.get_unfinished_dir()), pathlib.Path(paths.get_finished_dir())]
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


def _unique_routes_check(app: base.QuartApp) -> None:
    seen: dict[tuple[str, str], list[str]] = {}
    for rule in app.url_map.iter_rules():
        for method in (rule.methods or set()) - {"HEAD", "OPTIONS"}:
            seen.setdefault((method, rule.rule), []).append(rule.endpoint)
    duplicates = {key: endpoints for key, endpoints in seen.items() if len(endpoints) > 1}
    if duplicates:
        details = "; ".join(f"{method} {path} -> {endpoints}" for (method, path), endpoints in duplicates.items())
        raise RuntimeError(f"Duplicate route registrations detected: {details}")


def _uses_upload_body_timeout(method: str, path: str) -> bool:
    if method != "POST":
        return False
    return path.startswith("/upload/") or (path == "/api/release/upload")


def _validate_config(app_config: type[config.AppConfig], hot_reload: bool) -> None:
    config.validate()

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

    if (app_config.JWT_SECRET_KEY is not None) and (hot_reload is False):
        print("!!!", file=sys.stderr)
        print("WARNING: JWT_SECRET_KEY is no longer supported", file=sys.stderr)
        print("Please remove JWT_SECRET_KEY from secrets and environment", file=sys.stderr)
        print("ATR now uses secrets/generated/jwt_secret_key.txt", file=sys.stderr)
        print("!!!", file=sys.stderr)
        # sys.exit(1)

    if pathlib.Path(paths.get_downloads_dir()).is_dir() and (hot_reload is False):
        print("!!!", file=sys.stderr)
        print("WARNING: The downloads directory is no longer supported", file=sys.stderr)
        print("Please remove downloads from the state directory", file=sys.stderr)
        print("Announced release files are now published to SVN", file=sys.stderr)
        print("!!!", file=sys.stderr)


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
