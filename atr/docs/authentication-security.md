# 3.12. Authentication security

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.11.` [How to contribute](how-to-contribute)

**Next**: `3.13.` [Authorization security](authorization-security)

**Sections**:

* [Overview](#overview)
* [OAuth architecture and security responsibilities](#oauth-architecture-and-security-responsibilities)
* [Transport security](#transport-security)
* [Web authentication](#web-authentication)
* [API authentication](#api-authentication)
* [GitHub Actions OIDC (Trusted Publishing)](#github-actions-oidc-trusted-publishing)
* [SSH authentication](#ssh-authentication)
* [Token lifecycle](#token-lifecycle)
* [Adaptive response](#adaptive-response)
* [Audit Logging](#audit-logging)
* [Security properties](#security-properties)
* [Implementation references](#implementation-references)

## Overview

ATR uses several authentication mechanisms depending on the access method:

* **Web interface**: ASF OAuth provides browser-based sessions
* **API**: Personal Access Tokens (PATs) authenticate users to obtain short-lived JSON Web Tokens (JWTs), which then authenticate API requests
* **SSH**: Temporary SSH keys authenticate automated uploads from GitHub Actions workflows

All mechanisms require encrypted transport (HTTPS or SSH). Authentication verifies the identity of users, while authorization (covered in [Authorization security](authorization-security)) determines what actions they can perform.

## OAuth architecture and security responsibilities

ATR participates in several authentication protocols but does not implement an OAuth Authorization Server. Understanding which roles ATR fills is important for knowing which security requirements apply to ATR versus external services.

### ATR's roles

**OAuth Client.** ATR delegates user authentication to the ASF OAuth service at `oauth.apache.org` via the [ASFQuart](https://github.com/apache/infrastructure-asfquart) framework. ATR redirects users to the ASF authorization endpoint, receives an authorization code in the callback, and immediately exchanges that code for session data. ATR does not store authorization codes, issue OAuth tokens, or manage OAuth client registrations.

**OIDC Relying Party.** For [trusted publishing](trusted-publishing) workflows, ATR validates OIDC ID tokens issued by GitHub Actions (`token.actions.githubusercontent.com`). ATR verifies the token signature using the provider's JWKS endpoint, checks the issuer, audience, expiration, and expected claims, and enforces actor identity requirements that differ between project TP and ATR distribution workflows. ATR does not issue OIDC tokens. See [GitHub Actions OIDC](#github-actions-oidc-trusted-publishing) below for full detail.

**Resource Server.** ATR issues its own short-lived JWTs (30-minute TTL, HS256) for API access. These are a custom API authentication mechanism, not OAuth access tokens or refresh tokens. See [API authentication](#api-authentication) below.

### What ATR does not implement

ATR does not implement any OAuth Authorization Server functionality: there is no authorization endpoint, no token endpoint with OAuth grant type handling, no authorization code generation or lifetime management, no client registration, no refresh token issuance or rotation, and no support for the Implicit or Resource Owner Password Credentials flows.

### ASVS applicability

The OWASP ASVS V10.4 requirements target OAuth Authorization Servers. Because ATR is not an Authorization Server, V10.4.1 through V10.4.5 (redirect URI validation, authorization code single-use and lifetime, grant type restrictions, refresh token replay mitigation) are the responsibility of `oauth.apache.org`, not ATR.

The ASVS sections applicable to ATR are V10.2 (OAuth Client) and V10.3 (OAuth Resource Server). The OAuth client security controls that ATR implements are described in the sections below.

### OAuth client security controls

* **State parameter**: Generated with `secrets.token_hex(16)` and enforced as single-use (removed immediately on callback). Stale states expire after 900 seconds.
* **Authorization code exchange**: Codes received from `oauth.apache.org` are exchanged immediately over HTTPS and are never stored locally.
* **Session cookies**: Configured with `Secure`, `HttpOnly`, `SameSite=Strict`, and the `__Host-` prefix.
* **Session lifetime**: Enforced with a configurable absolute maximum (default 72 hours).
* **TLS enforcement**: All outbound requests to OAuth and OIDC endpoints use a hardened TLS context via [`util.create_secure_ssl_context()`](/ref/atr/util.py).

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

Session data includes the user's ASF UID and is used to authorize requests. The session expires after a configured maximum lifetime (default 72 hours), a period of inactivity, or when the user logs out.

### Session caching

Authorization data fetched from LDAP (committee memberships, project participation) is cached in [`principal.Cache`](/ref/atr/principal.py) (Cache) for performance. The cache has a TTL of 300 seconds, defined by `cache_for_at_most_seconds`. After the TTL expires, the next request will refresh the cache from LDAP.

## API authentication

API access uses a two-token system: Personal Access Tokens (PATs) for long-term credentials and JSON Web Tokens (JWTs) for short-term API access.

### Personal Access Tokens (PATs)

Committers can obtain PATs from the `/tokens` page on the ATR website. PATs have the following properties:

* **Validity**: 180 days from creation, while LDAP account is still active
* **Format**: New PATs are Noisy Secrets using the `tooling.apache.org` namespace
* **Storage**: ATR stores only SHA3-256 hashes, never the plaintext PAT
* **Revocation**: Users can revoke their own PATs at any time; admins can revoke all PATs for any user via the admin "Revoke user tokens" page
* **IP restriction**: A PAT may optionally be bound to a single IP or CIDR. When set, JWT issuance is refused from any other address
* **Purpose**: PATs are used solely to obtain JWTs; they cannot be used directly for API access

Only authenticated committers (signed in via ASF OAuth) can create PATs. Each user can have multiple active PATs.

PATs are rejected if the user who created them has been banned in or removed from LDAP.

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
* **Validity**: 30 minutes from creation
* **Claims**: `sub` (ASF UID), `iss` (issuer), `aud` (audience), `iat` (issued at), `nbf` (not before), `exp` (expiration), `jti` (unique token ID)
* **Storage**: JWTs are stateless; ATR does not store issued JWTs

The JWT is used in the `Authorization` header as a bearer token:

```text
Authorization: Bearer jwt_token_value
```

### Token handling

The [`jwtoken`](/ref/atr/jwtoken.py) module handles JWT creation and verification. Protected API endpoints use the `@jwtoken.require` decorator, which extracts the JWT from the `Authorization` header, verifies its signature and required claims, and makes the user's ASF UID available to the handler. Verification applies a deliberate two minute leeway to the time based JWT checks to tolerate small clock skew between ATR and API clients.

### System tokens

Most PATs belong to a committer and are tied to their LDAP account. ATR also supports *system tokens*: PATs minted for a trusted service caller rather than a person, flagged with `is_system` in the database. A system PAT has no owning user, so its `asfuid` is null; the administrator who minted it is recorded in `created_by`, which is always set on every PAT. The JWT a system PAT mints carries a fixed service identity, `system`, which has no LDAP account. System tokens are restricted to foundation administrators; committers cannot create them from the `/tokens` page.

A system token is exchanged for a JWT at `/api/jwt`, requesting the `system` identity. The resulting JWT carries an additional `atr_sys` claim and `system` as its subject. Because that identity has no LDAP account, verification skips the LDAP active check for these JWTs. It still re-validates the backing PAT on every request, so revoking the PAT immediately invalidates every JWT issued from it. Two further checks apply: a system JWT must be backed by a system PAT, and its subject must be `system`. The creating administrator and the PAT name are recorded in the audit log when the JWT is issued.

Endpoints that should accept only system tokens are decorated with `@api.auth.system_bearer`, which rejects ordinary user JWTs. The committee membership check that gates committer-driven writes does not apply, so any committee authorisation for these endpoints must be established by the calling service before it reaches ATR. The only such endpoint at present is `/project/config`, which the `.asf.yaml` processor uses to create and update projects.

## GitHub Actions OIDC (Trusted Publishing)

GitHub Actions workflows authenticate to ATR using OIDC tokens issued by GitHub (`token.actions.githubusercontent.com`). ATR uses this mechanism in two distinct contexts with different authorization requirements, described below.

### Token validation

All OIDC-authenticated endpoints share the same cryptographic validation, implemented in `jwtoken.verify_github_oidc`. The caller then applies context-specific checks on top.

**Header safety.** Before any other processing, ATR inspects the unverified JWT header and rejects any token that contains `jku`, `x5u`, or `jwk` fields. These fields could redirect key lookup to an attacker-controlled endpoint.

**JWKS retrieval.** ATR fetches the JWKS URI from GitHub's OIDC discovery document (`token.actions.githubusercontent.com/.well-known/openid-configuration`) over a hardened TLS session (`util.create_secure_session`). The resulting URI is then validated: its hostname must be `token.actions.githubusercontent.com` and its scheme must be `https`. This prevents a compromised or redirected discovery response from substituting a different key set.

**Signature and standard claims.** The token is verified using RS256 with the key obtained from JWKS. The `iss` must be `https://token.actions.githubusercontent.com`, the `aud` must be `atr-test-v1`, and both `exp` and `iat` must be present.

