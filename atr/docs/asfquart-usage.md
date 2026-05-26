# 3.17. ASFQuart usage

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.16.` [API documentation policy](api-documentation-policy)

**Next**: (none)

**Sections**:

* [Overview](#overview)
* [Application construction](#application-construction)
* [Session management](#session-management)
* [OAuth authentication](#oauth-authentication)
* [Authentication decorators](#authentication-decorators)
* [Exception handling](#exception-handling)
* [Application secret and session encryption](#application-secret-and-session-encryption)
* [JWT signing key storage](#jwt-signing-key-storage)
* [Application extensions](#application-extensions)
* [What ATR does not use from ASFQuart](#what-atr-does-not-use-from-asfquart)
* [Security considerations for auditors](#security-considerations-for-auditors)

## Overview

[ASFQuart](https://github.com/apache/infrastructure-asfquart) is a shared framework library maintained by ASF Infrastructure that provides common functionality for ASF web applications built on [Quart](https://quart.palletsprojects.com/) (an async Python web framework). ATR uses ASFQuart as the foundation of its web application, relying on it for application construction, session management, OAuth integration with the ASF identity provider, and authentication enforcement.

ASFQuart is not an ATR dependency in the usual sense — it is infrastructure-level code shared across multiple ASF services. ATR does not modify or vendor ASFQuart; it uses the library as published. This document describes what ATR uses from ASFQuart and what security-relevant behaviour is inherited from it.

## Application construction

ATR creates its application instance via `asfquart.construct()` in [`server.py`](/ref/atr/server.py). This call produces a `QuartApp` instance (a subclass of `quart.Quart`) with ASF-standard defaults applied:

```python
app = asfquart.construct(__name__, token_file="secrets/generated/apptoken.txt")
```

The `construct()` function performs the following setup that ATR inherits:

* Creates the Quart application with a persistent secret key (see [Application secret and session encryption](#application-secret-and-session-encryption))
* Sets secure cookie defaults: `SameSite=Strict`, `Secure=True`, `HttpOnly=True`
* Registers a URL converter for filenames (`FilenameConverter`)
* Sets up the ASF OAuth endpoint at the default `/auth` URI
* Registers an error handler that redirects unauthenticated users to the OAuth login flow

ATR then overrides the OAuth URLs to use the non-OIDC ASF OAuth endpoints and applies its own configuration via `app.config.from_object()`, taking care to preserve the ASFQuart-generated secret key.

## Session management

ATR uses ASFQuart's session module (`asfquart.session`) for all cookie-based user sessions. ASFQuart sessions are cookie-based Quart sessions keyed by the application ID, which means multiple ASFQuart applications on the same host do not share sessions.

### Reading sessions

ATR calls `asfquart.session.read()` throughout the codebase to obtain the current user's session. This function:

1. Looks up the session cookie by application ID
2. Checks whether the session has expired (default: 7 days of inactivity)
3. Checks whether the session exceeds the maximum lifetime if `MAX_SESSION_AGE` is configured
4. If valid, updates the last-access timestamp and returns a `ClientSession` object
5. If expired or absent, returns `None`

The returned `ClientSession` object provides: `uid` (ASF user ID), `dn` (distinguished name), `fullname`, `email`, `committees` (PMC memberships), `projects` (committer memberships), `isMember`, `isChair`, `isRole` (role/service account flag), `mfa`, and a `metadata` dict for application-specific data.

ATR uses the `metadata` dict to track admin impersonation state (the `admin` key stores the original admin's UID when browsing as another user).

### Writing and clearing sessions

ATR calls `asfquart.session.write()` to create sessions after OAuth login and during admin impersonation. The write function stores the session data with creation (`cts`) and last-update (`uts`) timestamps. ATR calls `asfquart.session.clear()` to destroy sessions on logout, session expiry, and when ending admin impersonation.

### Session expiry

ASFQuart enforces two expiry checks within `session.read()`:

* **Inactivity expiry**: sessions unused for longer than `expiry_time` (default 7 days) are deleted
* **Maximum lifetime**: if `MAX_SESSION_AGE` is set in the application config, sessions older than this value are deleted regardless of activity

ATR additionally enforces its own absolute session maximum via `ABSOLUTE_SESSION_MAX_SECONDS` in a `before_request` hook in [`server.py`](/ref/atr/server.py), which is independent of ASFQuart's expiry.

## OAuth authentication

ATR uses ASFQuart's generic OAuth endpoint (`asfquart.generics.setup_oauth`) to handle the ASF OAuth login flow. The endpoint is registered at `/auth` and handles:

* `/auth?login` — initiates the OAuth flow by generating a cryptographic state token (`secrets.token_hex(16)`) and redirecting to the ASF OAuth service
* `/auth?login=/path` — same as above, but redirects the user to `/path` after successful login
* `/auth?logout` — clears the user's session
* `/auth?state=...&code=...` — callback from the ASF OAuth service that exchanges the authorization code for user data

ATR overrides the default OAuth URLs to use the non-OIDC endpoints:

```python
asfquart.generics.OAUTH_URL_INIT = "https://oauth.apache.org/auth?state=%s&redirect_uri=%s"
asfquart.generics.OAUTH_URL_CALLBACK = "https://oauth.apache.org/token?code=%s"
```

The OAuth flow has a configurable workflow timeout (default 15 minutes) after which pending state tokens expire. State tokens are consumed immediately on use to prevent replay. The callback enforces HTTPS on the redirect URI and always uses a `Refresh` header (instead of a 302 redirect) when returning the user after login, in case `SameSite=Strict` is set, since the `Refresh` header approach counts as a same-site navigation, preserving the cookie.

ASFQuart also sets up a login enforcement handler (`enforce_login`) that intercepts `AuthenticationFailed` exceptions and redirects unauthenticated users to the OAuth flow. This handler respects the `X-No-Redirect` header and `Authorization` headers, so API clients receive error responses rather than HTML redirects.

### Open redirect prevention

The OAuth endpoint validates redirect URIs in both login and logout flows: URIs must start with a single `/` and must not start with `//`. This prevents open redirect attacks where an attacker crafts a login URL that redirects to an external site after authentication.

