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

import enum
import ipaddress
import logging
import os
import pathlib
import urllib.parse
from typing import Final

import decouple

import atr.sandbox as sandbox

_MB: Final = 1024 * 1024
_GB: Final = 1024 * _MB
_RAT_VERSION: Final = "0.18"


def _config_secrets(key: str, state_dir: str, default: str | None = None, cast: type = str) -> str | None:
    secrets_path = os.path.join(state_dir, "secrets", "curated", "secrets.ini")
    return _config_secrets_get(secrets_path, key, default, cast)


def _config_secrets_get(secrets_path: str, key: str, default: str | None = None, cast: type = str) -> str | None:
    sentinel = object()
    # We do not use the cast keyword argument here
    # If we did, it would also be applied to the default sentinel value
    value = decouple.config(key, default=sentinel)
    if value is not sentinel:
        if not isinstance(value, str):
            raise ValueError(f"Secret value for {key} is not a string")
        try:
            decouple.RepositoryIni(secrets_path)[key]
        except (FileNotFoundError, KeyError):
            pass
        else:
            logging.warning(f"Secret {key} from the environment overrides the value in {secrets_path}")
        return cast(value)

    try:
        repo_ini = decouple.RepositoryIni(secrets_path)
    except FileNotFoundError:
        return default
    try:
        return cast(repo_ini[key])
    except KeyError:
        return default


def _svn_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_no_root_secrets(conf: type["AppConfig"]) -> None:
    if os.path.isfile(os.path.join(conf.STATE_DIR, "secrets.ini")):
        raise RuntimeError(
            "A secrets.ini file in the state directory root is no longer supported; "
            "move it to secrets/curated/secrets.ini, or delete it if already migrated"
        )


def _validate_state_dir(conf: type["AppConfig"]) -> None:
    state_dir = pathlib.Path(conf.STATE_DIR).resolve()
    for path in sandbox.SYSTEM_PATHS:
        if state_dir.is_relative_to(path):
            raise RuntimeError(f"STATE_DIR must not be under the sandbox system path {path}")


def _validate_svn_dist_public_url(url: str) -> None:
    public_path = urllib.parse.urlparse(url).path.rstrip("/")
    if not public_path.endswith(("/atr", "/release")):
        raise RuntimeError("SVN_DIST_PUBLIC_URL must be an atr or release dist URL")


def _validate_svn_publish(conf: type["AppConfig"]) -> None:
    if not conf.SVN_PUBLISH_URL:
        raise RuntimeError("SVN_PUBLISH_URL must be configured; in development, run make svn-dev-repo")
    if not conf.SVN_TOKEN:
        raise RuntimeError("SVN_TOKEN must be configured; in development, any value works")
    try:
        kind = svn_publish_kind()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if (not is_production_mode()) and (kind is not SvnPublishKind.LOCAL_REPOSITORY):
        raise RuntimeError("SVN_PUBLISH_URL must be a local repository outside of production; run make svn-dev-repo")


