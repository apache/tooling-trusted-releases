# 7.20. ASF modules

**Up**: `7.` [Developer guide](developer-guide)

**Prev**: `7.19.` [API documentation policy](api-documentation-policy)

**Next**: `7.21.` [Resource management](resource-management)

**Sections**:

* [Overview](#overview)
* [ASFQuart](#asfquart)
* [asfpy](#asfpy)

## Overview

ATR makes use of source modules maintained by ASF, rather than only on third-party packages from
PyPI. This page records which ASF-maintained modules ATR depends on, and how ATR uses each of them.

At present there is a single direct dependency of this kind:
[ASFQuart](https://github.com/apache/infrastructure-asfquart), which forms the foundation of ATR's
web application. ASFQuart in turn brings in [asfpy](https://github.com/apache/infrastructure-asfpy)
as a transitive dependency, but ATR does not use asfpy directly. The two are covered in turn below.

## ASFQuart

[ASFQuart](https://github.com/apache/infrastructure-asfquart) is a shared framework library
maintained by ASF Infrastructure that provides common functionality for ASF web applications built
on [Quart](https://quart.palletsprojects.com/) (an async Python web framework). ATR uses ASFQuart as
the foundation of its web application, relying on it for application construction, session
management, OAuth integration with the ASF identity provider, and authentication enforcement.

ASFQuart is not an ATR dependency in the usual sense — it is infrastructure-level code shared across
multiple ASF services. ATR does not modify or vendor ASFQuart; it uses the library as published.
This section describes what ATR uses from ASFQuart and what security-relevant behaviour is inherited
from it.

### Application construction

ATR creates its application instance via `asfquart.construct()` in
[`server.py`](/ref/atr/server.py). This call produces a `QuartApp` instance (a subclass of
`quart.Quart`) with ASF-standard defaults applied:

```python
app = asfquart.construct(
    __name__,
    app_dir=app_config.STATE_DIR,
    token_file="secrets/generated/apptoken.txt",
    basic_auth=False,
)
```

The `construct()` function performs the following setup that ATR inherits:

* Creates the Quart application with a persistent secret key (see
  [Application secret and session encryption](#application-secret-and-session-encryption))
* Sets secure cookie defaults: `SameSite=Strict`, `Secure=True`, `HttpOnly=True`
* Registers a URL converter for filenames (`FilenameConverter`)
* Sets up the ASF OAuth endpoint at the default `/auth` URI
* Registers an error handler that redirects unauthenticated users to the OAuth login flow

Before the call, ATR sets two of ASFQuart's OAuth settings (see
[OAuth authentication](#oauth-authentication)). After it, ATR applies its own configuration via
`app.config.from_object()`, taking care to preserve the ASFQuart-generated secret key.

### Session management

ASFQuart can keep a session in one of two places: in the session cookie itself, or in a session
store that the application supplies. ATR supplies a store. In [`server.py`](/ref/atr/server.py) it
sets `app.sessions` to the `Store` class of [`sessions.py`](/ref/atr/sessions.py), which keeps each
session as a [`UserSession`](/ref/atr/models/sql.py) row in ATR's database. See
[Sessions](sessions) for what a session holds and how long it lasts.

ASFQuart generates the session identifier, keeps it in the Quart session cookie under the
application ID, and calls the store with a hash of it. The store never sees the identifier itself.
What is stored against the hash, and for how long, is up to ATR.

#### Reading sessions

ATR reads the session through `sessions.read()`, which calls `asfquart.session.read()`. With a
store in place that function:

1. Looks up the session identifier in the cookie
2. Calls `Store.validate` with the hash of the identifier
3. Returns the `UserSession` that the store gives it, or removes the cookie entry and returns
   `None` if the store has no such session

What ASFQuart expects of a session object is listed in `asfquart.session.SessionProtocol`: a `uid`,
the flags `is_member`, `is_chair`, `is_root`, `is_role` and `mfa`, and `committees`. `UserSession`
has all of these. Its `committees` is a property that returns `member_committees`. Pyright checks
`UserSession` against the protocol where ATR hands a session to ASFQuart.

Requests with an `Authorization: Bearer` header do not go through this path. ATR verifies its own
JWTs in [`jwtoken.py`](/ref/atr/jwtoken.py).

#### Writing and clearing sessions

ASFQuart calls the store at three points:

* **Login.** After the OAuth code exchange ASFQuart calls `asfquart.session.awrite()` with the data
  from the OAuth service, which destroys any session the browser already had and calls
  `Store.create`. ATR maps that data onto the `UserSession` fields there and discards the rest.
* **Replacement.** ATR calls `asfquart.session.areplace()`, through `util.write_session`, to put a
  session that it has built itself in place of the current one. Administrator impersonation and the
  mock logins of test mode use it. ASFQuart issues a new identifier and calls `Store.replace`.
  During impersonation the administrator's own UID is kept in the `admin_uid` field.
* **Logout.** `asfquart.session.aclear()` calls `Store.destroy` and removes the cookie entry. The
  OAuth endpoint calls it for a logout, and ATR calls it after revoking a user's sessions.

The store has methods that ASFQuart does not call and that ATR uses directly: `revoke_by_uid`,
`save` and `current`.

#### Session expiry

With a session store, ASFQuart applies no expiry of its own. ATR enforces two limits in
`Store.validate`:

* **Inactivity**: a session not used for three days is deleted
* **Maximum lifetime**: a session older than `MAX_SESSION_AGE` (72 hours by default) is deleted

### OAuth authentication

ATR uses ASFQuart's generic OAuth endpoint (`asfquart.generics.setup_oauth`) to handle the ASF OAuth
login flow. The endpoint is registered at `/auth` and handles:

* `/auth?login` — initiates the OAuth flow by generating a cryptographic state token
  (`secrets.token_hex(16)`) and redirecting to the ASF OAuth service
* `/auth?login=/path` — same as above, but redirects the user to `/path` after successful login
* `/auth?logout` — clears the user's session
* `/auth?state=...&code=...` — callback from the ASF OAuth service that exchanges the authorization
  code for user data

ATR uses ASFQuart's default OAuth URLs, which are the OIDC endpoints of `oauth.apache.org`.
ASFQuart sends the authorization code to the OAuth service in the body of a `POST`, which keeps it
out of URLs that get logged.

ATR sets two of ASFQuart's OAuth settings in [`server.py`](/ref/atr/server.py), before the
application is constructed:

```python
asfquart.generics.OAUTH_CALLBACK_HOST = f"https://{app_config.APP_HOST}"
asfquart.generics.OAUTH_LOGOUT_BY_GET = False
```

#### Callback host

`OAUTH_CALLBACK_HOST` sets the host that the OAuth service sends the browser back to. Without it
ASFQuart takes the host from the `Host` header of the request that started the login, which the
client chooses. ATR uses `APP_HOST` here, as it does for the links in the mail it sends. The server
does not start if the value is not `https://` followed by a host name and an optional port.

An OAuth login therefore only completes on `APP_HOST`. One started on another host, such as
`https://127.0.0.1:8080/` in development, is sent back to `APP_HOST` and refused there, because the
cookie that ties a login to the browser belongs to the host it was started on. The mock logins of
test mode do not go through OAuth and work on any host.

#### Logging out

The "Log out" button in the navigation bar is a form, and with `OAUTH_LOGOUT_BY_GET = False` a
`GET /auth?logout` is answered with a 405.

The `POST` goes through ATR's CSRF check like any other form, so a logout that another site asks
for is a 400. ASFQuart makes a check of its own after that: a logout with a `Sec-Fetch-Site` header
other than `same-origin` is a 403. A request without the header, as from a client that is not a
browser, passes that check.

The OAuth flow has a configurable workflow timeout (default 15 minutes) after which pending state
tokens expire. State tokens are consumed immediately on use to prevent replay. The callback enforces
HTTPS on the redirect URI and always uses a `Refresh` header (instead of a 302 redirect) when
returning the user after login, in case `SameSite=Strict` is set, since the `Refresh` header
approach counts as a same-site navigation, preserving the cookie.

ASFQuart also sets up a login enforcement handler (`enforce_login`) that intercepts
`AuthenticationFailed` exceptions and redirects unauthenticated users to the OAuth flow. This
handler respects the `X-No-Redirect` header and `Authorization` headers, so API clients receive
error responses rather than HTML redirects.

#### Open redirect prevention

The OAuth endpoint validates redirect URIs in both login and logout flows: URIs must start with a
single `/`, must not start with `//`, and may not contain a backslash or control characters. This
prevents open redirect attacks where an attacker crafts a login URL that redirects to an external
site after authentication. A login may not redirect to the OAuth endpoint itself either, as
`/auth?login=/auth?logout` would otherwise be a way round the logout rules.

### Authentication decorators

ATR uses ASFQuart's `auth.require` decorator to enforce authentication on web routes. The decorator
is applied in ATR's blueprint modules ([`blueprints/get.py`](/ref/atr/blueprints/get.py) and
[`blueprints/post.py`](/ref/atr/blueprints/post.py)):

```python
decorated = wrapper if public else auth.require(auth.Requirements.committer)(wrapper)
```

The `auth.require` decorator reads the current session via `asfquart.session.read()` and raises
`AuthenticationFailed` (HTTP 403) if no valid session exists or if the specified requirement is not
met. ATR uses only the `Requirements.committer` check, which verifies that any authenticated session
exists. Further authorization (PMC membership, project participation) is handled by ATR's own
[`principal`](/ref/atr/principal.py) module and storage layer.

### Exception handling

ATR uses ASFQuart's `ASFQuartException` class as a general-purpose HTTP error exception throughout
its codebase. The exception carries a message and an HTTP status code. ATR raises it for
authentication failures (401), authorization failures (403), validation errors, and other HTTP error
conditions.

ATR registers error handlers for `ASFQuartException` in both the main application
([`server.py`](/ref/atr/server.py)) and the API blueprint
([`blueprints/api.py`](/ref/atr/blueprints/api.py)). The API handler returns JSON error responses;
the main handler renders HTML error pages.

### Application secret and session encryption

ASFQuart manages the Quart `secret_key`, which is used to sign session cookies. The `QuartApp`
constructor either reads the secret from a token file or generates a new one:

* If the token file exists (`secrets/generated/apptoken.txt` in ATR's case), the secret is read from
  it
* If the token file does not exist, a new secret is generated via `secrets.token_hex()` and written
  to the file with mode `0o600` (owner read/write only)
* If the file cannot be written (permission error), the secret is generated in memory only, meaning
  sessions will not survive server restarts

ASFQuart warns (to stderr) if the token file has permissions other than `0o600`. ATR preserves the
ASFQuart-generated secret key when applying its own configuration, since `app.config.from_object()`
would otherwise overwrite it.

### JWT signing key storage

ATR stores its JWT signing key in the Quart application's `extensions` dict, which is provided by
the `QuartApp` instance from ASFQuart. The [`jwtoken`](/ref/atr/jwtoken.py) module calls
`asfquart.APP.extensions` to read and write the signing key. This is standard Quart functionality
exposed through ASFQuart's application instance.

### Application extensions

ATR uses the `QuartApp.extensions` dict (a standard Quart feature inherited from ASFQuart) to store
application-wide cached data including the admin user set, project version cache, and JWT signing
key. These are accessed via `asfquart.APP.extensions` throughout the codebase.

### What ATR does not use from ASFQuart

ASFQuart provides several capabilities that ATR does not use:

* **LDAP authentication** (`asfquart.ldap`): ASFQuart supports HTTP Basic authentication against ASF
  LDAP. It is off by default, and ATR also passes `basic_auth=False`. An `Authorization: Basic`
  header is answered with a 401 and LDAP is not contacted. ATR performs its own LDAP lookups via
  its [`ldap`](/ref/atr/ldap.py) module for authorization data.
* **Bearer token handler** (`app.token_handler`): ASFQuart supports registering a callback for
  `Authorization: Bearer` tokens. ATR implements its own JWT verification in
  [`jwtoken.py`](/ref/atr/jwtoken.py) rather than using this mechanism.
* **EZT template watcher** (`app.tw`): ASFQuart includes a file-watching template system based on
  EZT. ATR uses Jinja2 templates with its own preloading system instead.
* **YAML configuration** (`asfquart.config`): ASFQuart provides a YAML config reader. ATR uses its
  own [`config`](/ref/atr/config.py) module with Python class-based configuration.
* **Development server** (`app.runx()`): ASFQuart provides an extended development server with hot
  reloading. ATR uses Hypercorn directly.

### Security considerations for auditors

When auditing ATR's authentication and session handling, be aware that several security-critical
behaviours originate in ASFQuart rather than in ATR's own code:

* **Session cookie attributes** (`SameSite`, `Secure`, `HttpOnly`) are set by
  `asfquart.construct()`. ATR's [`config.py`](/ref/atr/config.py) sets the same values, and the
  `__Host-session` cookie name.
* **Session identifiers** are generated and hashed by `asfquart.session`. Session storage, expiry
  and revocation are ATR's, in [`sessions.py`](/ref/atr/sessions.py).
* **OAuth state management** (generation, validation, timeout, single-use) is handled by
  `asfquart.generics.setup_oauth()`.
* **Open redirect prevention** on login/logout URIs is enforced by `asfquart.generics`.
* **The OAuth callback URL** is built by `asfquart.generics`, on the `APP_HOST` that ATR gives it.
* **Logout** is handled by `asfquart.generics`, which refuses a `GET` and a request from another
  origin. ATR's CSRF and Fetch Metadata checks apply to the `POST` as well.
* **Secret key persistence and file permissions** are managed by
  `asfquart.base.QuartApp.__init__()`.
* **Authentication gating** on web routes uses `asfquart.auth.require`, which reads the session and
  raises `AuthenticationFailed`.

The source code for ASFQuart is maintained at
[github.com/apache/infrastructure-asfquart](https://github.com/apache/infrastructure-asfquart).

## asfpy

[asfpy](https://github.com/apache/infrastructure-asfpy) is ASF Infrastructure's general-purpose
Python utility library, offering helpers for LDAP, messaging, and PubSub among other things. It
reaches ATR only as a transitive dependency of ASFQuart, which requires it — ATR declares no direct
dependency on asfpy and imports nothing from it.

Two parts of ATR cover ground that asfpy also covers, and in both cases ATR uses its own
implementation rather than asfpy's:

* **Mail**: ATR sends signed mail through its own [`mail`](/ref/atr/mail.py) module. As the code
  notes, this is deliberately very different from the asfpy version — it is typed and built on
  `asyncio`, and ATR's need to sign a message before sending does not fit asfpy's design.
* **PubSub**: ATR consumes the ASF PubSub stream through its own [`pubsub`](/ref/atr/pubsub.py)
  module rather than through `asfpy.pubsub`.

Because ATR does not call into asfpy, it would be unaffected if ASFQuart were to drop asfpy from its
own dependencies in future.
