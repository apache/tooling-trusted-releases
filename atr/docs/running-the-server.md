# 3.1. Running the server

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `2.2.` [Signing artifacts](signing-artifacts)

**Next**: `3.2.` [Overview of the code](overview-of-the-code)

**Sections**:

* [Quick start](#quick-start)
* [Server architecture](#server-architecture)
* [Configuration details](#configuration-details)
* [Authentication and sessions](#authentication-and-sessions)

## Quick start

For step-by-step setup instructions, see **[DEVELOPMENT.md](https://github.com/apache/tooling-trusted-releases/blob/main/DEVELOPMENT.md)** in the repository root.

That guide covers:

- Prerequisites and platform-specific installation
- Running with Docker Compose (recommended)
- Running directly with uv and mkcert
- Development workflow and troubleshooting

The rest of this page provides deeper technical context for how the server works.

## Server architecture

ATR is a Python application based on [ASFQuart](https://github.com/apache/infrastructure-asfquart), which is based on [Quart](https://github.com/pallets/quart). Quart is an asynchronous version of [Flask](https://github.com/pallets/flask). In addition to Python, we use small amounts of JavaScript and TypeScript for the front end.

**Running in containers:** On ASF infrastructure, ATR runs in containers managed by Puppet. For development, we use Docker Compose with an Alpine Linux base image that includes external tools (CycloneDX, syft, Apache RAT) required for SBOM generation and license checking.

**Running directly:** For faster iteration, you can run ATR directly using uv and Hypercorn. This requires manually installing dependencies and generating TLS certificates with mkcert.

**Trade-offs:**

- _Container_: isolated, includes all tools; slower startup, certificate trust setup
- _Direct_: fast iteration, auto-trusted certs; manual dependency setup

**Important:** Do not run both methods simultaneously - they share the same state directory and will conflict.

## Configuration details

### TLS requirements

ATR requires TLS even for development because login is performed through the actual ASF OAuth server. This ensures development behavior aligns closely with production.

The `make certs-local` target generates certificates using mkcert:

```shell
mkcert localhost.apache.org 127.0.0.1 ::1
```

We exclude `localhost` to avoid [DNS resolution issues noted in RFC 8252](https://datatracker.ietf.org/doc/html/rfc8252#section-8.3).

### Host configuration

ATR serves on multiple hosts, but we recommend using `localhost.apache.org` consistently. This requires an `/etc/hosts` entry:

```
127.0.0.1 localhost.apache.org
```

**Why this matters:** Logging into the site on one host does not log you in on another host. Pick one and use it consistently.

### Environment variables

- `ALLOW_TESTS=1`: Enable test mode with mock authentication
- `APP_HOST`: Hostname for the application 
- `BIND`: Address and port to bind (default: `127.0.0.1:8080`)
- `LDAP_BIND_DN`: LDAP bind DN for rsync writes
- `LDAP_BIND_PASSWORD`: LDAP bind password
- `SSH_HOST`: SSH host for rsync operations

### Startup behavior

On first startup, the server fetches committee and project information from the ASF website. This takes 1-2 minutes, during which no existing committees or projects will appear.

## Authentication and sessions

### ASF OAuth

ATR uses ASF OAuth for user authentication. Even in development, you authenticate against the real ASF OAuth server. This is why TLS is required.

### Session caching for developers

Developers without LDAP credentials will be unable to perform rsync writes, and certain tasks may fail. To work around this in development:

1. Visit `/user/cache`
2. Press the "Cache me!" button

This writes your session information to the ATR state directory (`state/`), which is consulted instead of LDAP when present.

To clear cached session data:

1. Use the clear button on `/user/cache`
2. Restart the server (the `atr/principal.py` module caches authorization in memory)

**Note:** Session caching only works in debug mode, which is enabled when using `make serve-local`.