class AppConfig:
    ACCOUNT_CHECK_INTERVAL = decouple.config("ACCOUNT_CHECK_INTERVAL", default=300, cast=int)
    ATR_STATUS = decouple.config("ATR_STATUS", default="ALPHA", cast=str)
    DISABLE_CHECK_CACHE = decouple.config("DISABLE_CHECK_CACHE", default=False, cast=bool)
    APP_HOST = decouple.config("APP_HOST", default="127.0.0.1")
    SSH_HOST = decouple.config("SSH_HOST", default="0.0.0.0")
    SSH_PORT = decouple.config("SSH_PORT", default=2222, cast=int)
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    STATE_DIR = decouple.config("STATE_DIR", default=os.path.join(PROJECT_ROOT, "state"))
    LDAP_BIND_DN = _config_secrets("LDAP_BIND_DN", STATE_DIR, default=None, cast=str)
    LDAP_BIND_PASSWORD = _config_secrets("LDAP_BIND_PASSWORD", STATE_DIR, default=None, cast=str)
    LOG_LEVEL = decouple.config("LOG_LEVEL", default="INFO", cast=lambda x: x.upper())
    LOG_JSON = decouple.config("LOG_JSON", default=False, cast=bool)
    LOG_PUBLIC_KEY = _config_secrets("LOG_PUBLIC_KEY", STATE_DIR, default=None, cast=str)
    MAX_SESSION_AGE = decouple.config("MAX_SESSION_AGE", default=60 * 60 * 72, cast=int)
    PUBSUB_URL = _config_secrets("PUBSUB_URL", STATE_DIR, default=None, cast=str)
    PUBSUB_USER = _config_secrets("PUBSUB_USER", STATE_DIR, default=None, cast=str)
    PUBSUB_PASSWORD = _config_secrets("PUBSUB_PASSWORD", STATE_DIR, default=None, cast=str)
    # The dist pubsub watcher catalogues non-ATR releases it sees. Off by default, so
    # it runs in report mode (logs what it would create); set true to write to the db.
    DIST_CATALOG_WRITE = decouple.config("DIST_CATALOG_WRITE", default=False, cast=bool)
    SVN_TOKEN = _config_secrets("SVN_TOKEN", STATE_DIR, default=None, cast=str)
    SVN_PUBLISH_URL = _config_secrets("SVN_PUBLISH_URL", STATE_DIR, default=None, cast=str)
    SVN_DIST_PUBLIC_URL = decouple.config("SVN_DIST_PUBLIC_URL", default="https://dist.apache.org/repos/dist/atr")
    GITHUB_TOKEN = _config_secrets("GITHUB_TOKEN", STATE_DIR, default=None, cast=str)
    CAP_API_BASE_URL = decouple.config("CAP_API_BASE_URL", default="https://cap-test.apache.org")
    CAP_ROLE_ACCOUNT_TOKEN = _config_secrets("CAP_ROLE_ACCOUNT_TOKEN", STATE_DIR, default=None, cast=str)
    RELEASE_CATALOG_URL = _config_secrets("RELEASE_CATALOG_URL", STATE_DIR, default="https://catalog.apache.org/")

    DEBUG = False
    TEMPLATES_AUTO_RELOAD = False
    USE_BLOCKBUSTER = False
    # We no longer support SECRET_KEY or JWT_SECRET_KEY
    # We continue to read both values to print migration warnings
    # For SECRET_KEY we are now relying on apptoken.txt from ASFQuart instead
    # By default, apptoken.txt is a 256 bit random value
    # ASFQuart generates it using secrets.token_hex()
    SECRET_KEY = _config_secrets("SECRET_KEY", STATE_DIR, default=None, cast=str)
    JWT_SECRET_KEY = _config_secrets("JWT_SECRET_KEY", STATE_DIR, default=None, cast=str)
    FINISHED_STORAGE_DIR = os.path.join(STATE_DIR, "finished")
    UNFINISHED_STORAGE_DIR = os.path.join(STATE_DIR, "unfinished")
    # TODO: By convention this is at /x1/, but we can symlink it here perhaps?
    # TODO: We need to get Puppet to check SVN out initially, or do it manually
    SVN_STORAGE_DIR = os.path.join(STATE_DIR, "subversion")
    ARCHIVES_STORAGE_DIR = os.path.join(STATE_DIR, "archives")
    ATTESTABLE_STORAGE_DIR = os.path.join(STATE_DIR, "attestable")
    # The static release catalog site, rebuilt from the catalogue on each change
    CATALOG_SITE_DIR = os.path.join(STATE_DIR, "catalog-site")
    SQLITE_DB_PATH = decouple.config("SQLITE_DB_PATH", default="database/atr.db")
    STORAGE_AUDIT_LOG_FILE = os.path.join(STATE_DIR, "audit", "storage-audit.log")
    AUTH_AUDIT_LOG_FILE = os.path.join(STATE_DIR, "audit", "auth-audit.log")
    PERFORMANCE_LOG_FILE = os.path.join(STATE_DIR, "logs", "route-performance.log")
    REQUEST_LOG_FILE = os.path.join(STATE_DIR, "logs", "requests.log")
    TASK_LOG_FILE = os.path.join(STATE_DIR, "logs", "tasks.log")

    # Apache RAT configuration
    APACHE_RAT_JAR_PATH = decouple.config("APACHE_RAT_JAR_PATH", default=f"/opt/tools/apache-rat-{_RAT_VERSION}.jar")
    # Maximum content length for requests
    MAX_CONTENT_LENGTH: int = decouple.config("MAX_CONTENT_LENGTH", default=512 * _MB, cast=int)
    # Maximum duration to receive upload request bodies
    UPLOAD_BODY_TIMEOUT: int = decouple.config("UPLOAD_BODY_TIMEOUT", default=3600, cast=int)
    # Maximum size limit for archive extraction
    MAX_EXTRACT_SIZE: int = decouple.config("MAX_EXTRACT_SIZE", default=2 * _GB, cast=int)
    # Chunk size for reading files during extraction
    EXTRACT_CHUNK_SIZE: int = decouple.config("EXTRACT_CHUNK_SIZE", default=4 * _MB, cast=int)

    # Session cookie security
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_NAME = "__Host-session"

    # CSRF time limit
    WTF_CSRF_TIME_LIMIT = None

    ADMIN_USERS_ADDITIONAL = decouple.config("ADMIN_USERS_ADDITIONAL", default="", cast=str)
    TOOLING_USERS_ADDITIONAL = decouple.config("TOOLING_USERS_ADDITIONAL", default="", cast=str)


