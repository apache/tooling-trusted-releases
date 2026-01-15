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
import os
import pathlib
import queue
import urllib.parse
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
import rich.logging as rich_logging
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


def main() -> None:
    """Quart debug server"""
    global app
    if app is None:
        app = _create_app(config.get())
    app.run(port=8080, ssl_keyfile="key.pem", ssl_certfile="cert.pem")


def _app_create_base(app_config: type[config.AppConfig]) -> base.QuartApp:
    """Create the base Quart application."""
    if asfquart.construct is ...:
        raise ValueError("asfquart.construct is not set")
    app = asfquart.construct(__name__)
    app.config.from_object(app_config)
    return app


def _app_dirs_setup(app_config: type[config.AppConfig]) -> None:
    """Setup application directories."""
    if not os.path.isdir(app_config.STATE_DIR):
        raise RuntimeError(f"State directory not found: {app_config.STATE_DIR}")
    os.chdir(app_config.STATE_DIR)
    print(f"Working directory changed to: {os.getcwd()}")

    directories_to_ensure = [
        pathlib.Path(app_config.STATE_DIR) / "audit",
        pathlib.Path(app_config.STATE_DIR) / "cache",
        pathlib.Path(app_config.STATE_DIR) / "database",
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


def _app_setup_lifecycle(app: base.QuartApp) -> None:
    """Setup application lifecycle hooks."""

    @app.before_serving
    async def startup() -> None:
        """Start services before the app starts serving requests."""
        if listener := app.extensions.get("logging_listener"):
            listener.start()

        worker_manager = manager.get_worker_manager()
        await worker_manager.start()

        # Register recurring tasks (metadata updates, workflow status checks, etc.)
        await _register_recurrent_tasks()

        await _initialise_test_environment()

        conf = config.get()
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

        ssh_server = await ssh.server_start()
        app.extensions["ssh_server"] = ssh_server

    @app.after_serving
    async def shutdown() -> None:
        """Clean up services after the app stops serving requests."""
        worker_manager = manager.get_worker_manager()
        await worker_manager.stop()

        # Stop the metadata scheduler
        # metadata_scheduler = app.extensions.get("metadata_scheduler")
        # if metadata_scheduler:
        #     metadata_scheduler.cancel()
        #     try:
        #         await metadata_scheduler
        #     except asyncio.CancelledError:
        #         ...

        ssh_server = app.extensions.get("ssh_server")
        if ssh_server:
            await ssh.server_stop(ssh_server)

        if task := app.extensions.get("svn_listener"):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if listener := app.extensions.get("logging_listener"):
            listener.stop()

        await db.shutdown_database()

        app.background_tasks.clear()


def _app_setup_logging(app: base.QuartApp, config_mode: config.Mode, app_config: type[config.AppConfig]) -> None:
    """Setup application logging."""
    import logging
    import logging.handlers

    console_handler = rich_logging.RichHandler(rich_tracebacks=True, show_time=False)
    log_queue = queue.Queue(-1)
    handlers: list[logging.Handler] = [console_handler]
    if (config_mode == config.Mode.Debug) and app_config.ALLOW_TESTS:
        handlers.append(log.create_debug_handler())
    listener = logging.handlers.QueueListener(log_queue, *handlers)
    app.extensions["logging_listener"] = listener

    logging.basicConfig(
        format="[ %(asctime)s.%(msecs)03d ] %(process)d <%(name)s> %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.handlers.QueueHandler(log_queue)],
        force=True,
    )

    # Configure dedicated audit logger
    try:
        audit_handler = logging.FileHandler(
            app_config.STORAGE_AUDIT_LOG_FILE,
            encoding="utf-8",
            mode="a",
        )
        # audit_handler.setFormatter(
        #     logging.Formatter("%(message)s")
        # )
        audit_queue = queue.Queue(-1)
        audit_listener = logging.handlers.QueueListener(audit_queue, audit_handler)
        audit_listener.start()
        app.extensions["audit_listener"] = audit_listener

        audit_logger = logging.getLogger("atr.storage.audit")
        audit_logger.setLevel(logging.INFO)
        audit_logger.addHandler(audit_handler)
        audit_logger.propagate = False
        audit_queue_handler = logging.handlers.QueueHandler(audit_queue)
        audit_logger.handlers = [audit_queue_handler]
    except Exception:
        logging.getLogger(__name__).exception("Failed to configure audit logger")

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
        return response


def _create_app(app_config: type[config.AppConfig]) -> base.QuartApp:
    """Create and configure the application."""
    if os.sep != "/":
        raise RuntimeError('ATR requires a POSIX compatible filesystem where os.sep is "/"')
    config_mode = config.get_mode()
    _migrate_state_directory(app_config)
    _app_dirs_setup(app_config)
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
    _app_setup_lifecycle(app)

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


def _get_parent_process_age() -> float:
    import datetime
    import subprocess
    import time

    ppid = os.getppid()

    try:
        with open(f"/proc/{ppid}/stat") as f:
            stat = f.read().split()
        starttime_ticks = int(stat[21])
        ticks_per_sec = os.sysconf("SC_CLK_TCK")
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    boot_time = int(line.split()[1])
                    break
            else:
                return 0.0
        process_start = boot_time + (starttime_ticks / ticks_per_sec)
        return time.time() - process_start
    except (FileNotFoundError, IndexError, ValueError, OSError):
        pass

    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(ppid)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            start_str = result.stdout.strip()
            for fmt in ["%a %b %d %H:%M:%S %Y", "%a %d %b %H:%M:%S %Y"]:
                try:
                    dt = datetime.datetime.strptime(start_str, fmt)
                    return time.time() - dt.timestamp()
                except ValueError:
                    continue
    except OSError:
        pass

    return 0.0


async def _initialise_test_environment() -> None:
    if not config.get().ALLOW_TESTS:
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


#
# async def _metadata_update_scheduler() -> None:
#     """Periodically schedule remote metadata updates."""
#     # Wait one minute to allow the server to start
#     await asyncio.sleep(60)
#
#     while True:
#         try:
#             task = await tasks.metadata_update(asf_uid="system")
#             log.info(f"Scheduled remote metadata update with ID {task.id}")
#         except Exception as e:
#             log.exception(f"Failed to schedule remote metadata update: {e!s}")
#
#         # Schedule next update in 24 hours
#         await asyncio.sleep(86400)


async def _register_recurrent_tasks() -> None:
    """Schedule recurring tasks"""
    # Wait one minute to allow the server to start
    await asyncio.sleep(30)
    try:
        await tasks.clear_scheduled()
        metadata = await tasks.metadata_update(asf_uid="system", schedule_next=True)
        log.info(f"Scheduled remote metadata update with ID {metadata.id}")
        workflow = await tasks.workflow_update(asf_uid="system", schedule_next=True)
        log.info(f"Scheduled workflow status update with ID {workflow.id}")

    except Exception as e:
        log.exception(f"Failed to schedule recurrent tasks: {e!s}")


def _migrate_audit(state_dir: pathlib.Path) -> None:
    _migrate_file(
        state_dir / "storage-audit.log",
        state_dir / "audit" / "storage-audit.log",
    )


def _migrate_cache(state_dir: pathlib.Path) -> None:
    _migrate_file(
        state_dir / "routes.json",
        state_dir / "cache" / "routes.json",
    )
    _migrate_file(
        state_dir / "user_session_cache.json",
        state_dir / "cache" / "user_session_cache.json",
    )


def _migrate_database(state_dir: pathlib.Path, app_config: type[config.AppConfig]) -> None:
    configured_path = app_config.SQLITE_DB_PATH
    if configured_path not in ("atr.db", "database/atr.db"):
        raise RuntimeError(
            f"SQLITE_DB_PATH is set to '{configured_path}' but migration only supports "
            f"the default value 'atr.db'. Please manually migrate your database to "
            f"'database/atr.db' and update SQLITE_DB_PATH, or remove the custom setting."
        )
    _migrate_file(
        state_dir / "atr.db",
        state_dir / "database" / "atr.db",
    )
    for suffix in ["-shm", "-wal"]:
        old_path = state_dir / f"atr.db{suffix}"
        new_path = state_dir / "database" / f"atr.db{suffix}"
        if old_path.exists():
            _migrate_file(old_path, new_path)


def _migrate_directory(old_path: pathlib.Path, new_path: pathlib.Path) -> None:
    if old_path.exists() and (not new_path.exists()):
        old_path.rename(new_path)
        print(f"Migrated directory: {old_path} -> {new_path}")
    elif old_path.exists() and new_path.exists():
        raise RuntimeError(f"Migration conflict: both {old_path} and {new_path} exist")
    else:
        print(f"No directory migration needed: {old_path}")


def _migrate_file(old_path: pathlib.Path, new_path: pathlib.Path) -> None:
    if old_path.exists() and (not new_path.exists()):
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        print(f"Migrated file: {old_path} -> {new_path}")
    elif old_path.exists() and new_path.exists():
        raise RuntimeError(f"Migration conflict: both {old_path} and {new_path} exist")
    else:
        print(f"No file migration needed: {old_path}")


def _migrate_state_directory(app_config: type[config.AppConfig]) -> None:
    state_dir = pathlib.Path(app_config.STATE_DIR)

    pending = _pending_migrations(state_dir, app_config)
    if pending:
        parent_age = _get_parent_process_age()
        if parent_age > 10.0:
            import sys

            print("=" * 70, file=sys.stderr)
            print("ERROR: State directory migration required but hot reload detected", file=sys.stderr)
            print(f"Parent process age: {parent_age:.1f}s", file=sys.stderr)
            print("Pending migrations:", file=sys.stderr)
            for p in pending:
                print(f"  - {p}", file=sys.stderr)
            print("", file=sys.stderr)
            print("Please restart the server, not hot reload, to apply migrations", file=sys.stderr)
            print("=" * 70, file=sys.stderr)
            sys.exit(1)

    runtime_dir = state_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_dir / "migration.lock"

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _migrate_audit(state_dir)
            _migrate_cache(state_dir)
            _migrate_database(state_dir, app_config)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _pending_migrations(state_dir: pathlib.Path, app_config: type[config.AppConfig]) -> list[str]:
    pending = []
    if (state_dir / "storage-audit.log").exists():
        pending.append("storage-audit.log -> audit/storage-audit.log")
    if (state_dir / "routes.json").exists():
        pending.append("routes.json -> cache/routes.json")
    if (state_dir / "user_session_cache.json").exists():
        pending.append("user_session_cache.json -> cache/user_session_cache.json")
    configured_path = app_config.SQLITE_DB_PATH
    if configured_path in ("atr.db", "database/atr.db"):
        if (state_dir / "atr.db").exists():
            pending.append("atr.db -> database/atr.db")
    return pending


def _register_routes(app: base.QuartApp) -> None:
    # Add a global error handler to show helpful error messages with tracebacks
    @app.errorhandler(Exception)
    async def handle_any_exception(error: Exception) -> Any:
        import traceback

        # If the request was made to the API, return JSON
        if quart.request.path.startswith("/api"):
            status_code = getattr(error, "code", 500) if isinstance(error, Exception) else 500
            return quart.jsonify({"error": str(error)}), status_code

        # Required to give to the error.html template
        tb = traceback.format_exc()
        log.exception("Unhandled exception")
        return await template.render("error.html", error=str(error), traceback=tb, status_code=500), 500

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

    # Add a global error handler in case a page does not exist.
    @app.errorhandler(404)
    async def handle_not_found(error: Exception) -> Any:
        # Serve JSON for API endpoints, HTML otherwise
        if quart.request.path.startswith("/api"):
            return quart.jsonify({"error": "404 Not Found"}), 404
        return await template.render("notfound.html", error="404 Not Found", traceback="", status_code=404), 404


if __name__ == "__main__":
    main()
else:
    app = _create_app(config.get())
