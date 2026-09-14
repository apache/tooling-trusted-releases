# 3.13. Sessions

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.12.` [Authentication security](authentication-security)

**Next**: `3.14.` [Authorization security](authorization-security)

**Sections**:

* [Overview](#overview)
* [What a session stores](#what-a-session-stores)
* [The session cookie](#the-session-cookie)
* [Session lifecycle](#session-lifecycle)
* [Timeouts and expiry](#timeouts-and-expiry)
* [Concurrent sessions and revocation](#concurrent-sessions-and-revocation)
* [OAuth scope and least privilege](#oauth-scope-and-least-privilege)
* [Flashing form errors](#flashing-form-errors)
* [Implementation references](#implementation-references)

## Overview

ATR authenticates committers through ASF OAuth (see
[Authentication security](authentication-security)). A successful login creates a
*session*: a server-side record that identifies the user on later requests, so
they do not re-authenticate on every page. This page describes what a session
holds, how long it lasts, and how it is revoked.

Sessions are managed in [`sessions.py`](/ref/atr/sessions.py). The session
identifier itself travels in a cookie; the database stores only a hash of it,
alongside the user's identity and roles as reported by OAuth at login.

## What a session stores

Each session is a [`UserSession`](/ref/atr/models/sql.py) row. Its primary key,
`sid_hash`, is a hash of the session identifier - the raw identifier is held only
in the client's cookie, so the database never contains a value that could be
replayed as a session id.

The remaining columns record the user's identity and authorisation context:

* `uid`, `dn`, `fullname`, `email` - identity (email defaults to
  `<uid>@apache.org` when OAuth supplies none).
* `is_member`, `is_chair`, `is_root`, `is_role`, `mfa` - role and account flags.
* `member_committees` and `participant_committees` - the committees where the
  user is a PMC member or a committer.
* `ip_address` - the client address captured when the session was created.
* `admin_uid` and `downgrade_admin_to_user` - support administrator impersonation
  and voluntary downgrade.
* `last_account_check`, `cts`, `uts` - the time of the last account re-check, and
  the creation and last-update times.

Before a session is written, `_prepare_session_data` maps the raw OAuth payload
onto these fields - for example the OAuth `pmcs` key becomes `member_committees`
and `projects` becomes `participant_committees` - and discards everything that is
not a modelled field, including the OAuth `metadata` block. Only the fields above
are persisted.

## The session cookie

The session cookie is configured in [`config.py`](/ref/atr/config.py) with
hardened defaults:

* Name `__Host-session` - the `__Host-` prefix requires the cookie to be secure,
  host-scoped, and path `/`, so it cannot be scoped to a parent domain or set
  over plain HTTP.
* `Secure` - sent only over HTTPS.
* `HttpOnly` - not readable from JavaScript.
* `SameSite=Strict` - not sent on cross-site requests.

Only the identifier travels in the cookie; all other session state is server-side.

## Session lifecycle

* **Creation.** On a successful OAuth login, `Store.create` prepares the OAuth
  data, records the client IP and the current time, and inserts the
  `UserSession` row.
* **Validation.** On each request, `Store.validate` looks the session up by
  `sid_hash`. An expired session is deleted and treated as absent; otherwise its
  `uts` (last-update time) is refreshed, so activity slides the idle window
  forward.
* **Caching.** Within a single request the resolved session is cached on
  `quart.g`, so repeated reads do not re-query the database; `invalidate_cache`
  clears it when the session changes.

## Timeouts and expiry

A session can expire in two independent ways:

* **Idle timeout.** A session unused for more than seven days
  (`_SESSION_IDLE_TIMEOUT`) is treated as expired. Because `validate` refreshes
  `uts` on every request, this is a sliding window measured from the last
  activity.
* **Absolute maximum age.** When `MAX_SESSION_AGE` is greater than zero, a session
  older than that from its creation time (`cts`) expires regardless of activity.
  It defaults to 72 hours, and can be changed, or disabled entirely by setting it
  to zero.

With the default configuration the 72-hour absolute cap is reached before the
seven-day idle limit, so in practice a session lasts at most 72 hours. When
`validate` finds an expired session, it deletes the row.

## Concurrent sessions and revocation

ATR does not limit the number of concurrent sessions per account. `Store.create`
inserts a new row on every login, so a user signed in from several devices may
hold many live sessions at once; there is no per-account ceiling.

Unlimited concurrent sessions are safe here because revocation is always
account-wide:

* `revoke_by_uid` deletes every session belonging to a uid in one operation,
  matching both the session's `uid` and any `admin_uid`, so an administrator's
  impersonation sessions are revoked as well.
* Credential revocation calls `terminate_current_users_sessions`, which revokes
  all of the user's sessions, so a compromised credential cannot leave a stray
  session behind.
* If an account is found to be disabled or banned, `deleted_or_banned` revokes
  all of its sessions and clears the current one.

Account status is re-checked during a session's life. `account_check` asks LDAP,
through `is_active`, whether the account - and, for an impersonation session, the
administrator's account - is still active, records the time in
`last_account_check`, and tears the session down if the account is disabled or
banned.

Two situations relax this. If a live LDAP error occurs during the check,
`account_check` logs a warning and leaves the session in place rather than failing
the request. And when ATR runs without LDAP bind credentials outside production -
as in local development - `is_active` treats every account as active, so the check
never tears a session down.

## OAuth scope and least privilege

The OAuth authorization request that begins a login is constructed by ASFQuart
against `oauth.apache.org`; ATR does not build that request and does not set the
OAuth `scope` parameter itself. Which claims the authorization server returns is
therefore governed upstream - by ASFQuart and by ATR's client registration at
`oauth.apache.org`.

ATR applies least privilege to what it *retains*. As described under
[What a session stores](#what-a-session-stores), `_prepare_session_data` keeps
only the fields modelled on `UserSession` and drops the rest of the OAuth payload
before anything is written. So even if the authorization server returns more than
ATR needs, only those fields are persisted in a session.

## Flashing form errors

When a form submission fails validation, ATR re-renders the form after the browser
has been redirected back to it - a POST, then a redirect, then a GET - with the
user's own input restored and the errors shown against the relevant fields. The
data that survives that redirect is held in a session-scoped table rather than in
the URL or the cookie.

[`SessionFormError`](/ref/atr/models/sql.py) stores one payload per session and
request path. The payload, built by `flash_error_data` in
[`form.py`](/ref/atr/form.py), carries both the messages and the submitted input:
each errored field keeps its label, its message, and the value that was entered,
and every other submitted field keeps its value too, so nothing the user typed is
lost on the way back.

`form_error_put` writes that payload when a submission fails - through
`flash_form_error` in [`blueprints/common.py`](/ref/atr/blueprints/common.py) and
`form_error` in [`web.py`](/ref/atr/web.py) - and `form_error_pop` reads it back
and deletes it when the form is next rendered, so it is shown once. The `sid_hash`
foreign key cascades on delete, so a session's pending payload disappears when the
session does.

Alongside this, a short summary of the errors is shown immediately through Quart's
own `flash`, which ATR also uses elsewhere for one-off success and error messages.

## Implementation references

* [`sessions.py`](/ref/atr/sessions.py) - session store, lifecycle, and revocation
* [`models/sql.py`](/ref/atr/models/sql.py) - the `UserSession` and `SessionFormError` tables
* [`config.py`](/ref/atr/config.py) - `MAX_SESSION_AGE` and the session cookie settings
* [`generics.py.patch`](/ref/patches/generics.py.patch) - ATR's patch to the ASFQuart login flow