**Enterprise and environment claims.** Four additional claims are checked against hardcoded expected values: `enterprise` must be `the-asf`, `enterprise_id` must match the ASF GitHub enterprise, `repository_owner` must be `apache`, and `runner_environment` must be `github-hosted`. Together these constrain the token to workflows running inside the ASF GitHub enterprise, on official GitHub-hosted runners, under the `apache` organisation.

### Project Trusted Publishing

Project TP allows an `apache/*` project repository to upload release artifacts and perform actions on its own ATR project directly from a GitHub Actions workflow. The workflow is defined in [tooling-actions](https://github.com/apache/tooling-actions) and called from the project's own repository.

**Actor requirement.** After cryptographic validation, `interaction.trusted_jwt` checks that `actor_id` resolves to a human committer: it performs an LDAP lookup to map the numeric GitHub user ID to an ASF UID. If `actor_id` belongs to the ASF Infrastructure service account instead, the request is rejected. The committer must have their GitHub account registered in LDAP.

**Repository and workflow binding.** `interaction._trusted_project` further validates the `repository` and `workflow_ref` claims from the token against release policies stored in ATR. Only pre-registered workflow paths from pre-registered repositories are accepted, so an arbitrary workflow in an `apache/*` repo cannot invoke these endpoints.

### ATR distribution workflows

ATR uses a separate set of endpoints to orchestrate distribution to third-party platforms such as Maven Central. These workflows live in [tooling-actions](https://github.com/apache/tooling-actions) and are triggered by ATR itself, not by project committers.

**Trust chain.** An authenticated committer initiates a release distribution through ATR. ATR dispatches the workflow via the ASF Infrastructure service account, passing the required context (including the SSH public key) into the workflow. The workflow runs on GitHub Actions and calls back to ATR with a GitHub OIDC token.

**Actor requirement.** `interaction.trusted_jwt_for_dist` checks that `actor_id` matches `_GITHUB_TRUSTED_ROLE_NID`, the hardcoded numeric ID of the ASF Infrastructure service account. If `actor_id` belongs to a human committer instead, the request is rejected. The check uses the numeric ID, which is stable across account renames.

The OIDC token proves that the callback came from a workflow ATR itself started. The original authorization is inherited from the committer who initiated the distribution in ATR — the service account carries no privileges of its own beyond triggering the workflow.

The workflow also carries the originating committer's ASF UID as a parameter. ATR trusts this value because the service account gate already establishes that only ATR could have dispatched the workflow, and ATR embedded the UID when it did so.

### SSH key scope

In both cases, the workflow calls ATR's SSH registration endpoint (`/publisher/ssh/register` for project TP, `/distribute/ssh/register` for distribution workflows), which generates a temporary SSH key bound to the specific project. The key cannot be used for any other project. The identity attached to the key differs: for project TP it is the committer resolved via LDAP from the OIDC `actor_id`; for distribution workflows it is the committer's ASF UID passed through the workflow, trusted because only the service account — which only ATR can trigger — could have put it there.

## SSH authentication

ATR provides an SSH server for rsync file upload, and for automated release artifact uploads from GitHub Actions workflows. This is a distinct authentication pathway from the web and API mechanisms described above.

### Authentication mechanism

The SSH server supports only public key authentication — password authentication is disabled and rejected. The authentication flow accepts SSH keys for rsync connections from the user, or, depending on the route, is tightly integrated with the GitHub Actions OIDC workflows described in the previous section:

1. A GitHub Actions workflow authenticates to ATR via OIDC (see [GitHub Actions OIDC](#github-actions-oidc-trusted-publishing) above) and calls the SSH key registration endpoint.
2. ATR generates a temporary SSH key pair, stores the public key in the database with a 20-minute TTL, and returns the private key to the workflow.
3. The workflow uses the private key to connect to ATR's SSH server and upload release artifacts via rsync.
4. The SSH server validates the presented public key against the database, checking existence, expiration, and project binding.

Each such SSH key is bound to a specific project context and cannot be used for any other project. See [SSH key scope](#ssh-key-scope) above for how the key binding differs between project Trusted Publishing and ATR distribution workflows.

### SSH server configuration

The SSH server in [`ssh.py`](/ref/atr/ssh.py) applies a hardened security configuration. Cipher suites, key exchange algorithms, and MAC algorithms are restricted to those approved by the Hardened OpenSSH Server v9.9 policy from [ssh-audit](https://www.ssh-audit.com/). Only algorithms supported by both the policy and the asyncssh library are enabled.

## Token lifecycle

The relationship between authentication methods and tokens:

```text
ASF OAuth (web login)
    │
    ├──▶ Web Session ──▶ Web Interface Access
    │
    └──▶ PAT Creation ──▶ PAT (180 days)
                              │
                              └──▶ JWT Exchange ──▶ JWT (30 min)
                                                       │
                                                       ├──▶ API Access
                                                       │
                                                       └──▶ GitHub OIDC Workflow
                                                                 │
                                                                 └──▶ SSH Key (20 min)
                                                                          │
                                                                          └──▶ rsync Upload
```

For web users, authentication happens once via ASF OAuth, and the session persists until logout or expiration. For API users, the flow is: obtain a PAT once (via the web interface), then exchange it for JWTs as needed (JWTs expire quickly, so this exchange happens frequently in long-running scripts). For automated uploads, the flow extends further: a GitHub Actions workflow authenticates via OIDC, registers a temporary SSH key, and uses it to upload artifacts via rsync.

## Adaptive response

ATR does not currently implement any adaptive responses such as progressive delays or challenge page in its own authentication code. Its response to repeated failures is fixed rather than adaptive. Ordinary web requests are limited to `100` requests per minute and `1000` per hour. The limiter groups traffic by authenticated ASF UID when a session exists, and by client address otherwise. API requests have their own ceiling of `500` requests per hour. The JWT issue endpoints on the website and the API are tighter at `10` requests per hour. When an HTTP limit is exceeded, ATR returns `429 Too Many Requests`. For API responses it also includes `retry_after` when the limiter provides one.

SSH follows the same general policy. ATR allows at most `100` SSH connections per minute from one client address. It also allows at most `10` successful SSH authentications per minute for one ASF UID. If either limit is exceeded then ATR closes the connection.

Browser sign in is delegated to `oauth.apache.org`, so ATR does not process passwords and does not define how the ASF OAuth service handles repeated password failures. The credentials that ATR does verify directly are long random PATs, ATR signed JWTs, and public keys. For those mechanisms, ATR relies on fixed throttling, short token lifetimes, PAT and LDAP revalidation, and prompt revocation rather than challenge based escalation.

There is one automatic containment step that goes beyond a simple reject: when LDAP marks an account as inactive, ATR revokes that user's sessions, PATs, and SSH keys.

ATR also does not ship with built in alert thresholds or automatic blocking rules for repeated failures. Instead it writes authentication events to `<STATE_DIR>/audit/auth-audit.log` and request summaries to `<STATE_DIR>/logs/requests.log`.

## Audit Logging

An audit log, at `<STATE_DIR>/audit/auth-audit.log`, contains a log of all auth-adjacent operations. This should include authentication success/failures, token issuance/revocation,
and anything else that would be relevant to authentication when investigating a potential security issue.

This log is logged to by calling the specialised methods on atr.log (all args passed below as kwargs for readability):

```python
import atr.log as log

log.auth_success(type="ssh", asfuid="arm")
log.auth_failure(type="oauth", reason="account_not_found", asfuid="sbp")
log.auth_event(event="pat_bulk_revoke", asfuid="akm", by="wave") # In this case, everything after asfuid is free-kwargs for additional log context

```

## Security properties

### Web sessions

* Server-side storage prevents client-side tampering
* Session cookies are protected against XSS (`HttpOnly`) and transmission interception (`Secure`)
* `SameSite` attribute provides baseline CSRF protection (ATR also uses CSRF tokens in forms)

### Personal Access Tokens

* Stored as SHA3-256 hashes
* Can be revoked immediately by the user or in bulk by administrators
* Limited purpose (only for JWT issuance) reduces impact of compromise
* Long validity (180 days) balanced by easy revocation

### JSON Web Tokens

* Short validity (30 minutes) limits exposure window
* Signed with a server secret initialized at startup
* Stateless design means no database lookup required for verification

### SSH keys

* Persistent SSH keys are deleted when an admin revokes keys for a user
* Workflow SSH keys (20-minute TTL) can be immediately revoked via a `revoked` flag
* LDAP account status is checked during SSH authentication, rejecting banned or deleted accounts
* Administrators can revoke all SSH keys for a user via the admin interface

### Credential protection

Tokens must be protected by the user at all times:

* Never include tokens in URLs
* Never log tokens
* Never commit tokens to source control
* Report compromised tokens to ASF security immediately

## Implementation references

* [`principal.py`](/ref/atr/principal.py) - Session caching and authorization data
* [`server.py`](/ref/atr/server.py) - Default web rate limits, 429 handling, and request logging
* [`blueprints/api.py`](/ref/atr/blueprints/api.py) - API wide rate limiting
* [`api/__init__.py`](/ref/atr/api/__init__.py) - Tighter API limits for JWT creation
* [`jwtoken.py`](/ref/atr/jwtoken.py) - JWT creation, verification, and decorators; `verify_github_oidc` implements the OIDC validation chain
* [`blueprints/api_auth.py`](/ref/atr/blueprints/api_auth.py) - per-endpoint auth level decorators, including `system_bearer`
* [`db/interaction.py`](/ref/atr/db/interaction.py) - `validate_trusted_jwt` implements the service account authorisation, `trusted_jwt_for_dist` implements gating based on the service account
* [`ldap.py`](/ref/atr/ldap.py) - Account deactivation handling and credential revocation
* [`storage/writers/tokens.py`](/ref/atr/storage/writers/tokens.py) - Token creation, deletion, and admin revocation
* [`post/tokens.py`](/ref/atr/post/tokens.py) - Tighter web limits for JWT issuance and token management
* [`ssh.py`](/ref/atr/ssh.py) - SSH server implementation, authentication, and rsync handling
* [`storage/writers/ssh.py`](/ref/atr/storage/writers/ssh.py) - SSH key management and admin revocation