class DebugConfig(AppConfig):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True
    USE_BLOCKBUSTER = False


class Mode(enum.Enum):
    Debug = "Debug"
    Test = "Test"
    Production = "Production"
    Profiling = "Profiling"


_global_mode: Mode | None = None


class ProductionConfig(AppConfig):
    pass


class ProfilingConfig(AppConfig):
    DEBUG = False
    TEMPLATES_AUTO_RELOAD = False
    USE_BLOCKBUSTER = True


class SvnPublishKind(enum.Enum):
    ASF_DISTRIBUTION = "asf_distribution"
    LOCAL_REPOSITORY = "local_repository"


class TestConfig(DebugConfig):
    pass


# Load all possible configurations
_CONFIG_DICT: Final = {
    Mode.Debug: DebugConfig,
    Mode.Test: TestConfig,
    Mode.Production: ProductionConfig,
    Mode.Profiling: ProfilingConfig,
}


def get() -> type[AppConfig]:
    return _CONFIG_DICT[get_mode()]


def get_mode() -> Mode:
    global _global_mode

    profiling = decouple.config("PROFILING", default=False, cast=bool)
    production = decouple.config("PRODUCTION", default=False, cast=bool)
    test = decouple.config("TESTS", default=False, cast=bool)

    # Make sure we don't set more than one - which would fall back into whichever is first in the next conditional
    # This prevents accidental production in test mode, for example
    enabled = [name for name, val in [("PROFILING", profiling), ("PRODUCTION", production), ("TESTS", test)] if val]
    if len(enabled) > 1:
        exit(f"Only one mode flag may be set, but got: {', '.join(enabled)}")

    if _global_mode is None:
        if profiling:
            _global_mode = Mode.Profiling
        elif production:
            _global_mode = Mode.Production
        elif test:
            _global_mode = Mode.Test
        else:
            _global_mode = Mode.Debug

    return _global_mode


def is_dev_environment() -> bool:
    conf = get()
    for development_host in ("127.0.0.1", "atr", "atr-dev", "localhost.apache.org"):
        if (conf.APP_HOST == development_host) or conf.APP_HOST.startswith(f"{development_host}:"):
            return True
    return False