## Authentication decorators

ATR uses ASFQuart's `auth.require` decorator to enforce authentication on web routes. The decorator is applied in ATR's blueprint modules ([`blueprints/get.py`](/ref/atr/blueprints/get.py) and [`blueprints/post.py`](/ref/atr/blueprints/post.py)):

```python
decorated = wrapper if public else auth.require(auth.Requirements.committer)(wrapper)
```

The `auth.require` decorator reads the current session via `asfquart.session.read()` and raises `AuthenticationFailed` (HTTP 403) if no valid session exists or if the specified requirement is not met. ATR uses only the `Requirements.committer` check, which verifies that any authenticated session exists. Further authorization (PMC membership, project participation) is handled by ATR's own [`principal`](/ref/atr/principal.py) module and storage layer.

## Exception handling

ATR uses ASFQuart's `ASFQuartException` class as a general-purpose HTTP error exception throughout its codebase. The exception carries a message and an HTTP status code. ATR raises it for authentication failures (401), authorization failures (403), validation errors, and other HTTP error conditions.

ATR registers error handlers for `ASFQuartException` in both the main application ([`server.py`](/ref/atr/server.py)) and the API blueprint ([`blueprints/api.py`](/ref/atr/blueprints/api.py)). The API handler returns JSON error responses; the main handler renders HTML error pages.

## Application secret and session encryption

ASFQuart manages the Quart `secret_key`, which is used to sign session cookies. The `QuartApp` constructor either reads the secret from a token file or generates a new one:

* If the token file exists (`secrets/generated/apptoken.txt` in ATR's case), the secret is read from it
* If the token file does not exist, a new secret is generated via `secrets.token_hex()` and written to the file with mode `0o600` (owner read/write only)
* If the file cannot be written (permission error), the secret is generated in memory only, meaning sessions will not survive server restarts

ASFQuart warns (to stderr) if the token file has permissions other than `0o600`. ATR preserves the ASFQuart-generated secret key when applying its own configuration, since `app.config.from_object()` would otherwise overwrite it.

## JWT signing key storage

ATR stores its JWT signing key in the Quart application's `extensions` dict, which is provided by the `QuartApp` instance from ASFQuart. The [`jwtoken`](/ref/atr/jwtoken.py) module calls `asfquart.APP.extensions` to read and write the signing key. This is standard Quart functionality exposed through ASFQuart's application instance.

## Application extensions

ATR uses the `QuartApp.extensions` dict (a standard Quart feature inherited from ASFQuart) to store application-wide cached data including the admin user set, project version cache, and JWT signing key. These are accessed via `asfquart.APP.extensions` throughout the codebase.

## What ATR does not use from ASFQuart

ASFQuart provides several capabilities that ATR does not use:

* **LDAP authentication** (`asfquart.ldap`): ASFQuart supports HTTP Basic authentication against ASF LDAP. ATR performs its own LDAP lookups via its [`ldap`](/ref/atr/ldap.py) module for authorization data, but does not use ASFQuart's LDAP authentication path.
* **Bearer token handler** (`app.token_handler`): ASFQuart supports registering a callback for `Authorization: Bearer` tokens. ATR implements its own JWT verification in [`jwtoken.py`](/ref/atr/jwtoken.py) rather than using this mechanism.
* **EZT template watcher** (`app.tw`): ASFQuart includes a file-watching template system based on EZT. ATR uses Jinja2 templates with its own preloading system instead.
* **YAML configuration** (`asfquart.config`): ASFQuart provides a YAML config reader. ATR uses its own [`config`](/ref/atr/config.py) module with Python class-based configuration.
* **Development server** (`app.runx()`): ASFQuart provides an extended development server with hot reloading. ATR uses Hypercorn directly.

## Security considerations for auditors

When auditing ATR's authentication and session handling, be aware that several security-critical behaviours originate in ASFQuart rather than in ATR's own code:

* **Session cookie attributes** (`SameSite`, `Secure`, `HttpOnly`) are set by `asfquart.construct()`, not by ATR.
* **Session expiry logic** (inactivity timeout, maximum lifetime) is implemented in `asfquart.session.read()`. ATR adds an additional absolute lifetime check, but the primary expiry logic is in ASFQuart.
* **OAuth state management** (generation, validation, timeout, single-use) is handled by `asfquart.generics.setup_oauth()`.
* **Open redirect prevention** on login/logout URIs is enforced by `asfquart.generics`.
* **Secret key persistence and file permissions** are managed by `asfquart.base.QuartApp.__init__()`.
* **Authentication gating** on web routes uses `asfquart.auth.require`, which reads the session and raises `AuthenticationFailed`.

The source code for ASFQuart is maintained at [github.com/apache/infrastructure-asfquart](https://github.com/apache/infrastructure-asfquart).
