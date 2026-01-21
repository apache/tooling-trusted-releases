# 3.11. Authentication security

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.10.` [How to contribute](how-to-contribute)

**Next**: `3.12.` [Authorization security](security-authorization)

**Sections**:

* [Overview](#overview)
* [Transport security](#transport-security)
* [Web authentication](#web-authentication)
* [API authentication](#api-authentication)
* [Token lifecycle](#token-lifecycle)
* [Security properties](#security-properties)
* [Implementation references](#implementation-references)

## Overview

ATR uses two authentication mechanisms depending on the access method:

* **Web interface**: ASF OAuth provides browser-based sessions
* **API**: Personal Access Tokens (PATs) authenticate users to obtain short-lived JSON Web Tokens (JWTs), which then authenticate API requests

Both mechanisms require HTTPS. Authentication verifies the identity of users, while authorization (covered in [Authorization security](security-authorization)) determines what actions they can perform.

## Transport security

All ATR routes, on both the website and the API, require HTTPS using TLS 1.2 or newer. This is enforced at the httpd layer in front of the application. Requests over plain HTTP are redirected to HTTPS.

Tokens and credentials must never appear in URLs, as URLs may be logged or cached. They must only be transmitted in request headers or POST bodies over HTTPS.

## Web authentication

### ASF OAuth integration

Browser users authenticate through [ASF OAuth](https://oauth.apache.org/api.html). The authentication flow works as follows:

1. User clicks "Sign in" on the ATR website
2. ATR redirects the user to the ASF OAuth service
3. User authenticates with their ASF credentials
4. ASF OAuth redirects the user back to ATR with session information
5. ATR creates a server-side session linked to the user's ASF UID

The session is managed by [ASFQuart](https://github.com/apache/infrastructure-asfquart), which handles the OAuth handshake and session cookie management.

### Session management

Sessions are stored server-side. The browser receives only a session cookie that references the server-side session data. Session cookies are configured with security attributes:

* `HttpOnly` - prevents JavaScript access to the cookie
* `Secure` - cookie is only sent over HTTPS
* `SameSite=Strict` - provides CSRF protection for most requests

Session data includes the user's ASF UID and is used to authorize requests. The session expires after a period of inactivity or when the user logs out.

### Session caching

Authorization data fetched from LDAP (committee memberships, project participation) is cached in [`principal.Cache`](/ref/atr/principal.py:Cache) for performance. The cache has a TTL of 300 seconds, defined by `cache_for_at_most_seconds`. After the TTL expires, the next request will refresh the cache from LDAP.

## API authentication

API access uses a two-token system: Personal Access Tokens (PATs) for long-term credentials and JSON Web Tokens (JWTs) for short-term API access.

### Personal Access Tokens (PATs)

Committers can obtain PATs from the `/tokens` page on the ATR website. PATs have the following properties:

* **Validity**: 180 days from creation
* **Storage**: ATR stores only SHA3-256 hashes, never the plaintext PAT
* **Revocation**: Users can revoke their own PATs at any time; admins can revoke any PAT
* **Purpose**: PATs are used solely to obtain JWTs; they cannot be used directly for API access

Only authenticated committers (signed in via ASF OAuth) can create PATs. Each user can have multiple active PATs.

### JSON Web Tokens (JWTs)

To access protected API endpoints, users must first obtain a JWT by exchanging their PAT. This is done by POSTing to `/api/jwt`:

```text
POST /api/jwt
Content-Type: application/json

{"asfuid": "username", "pat": "pat_token_value"}
```

On success, the response contains a JWT:

```json
{"asfuid": "username", "jwt": "jwt_token_value"}
```

JWTs have the following properties:

* **Algorithm**: HS256 (HMAC-SHA256)
* **Validity**: 90 minutes from creation
* **Claims**: `sub` (ASF UID), `iat` (issued at), `exp` (expiration), `jti` (unique token ID)
* **Storage**: JWTs are stateless; ATR does not store issued JWTs

The JWT is used in the `Authorization` header as a bearer token:

```text
Authorization: Bearer jwt_token_value
```

### Token handling

The [`jwtoken`](/ref/atr/jwtoken.py) module handles JWT creation and verification. Protected API endpoints use the `@jwtoken.require` decorator, which extracts the JWT from the `Authorization` header, verifies its signature and expiration, and makes the user's ASF UID available to the handler.

## Token lifecycle

The relationship between authentication methods and tokens:

```text
ASF OAuth (web login)
    │
    ├──▶ Web Session ──▶ Web Interface Access
    │
    └──▶ PAT Creation ──▶ PAT (180 days)
                              │
                              └──▶ JWT Exchange ──▶ JWT (90 min)
                                                       │
                                                       └──▶ API Access
```

For web users, authentication happens once via ASF OAuth, and the session persists until logout or expiration. For API users, the flow is: obtain a PAT once (via the web interface), then exchange it for JWTs as needed (JWTs expire quickly, so this exchange happens frequently in long-running scripts).

## Security properties

### Web sessions

* Server-side storage prevents client-side tampering
* Session cookies are protected against XSS (`HttpOnly`) and transmission interception (`Secure`)
* `SameSite` attribute provides baseline CSRF protection (ATR also uses CSRF tokens in forms)

### Personal Access Tokens

* Stored as SHA3-256 hashes
* Can be revoked immediately by the user
* Limited purpose (only for JWT issuance) reduces impact of compromise
* Long validity (180 days) balanced by easy revocation

### JSON Web Tokens

* Short validity (90 minutes) limits exposure window
* Signed with a server secret initialized at startup
* Stateless design means no database lookup required for verification

### Credential protection

Tokens must be protected by the user at all times:

* Never include tokens in URLs
* Never log tokens
* Never commit tokens to source control
* Report compromised tokens to ASF security immediately

## Implementation references

* [`principal.py`](/ref/atr/principal.py) - Session caching and authorization data
* [`jwtoken.py`](/ref/atr/jwtoken.py) - JWT creation, verification, and decorators