def is_ldap_configured() -> bool:
    conf = get()
    return bool(conf.LDAP_BIND_DN and conf.LDAP_BIND_PASSWORD)


def is_production_mode() -> bool:
    return get_mode() == Mode.Production


def is_test_mode() -> bool:
    return get_mode() == Mode.Test


def svn_publish_kind() -> SvnPublishKind:
    url = get().SVN_PUBLISH_URL
    if not url:
        raise ValueError("SVN_PUBLISH_URL is not configured")
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if (parsed.scheme == "https") and ((host == "apache.org") or host.endswith(".apache.org")):
        return SvnPublishKind.ASF_DISTRIBUTION
    if parsed.scheme == "file":
        return SvnPublishKind.LOCAL_REPOSITORY
    if (parsed.scheme == "svn") and _svn_loopback_host(host):
        return SvnPublishKind.LOCAL_REPOSITORY
    raise ValueError("SVN_PUBLISH_URL must be an https apache.org URL, a file URL, or a loopback svn URL")


def validate() -> None:
    """
    Runs validity and safety checks to ensure configuration is consistent and secure:

    Path checks - absolute and relative paths are set correctly
    Debug mode can only be set on a development URL (127.0.0.1, atr, etc.)
    LDAP must be configured in production
    Cannot set additional admins at runtime in production
    Dev URLs cannot be set in production mode
    """
    conf = get()

    _validate_no_root_secrets(conf)
    _validate_state_dir(conf)

    absolute_paths = [
        (conf.PROJECT_ROOT, "PROJECT_ROOT"),
        (conf.STATE_DIR, "STATE_DIR"),
        (conf.FINISHED_STORAGE_DIR, "FINISHED_STORAGE_DIR"),
        (conf.UNFINISHED_STORAGE_DIR, "UNFINISHED_STORAGE_DIR"),
        (conf.SVN_STORAGE_DIR, "SVN_STORAGE_DIR"),
        (conf.ARCHIVES_STORAGE_DIR, "ARCHIVES_STORAGE_DIR"),
        (conf.ATTESTABLE_STORAGE_DIR, "ATTESTABLE_STORAGE_DIR"),
        (conf.CATALOG_SITE_DIR, "CATALOG_SITE_DIR"),
        (conf.STORAGE_AUDIT_LOG_FILE, "STORAGE_AUDIT_LOG_FILE"),
        (conf.AUTH_AUDIT_LOG_FILE, "AUTH_AUDIT_LOG_FILE"),
        (conf.PERFORMANCE_LOG_FILE, "PERFORMANCE_LOG_FILE"),
        (conf.TASK_LOG_FILE, "TASK_LOG_FILE"),
    ]
    relative_paths = [
        (conf.SQLITE_DB_PATH, "SQLITE_DB_PATH"),
    ]

    for path, name in absolute_paths:
        if not path.startswith("/"):
            raise RuntimeError(f"{name} must be an absolute path")
    for path, name in relative_paths:
        if path.startswith("/"):
            raise RuntimeError(f"{name} must be a relative path")

    _validate_svn_dist_public_url(conf.SVN_DIST_PUBLIC_URL)
    _validate_svn_publish(conf)

    if (not is_dev_environment()) and (get_mode() == Mode.Debug):
        raise RuntimeError("Debug mode can only be set in development environment")

    # Production-specific guards
    if is_production_mode():
        if not (conf.LDAP_BIND_DN and conf.LDAP_BIND_PASSWORD):
            raise RuntimeError("LDAP bind credentials must be configured in production mode")
        if conf.ADMIN_USERS_ADDITIONAL or conf.TOOLING_USERS_ADDITIONAL:
            raise RuntimeError("Cannot manually configure additional users in production")
        if is_dev_environment():
            raise RuntimeError("Production mode cannot use a development APP_HOST")
