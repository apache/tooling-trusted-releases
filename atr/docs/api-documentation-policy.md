# 3.16. API documentation policy

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.15.` [TLS security configuration](tls-security-configuration)

**Next**: `3.17.` [ASFQuart usage](asfquart-usage)

**Sections**:

* [Overview](#overview)
* [Decision](#decision)
* [Rationale](#rationale)
* [What the public spec contains](#what-the-public-spec-contains)
* [What the public spec does not contain](#what-the-public-spec-does-not-contain)
* [Endpoint-level authentication is unchanged](#endpoint-level-authentication-is-unchanged)
* [ASVS reference](#asvs-reference)
* [Implementation references](#implementation-references)

## Overview

ATR publishes machine-readable API documentation at two endpoints:

* `/api/openapi.json` — the OpenAPI 3 specification for the ATR HTTP API
* `/api/docs` — a Swagger UI rendering of that specification

Both endpoints are reachable without authentication. This document records the reason that is the intended behaviour, what the public surface does and does not include, and what protections are in place at the endpoints the specification describes.

## Decision

`/api/openapi.json` and `/api/docs` are **intentionally public** and do not require authentication. The corresponding inline `audit_guidance` comment in [`atr/server.py`](/ref/atr/server.py) is the source of truth for the implementation; this page is the policy record.

## Rationale

1. **ATR exposes a public API by design.** The HTTP API is consumed by the `atr` CLI client, by ASF tooling GitHub Actions, and by other release tooling outside the ASF. Publishing the specification publicly is the most direct way for those clients, and for prospective contributors, to discover what the API offers.

2. **Open-source ASF practice.** ATR is an Apache Software Foundation project. Project interfaces and their documentation are expected to be openly accessible.

3. **No admin or internal surface is exposed.** The `ApiOnlyOpenAPIProvider` class in [`atr/server.py`](/ref/atr/server.py) filters the generated specification so that only routes whose path begins with `/api` appear. Admin routes, internal blueprints, and the web UI's own endpoints are excluded from the published spec, so an unauthenticated reader does not learn about them from this channel.

4. **Documenting an endpoint does not grant access to it.** The `@quart_schema.hide` decorator on the Swagger UI route only suppresses that route from appearing in the spec; it does not relax the rate limit, the CSRF posture, or any per-endpoint authentication check. Authentication for the documented API is enforced separately, at the endpoint, as described below.

## What the public spec contains

* Paths under `/api`
* HTTP methods, parameter names and types, request body schemas, and response schemas for those paths
* The `BearerAuth` security scheme declaration, so that clients know JWT Bearer tokens are the expected credential for protected operations

## What the public spec does not contain

* Admin routes or any route outside the `/api` prefix (filtered out by `ApiOnlyOpenAPIProvider`)
* Operational details such as deployment topology, secrets, signing keys, or internal hostnames
* User data of any kind

## Endpoint-level authentication is unchanged

The endpoints described by the published specification continue to enforce their own authentication and rate limits independently of whether the spec is public:

* Protected API endpoints require a valid JWT Bearer token, issued from a Personal Access Token. See `3.12.` [Authentication security](authentication-security) for the full authentication model.
* The `/api` blueprint applies a blueprint-wide rate limit in [`atr/blueprints/api.py`](/ref/atr/blueprints/api.py), and JWT issuance has its own tighter limits.

Publishing the specification therefore tells a reader *which* endpoints exist and *what shape* requests must take; it does not allow them to call protected endpoints.

## ASVS reference

This decision addresses ASVS v5 §13.4.5 (L2). The control is satisfied because:

* The public specification is filtered by `ApiOnlyOpenAPIProvider` and does not leak admin functionality or internal implementation details.
* Each endpoint documented in the specification enforces its own authentication and rate-limit requirements.
* The decision is recorded both as an inline `audit_guidance` comment in the code and in this document.

If the public-by-default policy ever needs to change, the appropriate path is to remove the route registrations in `_app_setup_api_docs` (or place them behind an authentication check) and update this section.

## Implementation references

* [`server.py`](/ref/atr/server.py) — `_app_setup_api_docs` registers the `/api/docs` and `/api/openapi.json` routes and carries the inline `audit_guidance` comment; `ApiOnlyOpenAPIProvider` restricts the generated spec to `/api` routes
* [`blueprints/api.py`](/ref/atr/blueprints/api.py) — defines the `/api` blueprint and the blueprint-wide rate limit
* [`jwtoken.py`](/ref/atr/jwtoken.py) — JWT verification and the decorators that protect individual API endpoints
* [`templates/about.html`](/ref/atr/templates/about.html) — the public "About" page links to `/api/docs` so that prospective API users can discover the documentation
