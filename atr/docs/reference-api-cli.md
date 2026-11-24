# 7.18. API and CLI reference

**Up**: `7.` [Developer guide](developer-guide)

**Prev**: `7.17.` [TLS security configuration](tls-security-configuration)

**Next**: `7.19.` [API documentation policy](api-documentation-policy)

**Sections**:

* [Overview](#overview)
* [Spec changes](#spec-changes)
* [Authentication](#authentication)
* [Release workflow](#release-workflow)
* [Limits and errors](#limits-and-errors)
* [API endpoints](#api-endpoints)
* [CLI commands](#cli-commands)

## Overview

ATR is the Apache Software Foundation’s service for verifying and distributing releases. Its HTTP API is used by the `atr` command line client, ASF GitHub Actions and other release tooling. The project is in beta development and says it plans to avoid significant changes, but it gives no guarantees about stability or backward compatibility.

Base URL: `https://releases.apache.org`. All API calls require HTTPS (TLS 1.2+). Plain HTTP to the API is rejected with `421`. Every API path starts with `/api`.

The live spec is at [/api/openapi.json](/api/openapi.json), and the automatically generated reference is the [Swagger UI](/api/docs). Links to individual API methods on this page open that Swagger UI. This page reflects spec version `v0.1.0.dev3675-31cdc7e3+dirty` and the [client](https://github.com/apache/tooling-releases-client) at commit `a07f9fe` (10 Nov 2026).

## Spec changes

Changes between spec `v0.1.0.dev3631` and `v0.1.0.dev3675-31cdc7e3+dirty`.

### Removed

* `POST /api/ssh-key/add`. [`ssh-key/delete`](/api/docs#/default/post_ssh_key_delete) remains.
* The `manual` vote mode. `VoteMode` is now `email` or `trusted`. `policy_manual_vote` is gone from the policy response and `manual_vote` is gone from the policy arguments.

### Added

* [`POST /api/release/commit`](/api/docs#/default/post_release_commit) records the commit that a draft’s source archives were built from, for the whole release.
* `source_commit` is accepted by [`POST /api/release/upload`](/api/docs#/default/post_release_upload) (JSON body) and [`POST /api/release/store`](/api/docs#/default/post_release_store) (query parameter).
* `auto_archive_prior_release` (boolean, optional) in the policy arguments.
* `gate_decision` in `TaskType`.

### Changed

* [`POST /api/release/source`](/api/docs#/default/post_release_source) declares sources archive by archive. Projects with several GitHub repositories must use it.
* The test-mode [`POST /api/test/archive-project`](/api/docs#/default/post_test_archive_project) now sets `DORMANT` rather than `RETIRED`.
* [`POST /api/vote/resolve`](/api/docs#/default/post_vote_resolve) is refused with 403 unless the release uses streamlined voting, the `trusted` vote mode (the `solr-mcp` project is exempt). This comes from the source code. The spec’s description does not mention it.

## Authentication

You create a long-lived Personal Access Token (PAT) once in the web UI, then exchange it for a short-lived JWT whenever you call the API.

1. **Create a PAT.** Sign in at `/tokens`. A PAT is valid for 180 days and is shown once.
2. **Exchange it.** [`POST /api/jwt/create`](/api/docs#/default/post_jwt_create) returns a JWT.
3. **Call the API.** Send `Authorization: Bearer <jwt>`. A JWT is valid for 30 minutes.

PATs can’t call the API directly. A PAT can optionally be bound to one IP address or CIDR range, and JWT issuance is refused from any other address. Never put tokens in URLs.

### Get a JWT with curl

```sh
curl -sS https://releases.apache.org/api/jwt/create \
  -H 'Content-Type: application/json' \
  -d '{"asfuid": "alice", "pat": "YOUR_PAT"}'
# {"asfuid": "alice", "jwt": "eyJ..."}
```

### Get a JWT with Python

```python
import requests

BASE = "https://releases.apache.org"
r = requests.post(f"{BASE}/api/jwt/create",
                  json={"asfuid": "alice", "pat": "YOUR_PAT"})
r.raise_for_status()
jwt = r.json()["jwt"]

headers = {"Authorization": f"Bearer {jwt}"}
print(requests.get(f"{BASE}/api/user/info", headers=headers).json())
```

### Get a JWT with the atr client

```sh
uv tool install "apache-trusted-releases @ git+https://github.com/apache/tooling-releases-client"
atr set asf.uid alice
atr set tokens.pat      # prompts, keeps the PAT out of shell history
atr jwt refresh         # the client renews the JWT itself afterwards
```

### JWT details

* Algorithm HS256, 30-minute lifetime, stateless. Verification allows two minutes of clock skew.
* In long-running scripts, re-exchange your PAT every 30 minutes.
* If the account is banned or removed in LDAP, its PATs are rejected.

### Auth labels used in the endpoint tables

* **Bearer JWT:** the endpoint declares the `BearerAuth` scheme in the spec.
* **System token (Bearer):** `/api/project/config` accepts only system tokens, which are minted for trusted services by foundation administrators.
* **Trusted Publisher JWT in body:** GitHub Actions workflows pass a GitHub OIDC token in the request body. These endpoints don’t declare `BearerAuth`.
* **GitHub workflow token:** callbacks from workflows that ATR itself triggered.
* **Test mode only:** available only when ATR runs in test mode.
* **PAT in body:** the JWT exchange itself, which takes the PAT in the request body.
* **Public:** the spec declares no security for the endpoint. Embargoed releases are still hidden from callers who can’t view them.

## Release workflow

Each step gives the `atr` command and the API call beneath it.

1. **Start a draft.** [`atr release start`](#cli-releases) calls [`POST /api/release/create`](/api/docs#/default/post_release_create).
2. **Upload artifacts** with `.asc` signature and SHA256/512 checksum. [`atr upload`](#cli-files-and-signatures) calls [`POST /api/release/store`](/api/docs#/default/post_release_store) (raw bytes), or send JSON to [`POST /api/release/upload`](/api/docs#/default/post_release_upload). Each upload creates a revision. Over 512 MiB, use [`atr rsync`](#cli-files-and-signatures). Optionally record the source commit with [`POST /api/release/commit`](/api/docs#/default/post_release_commit).
3. **Wait for checks.** [`atr check wait`](#cli-checks) polls [`GET /api/checks/ongoing/{project_key}/{version_key}`](/api/docs#/default/get_checks_ongoing). Finished doesn’t mean passed.
4. **Review.** [`atr check blockers`](#cli-checks) and [`atr check concerns`](#cli-checks) read [`GET /api/checks/list/{project_key}/{version_key}`](/api/docs#/default/get_checks_list). Acknowledge remaining concerns when starting the vote.
5. **Start the vote.** [`atr vote start`](#cli-voting-announcing-and-distribution) calls [`POST /api/vote/start`](/api/docs#/default/post_vote_start). The revision is frozen once voting starts. With streamlined voting, voters cast ballots with [`POST /api/vote/cast`](/api/docs#/default/post_vote_cast) (no `atr` command for this yet), and ATR treats `automatic_resolve_when_finished` and `automatic_publish_when_resolved` as `true` whatever the request says, because it always resolves and publishes a vote that passes with every vote +1.
6. **Tabulate and resolve.** [`atr vote tabulate`](#cli-voting-announcing-and-distribution) calls [`POST /api/vote/tabulate`](/api/docs#/default/post_vote_tabulate), then [`atr vote resolve`](#cli-voting-announcing-and-distribution) calls [`POST /api/vote/resolve`](/api/docs#/default/post_vote_resolve) (streamlined voting only).
7. **Record distributions** if your project requires them. [`atr distribution record`](#cli-voting-announcing-and-distribution) calls [`POST /api/distribution/record`](/api/docs#/default/post_distribution_record).
8. **Announce.** [`atr announce`](#cli-voting-announcing-and-distribution) calls [`POST /api/release/announce`](/api/docs#/default/post_release_announce). Five attempts per hour. After this, files can’t change.

## Limits and errors

| Scope | Limit |
| --- | --- |
| API requests overall | 500 per hour |
| JWT issuance | 10 per hour |
| Announcements | 5 attempts per hour |
| Upload via JSON endpoint | 512 MiB per file (use rsync beyond that) |

* `421`: plain HTTP used on the API. Switch to HTTPS.
* `429`: rate limit exceeded. API responses include `retry_after` when available.
* `403`: cross-site requests and browser navigations to API routes are refused for non-GET methods. Call the API from a client, not a browser page.

The spec lists only success responses (200, 201, 202) and doesn’t describe error bodies.

## API endpoints

The 63 endpoints, grouped by area. Each endpoint links to its entry in the [Swagger UI](/api/docs), which has the parameters, request body and response schema. The last column lists the `atr` commands that call the endpoint. See [CLI commands](#cli-commands) for the reverse view.

### API: Authentication

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/jwt/create`](/api/docs#/default/post_jwt_create) | PAT in body | Create a JWT. | [`atr jwt refresh`](#cli-other-api-backed-commands) |

### API: Releases

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/release/announce`](/api/docs#/default/post_release_announce) | Bearer JWT | Announce a release. | [`atr announce`](#cli-voting-announcing-and-distribution) |
| `POST` | [`/api/release/commit`](/api/docs#/default/post_release_commit) | Bearer JWT | Record the commit that the source archives in a draft were built from. | |
| `POST` | [`/api/release/create`](/api/docs#/default/post_release_create) | Bearer JWT | Create a release. | [`atr release start`](#cli-releases) |
| `POST` | [`/api/release/delete`](/api/docs#/default/post_release_delete) | Bearer JWT | Delete a release. | [`atr dev delete`](#cli-releases) |
| `POST` | [`/api/release/draft/delete`](/api/docs#/default/post_release_draft_delete) | Bearer JWT | Delete a draft release. | [`atr draft delete`](#cli-releases) |
| `GET` | [`/api/release/get/{project_key}/{version_key}`](/api/docs#/default/get_release_get) | Public | Get a release by project and version. | [`atr release info`](#cli-releases) [`atr upload`](#cli-files-and-signatures) [`atr sign`](#cli-files-and-signatures) [`atr check status`](#cli-checks) |
| `GET` | [`/api/release/manifest/{project_key}/{version_key}/{revision}`](/api/docs#/default/get_release_manifest) | Public | Not described in the spec. | |
| `GET` | [`/api/release/paths/{project_key}/{version_key}`](/api/docs#/default/get_release_paths) | Public | List paths in a release by project and version. | [`atr list`](#cli-releases) |
| `GET` | [`/api/release/paths/{project_key}/{version_key}/{revision}`](/api/docs#/default/get_release_paths) | Public | List paths in a release by project and version. | [`atr list`](#cli-releases) |
| `GET` | [`/api/release/revisions/{project_key}/{version_key}`](/api/docs#/default/get_release_revisions) | Public | List revisions by project and version. | [`atr revisions`](#cli-releases) |
| `POST` | [`/api/release/source`](/api/docs#/default/post_release_source) | Bearer JWT | Declare the repository and commit that source archives in a draft were built from, archive by archive. | |
| `POST` | [`/api/release/store`](/api/docs#/default/post_release_store) | Bearer JWT | Store a file in a release, streaming the raw request body to disk. | [`atr upload`](#cli-files-and-signatures) [`atr sign`](#cli-files-and-signatures) |
| `POST` | [`/api/release/upload`](/api/docs#/default/post_release_upload) | Bearer JWT | Upload a file to a release. | |
| `GET` | [`/api/releases/list`](/api/docs#/default/get_releases_list) | Public | List releases. | |

### API: Votes

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/vote/cast`](/api/docs#/default/post_vote_cast) | Bearer JWT | Cast a ballot in a streamlined vote. | |
| `POST` | [`/api/vote/resolve`](/api/docs#/default/post_vote_resolve) | Bearer JWT | Resolve a vote. | [`atr vote resolve`](#cli-voting-announcing-and-distribution) |
| `POST` | [`/api/vote/start`](/api/docs#/default/post_vote_start) | Bearer JWT | Start a vote. | [`atr vote start`](#cli-voting-announcing-and-distribution) |
| `POST` | [`/api/vote/tabulate`](/api/docs#/default/post_vote_tabulate) | Bearer JWT | Tabulate a vote. | [`atr vote tabulate`](#cli-voting-announcing-and-distribution) |

### API: Checks

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `GET` | [`/api/checks/list/{project_key}/{version_key}`](/api/docs#/default/get_checks_list) | Public | List checks by project and version. | [`atr check blockers`](#cli-checks) [`atr check concerns`](#cli-checks) [`atr check exceptions`](#cli-checks) [`atr check notes`](#cli-checks) [`atr check suggestions`](#cli-checks) |
| `GET` | [`/api/checks/list/{project_key}/{version_key}/{revision}`](/api/docs#/default/get_checks_list_revision) | Public | List checks by project, version, and revision. | [`atr check status`](#cli-checks) [`atr check blockers`](#cli-checks) [`atr check concerns`](#cli-checks) [`atr check exceptions`](#cli-checks) [`atr check notes`](#cli-checks) [`atr check suggestions`](#cli-checks) |
| `GET` | [`/api/checks/ongoing/{project_key}/{version_key}`](/api/docs#/default/get_checks_ongoing) | Public | Count ongoing checks by project, version, and optionally revision. | [`atr check wait`](#cli-checks) |
| `GET` | [`/api/checks/ongoing/{project_key}/{version_key}/{revision}`](/api/docs#/default/get_checks_ongoing) | Public | Count ongoing checks by project, version, and optionally revision. | [`atr check wait`](#cli-checks) |
| `POST` | [`/api/ignore/add`](/api/docs#/default/post_ignore_add) | Bearer JWT | Add a check ignore. | [`atr ignore add`](#cli-checks) |
| `POST` | [`/api/ignore/delete`](/api/docs#/default/post_ignore_delete) | Bearer JWT | Delete a check ignore. | [`atr ignore delete`](#cli-checks) |
| `GET` | [`/api/ignore/list/{project_key}`](/api/docs#/default/get_ignore_list) | Public | List ignores by project name. | [`atr ignore list`](#cli-checks) |

### API: Distributions

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `GET` | [`/api/distribution/list/{project_key}/{version_key}`](/api/docs#/default/get_distribution_list) | Public | List recorded distributions for a release. | [`atr distribution list`](#cli-voting-announcing-and-distribution) |
| `POST` | [`/api/distribution/record`](/api/docs#/default/post_distribution_record) | Bearer JWT | Record a manual distribution. | [`atr distribution record`](#cli-voting-announcing-and-distribution) |

### API: Keys

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/key/add`](/api/docs#/default/post_key_add) | Bearer JWT | Add a public OpenPGP key. | [`atr key add`](#cli-keys) |
| `POST` | [`/api/key/delete`](/api/docs#/default/post_key_delete) | Bearer JWT | Delete a public OpenPGP key. | [`atr key delete`](#cli-keys) |
| `GET` | [`/api/key/get/{fingerprint}`](/api/docs#/default/get_key_get) | Public | Get a public OpenPGP key by fingerprint. | [`atr key get`](#cli-keys) |
| `POST` | [`/api/keys/upload`](/api/docs#/default/post_keys_upload) | Bearer JWT | Upload a public OpenPGP KEYS file. | [`atr key upload`](#cli-keys) |
| `GET` | [`/api/keys/user/{asf_uid}`](/api/docs#/default/get_keys_user) | Public | List public OpenPGP keys by the ASF UID of a user. | [`atr key user`](#cli-keys) |
| `POST` | [`/api/ssh-key/delete`](/api/docs#/default/post_ssh_key_delete) | Bearer JWT | Delete an SSH key. | [`atr ssh delete`](#cli-keys) |
| `GET` | [`/api/ssh-keys/list/{asf_uid}`](/api/docs#/default/get_ssh_keys_list) | Public | List SSH keys by ASF UID. | [`atr ssh list`](#cli-keys) |

### API: Projects and policy

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `GET` | [`/api/policy/get/{project_key}`](/api/docs#/default/get_policy_get) | Public | Get project policy by name. | |
| `POST` | [`/api/policy/update`](/api/docs#/default/post_policy_update) | Bearer JWT | Update release policy fields for a project. | |
| `POST` | [`/api/project/config`](/api/docs#/default/post_project_config_upsert) | System token (Bearer) | Upsert a project's full configuration. | |
| `GET` | [`/api/project/get/{project_key}`](/api/docs#/default/get_project_get) | Public | Get a project by name. | [`atr sign`](#cli-files-and-signatures) |
| `GET` | [`/api/project/releases/{project_key}`](/api/docs#/default/get_project_releases) | Public | List releases by project name. | [`atr release list`](#cli-releases) |
| `GET` | [`/api/projects/list`](/api/docs#/default/get_projects_list) | Public | List projects. | |

### API: Committees and users

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `GET` | [`/api/committee/get/{name}`](/api/docs#/default/get_committee_get) | Public | Get a committee by name. | |
| `GET` | [`/api/committee/keys/{name}`](/api/docs#/default/get_committee_keys) | Public | List public OpenPGP keys by committee name. | [`atr sign`](#cli-files-and-signatures) |
| `GET` | [`/api/committee/projects/{name}`](/api/docs#/default/get_committee_projects) | Public | List projects by committee name. | |
| `GET` | [`/api/committees/list`](/api/docs#/default/get_committees_list) | Public | List committees. | |
| `GET` | [`/api/user/info`](/api/docs#/default/get_user_info) | Bearer JWT | Get information about a user. | |
| `GET` | [`/api/users/list`](/api/docs#/default/get_users_list) | Public | List known users. | |

### API: Catalog

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `GET` | [`/api/catalog/project/{project_key}`](/api/docs#/default/get_catalog_project) | Public | The release catalogue for a project as JSON: every catalogued version with its artifacts, grouped into cycles for projects that use them. | |

### API: Lifecycle events

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `GET` | [`/api/cle/project/{project_key}`](/api/docs#/default/get_cle_project) | Public | Generate an ECMA-428 Common Lifecycle Enumeration document covering every cycle and release of a project. | |
| `GET` | [`/api/cle/release/{project_key}/{version_key}`](/api/docs#/default/get_cle_release) | Public | Generate a CLE document filtered to a single release. | |

### API: SBOM

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/sbom/generate`](/api/docs#/default/post_sbom_generate) | Bearer JWT | Not described in the spec. | [`atr sbom generate`](#cli-other-api-backed-commands) |

### API: Signatures

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/signature/provenance`](/api/docs#/default/post_signature_provenance) | Bearer JWT | Get the provenance of a signature. | [`atr verify`](#cli-files-and-signatures) |

### API: Tasks

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `GET` | [`/api/task/get/{task_id}`](/api/docs#/default/get_task_get) | Bearer JWT | Not described in the spec. | [`atr sbom generate`](#cli-other-api-backed-commands) |

### API: Trusted publishing

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/distribute/record_from_workflow`](/api/docs#/default/post_distribution_record_from_workflow) | GitHub workflow token | Record the result of an automated distribution from the GH tooling-actions workflow. | |
| `POST` | [`/api/distribute/ssh/register`](/api/docs#/default/post_distribute_ssh_register) | Trusted Publisher JWT in body | Register an SSH key sent with a corroborating Trusted Publisher JWT, validating the requested release is in the correct phase. | |
| `POST` | [`/api/distribute/task/status`](/api/docs#/default/post_update_distribution_task_status) | GitHub workflow token | Update the status of a distribution task Validates the caller is a Github workflow, triggered by ATR itself | |
| `POST` | [`/api/publisher/distribution/record`](/api/docs#/default/post_publisher_distribution_record) | Trusted Publisher JWT in body | Record a distribution with a corroborating Trusted Publisher JWT. | |
| `POST` | [`/api/publisher/release/announce`](/api/docs#/default/post_publisher_release_announce) | Trusted Publisher JWT in body | Announce a release with a corroborating Trusted Publisher JWT. | |
| `POST` | [`/api/publisher/release/source`](/api/docs#/default/post_publisher_release_source) | Trusted Publisher JWT in body | Declare the repository and commit that source archives in a draft were built from, with a corroborating Trusted Publisher JWT. | |
| `POST` | [`/api/publisher/ssh/register`](/api/docs#/default/post_publisher_ssh_register) | Trusted Publisher JWT in body | Register an SSH key sent with a corroborating Trusted Publisher JWT. | |
| `POST` | [`/api/publisher/vote/resolve`](/api/docs#/default/post_publisher_vote_resolve) | Trusted Publisher JWT in body | Resolve a vote with a corroborating Trusted Publisher JWT. | |

### API: Test mode

| Method | Endpoint | Auth | Purpose | CLI commands |
| --- | --- | --- | --- | --- |
| `POST` | [`/api/test/activate-project`](/api/docs#/default/post_test_activate_project) | Test mode only | Restore a project to ACTIVE status (test mode only). | |
| `POST` | [`/api/test/archive-project`](/api/docs#/default/post_test_archive_project) | Test mode only | Set a project to DORMANT status, as a PMC archive does (test mode only). | |

## CLI commands

The 55 `atr` commands from [COMMANDS.md](https://github.com/apache/tooling-releases-client/blob/main/COMMANDS.md), each with the calls it makes, in order. Every command that talks to ATR is a thin wrapper over one or more API calls. `atr download`, `atr sign` and `atr verify` fetch files from outside the `/api` prefix, and `atr rsync` uses SSH. Endpoints that no command wraps can still be reached with `atr api get` and `atr api post`.

### CLI: Releases

| Command | Description | Underlying calls, in order |
| --- | --- | --- |
| `atr release start` | Start a release. | `POST` [`/api/release/create`](/api/docs#/default/post_release_create) Creates the draft. |
| `atr release list` | List releases for a project. | `GET` [`/api/project/releases/{project_key}`](/api/docs#/default/get_project_releases) The client warns when fewer releases are shown than exist. |
| `atr release info` | Show information about a release. | `GET` [`/api/release/get/{project_key}/{version_key}`](/api/docs#/default/get_release_get) |
| `atr draft delete` | Delete a draft release. | `POST` [`/api/release/draft/delete`](/api/docs#/default/post_release_draft_delete) |
| `atr list` | List all files within a release. | `GET` [`/api/release/paths/{project_key}/{version_key}`](/api/docs#/default/get_release_paths) or [`…/{revision}`](/api/docs#/default/get_release_paths) With --revision, …/{revision} is used. The client warns when the list is truncated. |
| `atr revisions` | List all revisions for a release. | `GET` [`/api/release/revisions/{project_key}/{version_key}`](/api/docs#/default/get_release_revisions) |
| `atr dev delete` | Delete a release. | `POST` [`/api/release/delete`](/api/docs#/default/post_release_delete) Developer command. Deletes a release. |

### CLI: Files and signatures

| Command | Description | Underlying calls, in order |
| --- | --- | --- |
| `atr upload` | Upload a file to a release. | **1.** `GET` [`/api/release/get/{project_key}/{version_key}`](/api/docs#/default/get_release_get) Reads the latest revision number first. **2.** `POST` [`/api/release/store`](/api/docs#/default/post_release_store) Sends the file as raw bytes, with project, version and relpath as query parameters. **3.** `GET` [`/api/release/get/{project_key}/{version_key}`](/api/docs#/default/get_release_get) Only if the upload is quarantined for archive validation: polls this, then GET /api/release/revisions/{project_key}/{version_key}, until a new revision appears (60 s). |
| `atr sign` | Sign a release file, optionally uploading the signature. | **1.** `GET /download/path/{project}/{version}/{path}` (outside `/api`) Downloads the file to sign. Signing happens locally. **2.** `GET` [`/api/project/get/{project_key}`](/api/docs#/default/get_project_get) With --upload: finds the project’s committee. **3.** `GET` [`/api/committee/keys/{name}`](/api/docs#/default/get_committee_keys) With --upload: checks that the signing key belongs to the committee. **4.** `GET` [`/api/release/get/{project_key}/{version_key}`](/api/docs#/default/get_release_get) With --upload: reads the latest revision number. **5.** `POST` [`/api/release/store`](/api/docs#/default/post_release_store) With --upload: uploads the .asc file, passing the revision as expected_revision. |
| `atr download` | Download a file from a release. | `GET /download/path/{project}/{version}/{path}` (outside `/api`) Not an API endpoint. |
| `atr rsync` | Rsync a release. | `SSH` Runs rsync over SSH (port 2222) to {asf_uid}@{host}:/{project}/{version}/. No HTTP API call. |
| `atr verify` | Verify an artifact. | **1.** `GET <artifact URL>` (outside `/api`) Downloads the artifact and its .asc from the URLs you give, not from the ATR API. **2.** `POST` [`/api/signature/provenance`](/api/docs#/default/post_signature_provenance) Sends the signature to find the matching OpenPGP key and committees. The client then verifies locally. |

### CLI: Checks

| Command | Description | Underlying calls, in order |
| --- | --- | --- |
| `atr check status` | Get check status for a release revision. | **1.** `GET` [`/api/release/get/{project_key}/{version_key}`](/api/docs#/default/get_release_get) Checks the phase. Checks only apply to release_candidate_draft. **2.** `GET` [`/api/checks/list/{project_key}/{version_key}/{revision}`](/api/docs#/default/get_checks_list_revision) Always called with a revision (the latest unless --revision is given). |
| `atr check wait` | Wait for checks to be completed. | `GET` [`/api/checks/ongoing/{project_key}/{version_key}`](/api/docs#/default/get_checks_ongoing) or [`…/{revision}`](/api/docs#/default/get_checks_ongoing) Polled until ongoing is 0. With --revision, …/{revision} is used. Defaults: --timeout 60, --interval 500 ms (minimum 500 ms). |
| `atr check blockers` | Get check blockers for the latest or specified release revision. | **1.** `GET` [`/api/checks/list/{project_key}/{version_key}`](/api/docs#/default/get_checks_list) or [`…/{revision}`](/api/docs#/default/get_checks_list_revision) Reads the check results for the latest revision, or for the one given by --revision (the …/{revision} variant). **2.** Local: The client filters the results to blockers and prints them. --members/-m changes how they are grouped. |
| `atr check concerns` | Get check concerns for the latest or specified release revision. | **1.** `GET` [`/api/checks/list/{project_key}/{version_key}`](/api/docs#/default/get_checks_list) or [`…/{revision}`](/api/docs#/default/get_checks_list_revision) Reads the check results for the latest revision, or for the one given by --revision (the …/{revision} variant). **2.** Local: The client filters the results to concerns and prints them. --members/-m changes how they are grouped. |
| `atr check exceptions` | Get check exceptions for the latest or specified release revision. | **1.** `GET` [`/api/checks/list/{project_key}/{version_key}`](/api/docs#/default/get_checks_list) or [`…/{revision}`](/api/docs#/default/get_checks_list_revision) Reads the check results for the latest revision, or for the one given by --revision (the …/{revision} variant). **2.** Local: The client filters the results to exceptions and prints them. --members/-m changes how they are grouped. |
| `atr check notes` | Get check notes for the latest or specified release revision. | **1.** `GET` [`/api/checks/list/{project_key}/{version_key}`](/api/docs#/default/get_checks_list) or [`…/{revision}`](/api/docs#/default/get_checks_list_revision) Reads the check results for the latest revision, or for the one given by --revision (the …/{revision} variant). **2.** Local: The client filters the results to notes and prints them. --members/-m changes how they are grouped. |
| `atr check suggestions` | Get check suggestions for the latest or specified release revision. | **1.** `GET` [`/api/checks/list/{project_key}/{version_key}`](/api/docs#/default/get_checks_list) or [`…/{revision}`](/api/docs#/default/get_checks_list_revision) Reads the check results for the latest revision, or for the one given by --revision (the …/{revision} variant). **2.** Local: The client filters the results to suggestions and prints them. --members/-m changes how they are grouped. |
| `atr ignore add` | Add a check ignore. | `POST` [`/api/ignore/add`](/api/docs#/default/post_ignore_add) |
| `atr ignore delete` | Delete a check ignore. | `POST` [`/api/ignore/delete`](/api/docs#/default/post_ignore_delete) |
| `atr ignore list` | List check ignores. | `GET` [`/api/ignore/list/{project_key}`](/api/docs#/default/get_ignore_list) |

### CLI: Voting, announcing and distribution

| Command | Description | Underlying calls, in order |
| --- | --- | --- |
| `atr vote start` | Start a vote. | `POST` [`/api/vote/start`](/api/docs#/default/post_vote_start) |
| `atr vote tabulate` | Tabulate a vote. | `POST` [`/api/vote/tabulate`](/api/docs#/default/post_vote_tabulate) |
| `atr vote resolve` | Resolve a vote. | `POST` [`/api/vote/resolve`](/api/docs#/default/post_vote_resolve) The client accepts passed or failed. The server refuses this unless the release uses streamlined voting. |
| `atr announce` | Announce a release. | `POST` [`/api/release/announce`](/api/docs#/default/post_release_announce) --body or --body-file sets the email body. If neither is given, the server renders the project’s announce template. |
| `atr distribution record` | DISTRIBUTION-VERSION [ARGS] | `POST` [`/api/distribution/record`](/api/docs#/default/post_distribution_record) |
| `atr distribution list` | List recorded distributions for a release. | `GET` [`/api/distribution/list/{project_key}/{version_key}`](/api/docs#/default/get_distribution_list) |

**No `atr vote cast` command yet.** The API has [`POST /api/vote/cast`](/api/docs#/default/post_vote_cast) (ballots in streamlined votes, bearer token required), but the client at commit `a07f9fe` does not wrap it. Its body takes `project`, `version`, `choice` (`+1`, `0` or `-1`) and a `comment`, which is required for `0` and `-1`. It returns `success` and the `receipt_email_to` address. Until the client adds a command, it can be called with `atr api post /vote/cast --project example --version 1.0.0 --choice +1 --comment "Looks good"`. That command line is untested.

### CLI: Keys

| Command | Description | Underlying calls, in order |
| --- | --- | --- |
| `atr key add` | Add an OpenPGP key. | `POST` [`/api/key/add`](/api/docs#/default/post_key_add) Sends the key text, your asf.uid and the committees. |
| `atr key delete` | Delete an OpenPGP key. | `POST` [`/api/key/delete`](/api/docs#/default/post_key_delete) |
| `atr key get` | Get an OpenPGP key. | `GET` [`/api/key/get/{fingerprint}`](/api/docs#/default/get_key_get) |
| `atr key upload` | Upload a KEYS file. | `POST` [`/api/keys/upload`](/api/docs#/default/post_keys_upload) Uploads a KEYS file for one committee. |
| `atr key user` | List OpenPGP keys for a user. | `GET` [`/api/keys/user/{asf_uid}`](/api/docs#/default/get_keys_user) |
| `atr ssh list` | List SSH keys. | `GET` [`/api/ssh-keys/list/{asf_uid}`](/api/docs#/default/get_ssh_keys_list) Defaults to your configured asf.uid. |
| `atr ssh delete` | Delete an SSH key. | `POST` [`/api/ssh-key/delete`](/api/docs#/default/post_ssh_key_delete) |

### CLI: Other API-backed commands

| Command | Description | Underlying calls, in order |
| --- | --- | --- |
| `atr sbom generate` | Generate and augment a CycloneDX SBOM for a release artifact. | **1.** `POST` [`/api/sbom/generate`](/api/docs#/default/post_sbom_generate) Queues a task. **2.** `GET` [`/api/task/get/{task_id}`](/api/docs#/default/get_task_get) With --wait: polls every 0.5 s until the task completes or fails (--timeout 600). This endpoint needs a bearer token. |
| `atr jwt refresh` | Fetch a JWT using the stored PAT and store it in config. | `POST` [`/api/jwt/create`](/api/docs#/default/post_jwt_create) Exchanges your stored PAT for a JWT and stores the JWT in the config file. |
| `atr api get` | GET a resource from the API. | `GET <any path under /api>` Sends a GET with your JWT. The path is appended to /api. |
| `atr api post` | POST a resource to the API. | `POST <any path under /api>` Sends the --key value options as a JSON body, with your JWT. |

### CLI: Local-only commands

| Command | Description | Underlying calls, in order |
| --- | --- | --- |
| `atr jwt show` | Show stored JWT token. | Local: Shows the stored JWT. |
| `atr jwt dump` | Show decoded JWT payload from stored config. | Local: Decodes the stored JWT payload. |
| `atr jwt info` | Show JWT payload in human-readable form. | Local: Shows the JWT payload in readable form. |
| `atr config file` | Display the configuration file contents. | Local only. |
| `atr config path` | Show the configuration file path. | Local only. |
| `atr set` | Set a configuration value using dot notation. | Local: Sets a config value with dot notation, for example asf.uid. |
| `atr show` | Show a configuration value using dot notation. | Local only. |
| `atr drop` | Remove a configuration key using dot notation. | Local only. |
| `atr dev env` | Show the environment variables. | Local only. |
| `atr dev key` | Return a test OpenPGP key. | Local only. |
| `atr dev pat` | Read a PAT from development configuration. | Local only. |
| `atr dev pwd` | Show the current working directory. | Local only. |
| `atr dev stamp` | Update version in pyproject.toml. | Local only. |
| `atr dev token` | Generate a random alphabetical token. | Local only. |
| `atr dev user` | Show the value of $USER. | Local only. |
| `atr docs` | Show comprehensive CLI documentation in Markdown. | Local: Prints the CLI documentation in Markdown. |
