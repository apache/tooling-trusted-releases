# 3.13. Authorization security

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.12.` [Authentication security](authentication-security)

**Next**: `3.14.` [Input validation](input-validation)

**Sections**:

* [Overview](#overview)
* [Roles and principals](#roles-and-principals)
* [LDAP integration](#ldap-integration)
* [Access control for releases](#access-control-for-releases)
* [Access control for tokens](#access-control-for-tokens)
* [Access control for distribution management](#access-control-for-distribution-management)
* [Access control for SSH and rsync access](#access-control-for-ssh-and-rsync-access)
* [Access control for key management](#access-control-for-key-management)
* [Access control for policy management](#access-control-for-policy-management)
* [Access control for project management](#access-control-for-project-management)
* [Access control for admin operations](#access-control-for-admin-operations)
* [Phase-based access control](#phase-based-access-control)
* [Implementation patterns](#implementation-patterns)
* [Caching behavior](#caching-behavior)
* [Implementation references](#implementation-references)

## Overview

ATR uses role-based access control (RBAC) where roles are derived from ASF LDAP group memberships. Authentication (covered in [Authentication security](authentication-security)) establishes *who* a user is; authorization determines *what* they can do.

The authorization model is committee-centric: most permissions are granted based on a user's relationship to a committee (PMC membership) or project (committer status).

## Roles and principals

### Note

This documents the current status of roles in the application, which will be reorganized, per these Issues:

* [Review permissions for all actions in ATR](https://github.com/apache/tooling-trusted-releases/issues/242)
* [Allow release managers to be designated](https://github.com/apache/tooling-trusted-releases/issues/520)
* [Promotion permissions for phase transitions and distributions](https://github.com/apache/tooling-trusted-releases/issues/523)

ATR recognizes the following roles, derived from ASF LDAP:

* **Public**: Unauthenticated users. Can view public information about releases and projects.

* **Committer**: Any authenticated ASF committer. Can create Personal Access Tokens and view their own committees and projects. Determined by existence in LDAP `ou=people,dc=apache,dc=org`.

* **Project Participant**: A committer who is a member of a specific project. Can start releases, upload artifacts, and cast votes for that project. Determined by the `member` attribute in the project's LDAP group.

* **Release Manager**: A committer designated by the PMC as a release manager, through the roster on the committee page. Has all participant permissions plus can start and resolve votes, publish and announce releases, record distributions, and edit project metadata and policy, but does not get a binding vote. All PMC members are release managers, so PMC members cannot be designated, and a designation is removed automatically when a designated release manager joins the PMC. Designations are stored in the ATR database, not in LDAP.

* **PMC Member**: A committer who is on the PMC (Project Management Committee) for a specific committee. Has all release manager permissions plus can designate release managers, create and archive projects, manage categories and check ignores, and manage signing keys. Determined by the `owner` attribute in the committee's LDAP group.

* **Chair**: A PMC chair. Currently has the same permissions as PMC Member in ATR. Determined by membership in `cn=pmc-chairs,ou=groups,ou=services,dc=apache,dc=org`.

* **ASF Member**: An ASF Member. Currently has the same permissions as a regular committer in ATR, though this may change. Determined by membership in `cn=member,ou=groups,dc=apache,dc=org`.

* **Infrastructure Root**: ASF Infrastructure team with root access. Has administrative capabilities. Determined by membership in `cn=infrastructure-root,ou=groups,ou=services,dc=apache,dc=org`.

* **Tooling Team**: Members of the ASF Tooling service group, determined by membership in `cn=tooling,ou=groups,ou=services,dc=apache,dc=org`. This group grants ATR administrator rights only (alongside Infrastructure Root). The "tooling" committee roster is sourced separately from the Tooling PMC group `cn=tooling,ou=project,ou=groups,dc=apache,dc=org`, like any other PMC.

## LDAP integration

Authorization data is fetched from ASF LDAP using the [`principal`](/ref/atr/principal.py) module. The key LDAP bases are:

* `ou=people,dc=apache,dc=org` - All committers
* `ou=project,ou=groups,dc=apache,dc=org` - Project and committee groups
* `cn=member,ou=groups,dc=apache,dc=org` - ASF Members
* `cn=pmc-chairs,ou=groups,ou=services,dc=apache,dc=org` - PMC Chairs
* `cn=infrastructure-root,ou=groups,ou=services,dc=apache,dc=org` - Infrastructure root
* `cn=tooling,ou=groups,ou=services,dc=apache,dc=org` - Tooling service group (grants ATR admin rights only; the "tooling" committee is sourced from `ou=project,ou=groups` like any PMC)

The [`Committer`](/ref/atr/principal.py) (Committer) class fetches a user's full authorization profile from LDAP, including their committee memberships (PMC membership) and project participations (committer access).

## Access control for releases

Release operations have the following access requirements:

**View release information** (public pages, download links):

* Allowed for: Everyone, including unauthenticated users
* This includes the following API endpoints, which are intentionally unauthenticated because they serve the same public information available on the website:
  * `/api/checks/list/<project>/<version>` — check results for a release
  * `/api/checks/ongoing/<project>/<version>` — count of ongoing checks
  * `/api/release/paths/<project>/<version>` — file paths in a release
  * `/api/release/revisions/<project>/<version>` — revision history of a release
  * `/api/ssh-keys/list/<asf_uid>` — enumerates SSH key fingerprints for any user
  * `/api/keys/user/<asf_uid>` — enumerates OpenPGP keys for any user
* Rationale: ASF release artifacts, their check results, and their metadata are public by design. The release process is transparent and these endpoints support tooling that consumes public release data.

**Start a new release**:

* Allowed for: Project participants (committers on the project)
* Checked via: `is_participant_of(project.committee_key)`

**Upload release artifacts**:

* Allowed for: Project participants
* Additional constraint: Must be the user who started the release, or a PMC member

**Cast a vote on a release**:

* Allowed for: Project participants
* Constraint: Cannot vote multiple times; can change existing vote

**Resolve a vote (tally votes and determine outcome)**:

* Allowed for: PMC members only
* Checked via: `is_member_of(project.committee_key)`

**Finish a release (publish to distribution)**:

* Allowed for: PMC members only
* Constraint: Vote must be resolved with a passing result

**Cancel or delete a release**:

* Draft releases: Project participants
* Finished releases: ATR administrators only

## Access control for tokens

Token operations apply to the authenticated user:

**Create a Personal Access Token**:

* Allowed for: Any authenticated committer
* Constraint: Can only create tokens for themselves

**List own Personal Access Tokens**:

* Allowed for: Any authenticated committer
* Constraint: Can only see their own tokens

**Revoke a Personal Access Token**:

* Allowed for: The token owner, or administrators
* Constraint: Users can only revoke their own tokens (unless admin)

**Revoke all tokens for a user (admin)**:

* Allowed for: ATR administrators only
* Interface: Admin "Revoke user tokens" page
* Constraint: Requires typing "REVOKE" as confirmation

**Revoke all SSH keys for a user (admin)**:

* Allowed for: ATR administrators only
* Interface: Admin "Revoke user SSH keys" page
* Constraint: Requires typing "REVOKE" as confirmation

**Exchange PAT for JWT**:

* Allowed for: Anyone with a valid PAT
* Note: This is an unauthenticated endpoint; the PAT serves as the credential

**System tokens**:

* Allowed for: Foundation administrators only (not committer self-service)
* Note: System tokens are PATs for a service identity rather than a person. Endpoints that accept them (via `auth_scheme=api_auth.Auth.SYSTEM_BEARER`) apply no committee membership check, so the calling service must establish any committee authorisation upstream. See [System tokens](authentication-security#system-tokens) in the authentication guide for the mechanism.

## Access control for distribution management

Distribution records track where a release's artifacts have been published (for example to Maven Central, PyPI, or npm). These operations live in the [`distributions`](/ref/atr/storage/writers/distributions.py) storage writer, and are all release manager tier.

**Automate a distribution publish** (queue a background task that publishes and records a distribution):

* Allowed for: Release managers (PMC members and designated release managers)
* Obtained via: `write.as_project_release_manager(project_key)` or a higher tier such as `write.as_committee_member(committee_key)`
* Constraint: The release must belong to the writer's committee, and `write.ensure_release_writable(release)` rejects the call if the release is embargoed and the actor is not a committee member

**Record a distribution** (record that a release has been published, including upgrading a staging record to a final one):

* Allowed for: Release managers
* Same committee and embargo constraints as `automate`

**Delete a distribution record**:

* Allowed for: Release managers
* Same committee and embargo constraints as above

Each of these operations validates the release's committee via `__validate_release_in_committee` before acting, so a caller cannot record or delete a distribution for a release outside their own committee even if they somehow obtain a matching release key. All three operations are audited (`distribution_automate`, `distribution_record` or `distribution_upgrade`, and `distribution_delete`).

## Access control for SSH and rsync access

ATR runs an SSH server (see [`ssh.py`](/ref/atr/ssh.py)) that accepts `rsync` commands for uploading and downloading release artifacts outside the web UI. Authentication is by SSH public key (see [SSH authentication](authentication-security#ssh-authentication) in the authentication guide); the following rules apply once a connection is authenticated, and are enforced independently of the web-based [access control for releases](#access-control-for-releases).

**Read a release over rsync** (`rsync --server --sender ...`):

* Allowed for: Committers on the release's project
* Checked via: `_step_06a_validate_read_permissions`, using `user.is_committer`
* Constraint: The release's phase must be `RELEASE_CANDIDATE_DRAFT`, `RELEASE_CANDIDATE`, or `RELEASE_PREVIEW`; reads of the `RELEASE` phase are refused over SSH (use the public download endpoints instead)
* Constraint: If the release is embargoed, the reader must additionally satisfy `user.can_view_embargoed_release` (committee member, or admin)
* Constraint: An optional path tag segment (for example a "source" or "convenience" tag) is only accepted if it appears in the project's `file_tag_mappings` policy

**Write (upload) to an existing release over rsync**:

* Allowed for: Committers on the release's project
* Checked via: `_step_06b_validate_write_permissions`, using `user.is_committer`
* Constraint: The release must be in the `RELEASE_CANDIDATE_DRAFT` phase; uploads are refused once voting has started

**Create a new release over rsync** (the target project/version does not exist yet):

* Allowed for: Committee members only, which is a stricter requirement than uploading to an existing draft
* Checked via: `_step_06b_validate_write_permissions`, using `user.is_committee_member`

**Path and command validation** (applies to every rsync command, read or write):

* The command must be exactly `rsync --server [--sender] -Dgloprtv [--dirs] [--delete] . PATH`; any other flag combination is refused
* `PATH` must resolve to a `/PROJECT/VERSION/` directory (write), or a `/PROJECT/VERSION/` or `/PROJECT/VERSION/TAG/` directory (read), where `PROJECT` and `VERSION` are validated against the same `safe.ProjectKey` and `safe.VersionKey` types used elsewhere in ATR
* Uploads are capped at 2,000,000,000 bytes per rsync invocation (`--max-size` is injected server side and cannot be overridden by the client)
* Connections are rate limited per IP address (100 per 60 seconds, applied even to failed authentication) and per authenticated user (10 per 60 seconds)

Uploads are funnelled through [`create_revision_with_quarantine`](/ref/atr/storage/writers/revision.py), the same quarantine and revisioning path used by web uploads, so the same file-level checks apply regardless of whether the upload arrived over rsync or HTTP.

## Access control for key management

Key management covers both OpenPGP signing keys ([`storage/writers/keys.py`](/ref/atr/storage/writers/keys.py)) and SSH keys ([`storage/writers/ssh.py`](/ref/atr/storage/writers/ssh.py)).

### OpenPGP keys

**Upload or parse an OpenPGP key not yet linked to a committee**:

* Allowed for: Any authenticated committer
* Note: The key is stored once its ASF UID can be determined (from a `name@apache.org` user ID, or from an LDAP email match), but it is not yet published in any committee's KEYS file until it is associated with one

**Delete an OpenPGP key**:

* Allowed for: The key's owner (matched by `apache_uid`) only
* Constraint: Deletion is a soft delete (sets `deleted`); an upload of the same key later automatically undeletes it

**Associate a key with a committee, or import a committee's `KEYS` file**:

* Allowed for: Participants of the target committee
* Constraint: `associate_fingerprint` links a key that already exists in the database, regardless of who owns it; the caller does not need to own the key, only to participate in the committee they are linking it to

**Update which committees a key is associated with**:

* Allowed for: The key's owner
* Constraint: Adding a new committee association additionally requires that the owner be a participant of that committee (`write.as_committee_participant(committee_key)`); removing an association has no such check, since it only reduces exposure

**Enable or disable automated KEYS file publication for a committee**:

* Allowed for: PMC members
* Checked via: `WriteAsCommitteeMember` tier (`set_automated_keys_file`)

**Delete all keys linked to a committee**:

* Allowed for: Foundation administrators, obtained via `write.as_committee_admin(committee_key)`
* Note: This check only requires foundation admin rights; unlike other committee-scoped writers, it does not additionally require the admin to be a member of that specific committee

Minimum key strength (RSA, ECDSA, or EdDSA only, with minimum key sizes) is enforced for any key created after 2026-04-01, regardless of who uploads it; see `_validate_key_strength`.

### SSH keys

**Add or delete your own SSH key**:

* Allowed for: Any authenticated committer, for their own keys only
* Note: Deleting a key sends a notification email to the owner so that an unexpected deletion is noticed

**Issue a workflow (GitHub Actions trusted publisher) SSH key**:

* Allowed for: Participants of the target committee
* Constraint: The key is time-limited (20 minutes) and single-use; the GitHub Actions OIDC token's `jti` claim is consumed on first use, so a replayed token is rejected

**Revoke all SSH keys for a user**:

* Allowed for: Foundation administrators only
* Interface: Admin "Revoke user SSH keys" page (see [Access control for tokens](#access-control-for-tokens))

## Access control for policy management

Release policy (`ReleasePolicy`) governs how a project composes, votes on, and finishes its releases. All policy edits in [`storage/writers/policy.py`](/ref/atr/storage/writers/policy.py) are release manager tier, obtained through the project's committee.

**Edit compose policy** (license check mode, source excludes, file tag mappings):

* Allowed for: Release managers (PMC members and designated release managers)

**Edit vote policy** (vote mode, minimum voting hours, recipients, vote templates):

* Allowed for: Release managers
* Constraint: Manual voting (`VoteMode.MANUAL`) cannot be selected for podling projects

**Edit finish policy** (announcement subject/template, recipients, download retention):

* Allowed for: Release managers

**Edit trusted publishing configuration** (GitHub repository, branch, and workflow paths used for OIDC-based publishing):

* Allowed for: Release managers

**Edit version scheme** (version method, version pattern, calendar-versioning format, cycle match):

* Allowed for: Release managers
* Note: Changing the scheme reassigns release cycles for the project via `cycles.reassign_release_cycles`

**Edit project lifecycle cycle dates** (end of development, end of support, end of life):

* Allowed for: Release managers
* Constraint: Dates can only be moved forward once set, not cleared; each change is recorded as a `LifecycleEvent` for audit purposes

**Apply a policy update without an interactive session** (`edit_no_commit`):

* Allowed for: Foundation admin tier only
* Note: This is used by system services such as the `.asf.yaml` ingestion pipeline, which establish their own authorization upstream (see [System tokens](authentication-security#system-tokens)) before reaching this method; it performs no additional committee check itself

## Access control for project management

Project metadata, categorisation, and lifecycle are managed in [`storage/writers/project.py`](/ref/atr/storage/writers/project.py), split across two tiers.

**Edit project metadata** (display name, description, homepage, links, repositories, standards):

* Allowed for: Release managers
* Constraint: Refused once the project's status is `RETIRED`

**Edit security metadata** (security contact, threat model links):

* Allowed for: Release managers
* Constraint: The security contact address is validated against the committee via `validation.validate_security_contact`

**Set a project's download page**:

* Allowed for: Release managers
* Constraint: This is set-once; if a download page is already recorded, the call is a no-op rather than an overwrite

**Add or remove a project category or programming language**:

* Allowed for: PMC members
* Constraint: A fixed set of categories in `registry.FORBIDDEN_PROJECT_CATEGORIES` can never be added or removed this way

**Create a project**:

* Allowed for: PMC members
* Note: A new project may derive defaults (description, categories, version scheme, release policy) from an existing "super-project" if its key is a dash-separated prefix of the new key

**Archive (retire) a project**:

* Allowed for: PMC members
* Constraint: Requires an already-approved Community Approval Process (CAP) request for the `ARCHIVE` action; refused if the project has any non-draft releases, any draft releases, or is the only active project left in its committee

**Delete a project**:

* Allowed for: PMC members
* Constraint: Requires an already-approved CAP request for the `DELETE` action; refused if the project has any releases at all, or is the only active project left in its committee

**Request CAP approval for archiving or deleting a project**:

* Allowed for: PMC members
* Note: Creates an `ApprovalRequest` row and schedules an automatic resolution task; duplicate in-flight requests for the same project or release are rejected

**Record a CAP approval outcome, or upsert a project's configuration from `.asf.yaml`**:

* Allowed for: Foundation admin tier only
* Note: As with policy's `edit_no_commit`, these methods have no committee gate of their own. The caller's right to act for the committee is established upstream, by the CAP resolution service or the `.asf.yaml` ingestion feature respectively, both of which use the fixed system service identity rather than a human session

## Access control for admin operations

The entire `/admin` web interface is gated by one check: the [`_check_admin_access`](/ref/atr/blueprints/admin.py) `before_request` hook on the admin blueprint, which requires a valid session and `user.is_admin`. Individual routes under `/admin` do not repeat this check; the blueprint-level gate is the sole enforcement point for the web UI.

Admin-only operations include, in addition to token and SSH key revocation (see [Access control for tokens](#access-control-for-tokens)):

* Site banner management (set, view history, restore a previous banner)
* Catalogue correction: move, rename, or delete/rehome a project between committees, and bulk CSV import/export of projects, releases, and artifacts
* Deleting releases, deleting a committee's OpenPGP keys, or deleting test-mode OpenPGP keys
* Checking or updating public signing keys from remote data
* LDAP lookups by ASF UID or email
* Viewing application configuration (secrets are redacted by name pattern), performance statistics, recent logs, and database/filesystem consistency checks
* Rotating the JWT signing key, and creating or revoking system tokens
* In test mode only: managing the synthetic "test" committee roster

**Admin impersonation ("browse as")**:

* Allowed for: Foundation administrators only
* Mechanism: The admin's session is rewritten to carry the target user's identity (committees, projects, full name), but the session's `admin_uid` field retains the *original* admin's ASF UID, so impersonated actions remain traceable back to the admin who initiated them
* Audit: Logged via `log.auth_event("impersonate", admin_id, as_user=...)` at the moment impersonation begins

**Defense in depth for non-web callers**:

* Storage-layer entry points that grant admin tier, `write.as_foundation_admin()` and `write.as_committee_admin(committee_key)`, each independently check `user.is_admin` inside the storage layer. This matters for callers that do not go through the `/admin` blueprint at all, such as background tasks
* This is distinct from the system-service writers (`WriteAsAsfYamlService`, `WriteAsCapResolveService`, `WriteAsDistCatalogService`, `WriteAsInactivitySweepService`, `WriteAsJwtMintService`, `WriteAsAutomatedMailService`), which reach admin-tier methods internally but are themselves gated by requiring the fixed `constants.SYSTEM_SERVICE_UID` identity rather than by `user.is_admin`. A system service identity is not a human admin account

## Phase-based access control

Release lifecycle phases gate many operations at once, and the rules are enforced in several different modules. This section consolidates them into a single reference; each rule links back to the section or module that enforces it.

### Phase definitions

| Phase | Description |
| ------- | ------------- |
| `RELEASE_CANDIDATE_DRAFT` | Candidate files are being added and checked; not yet under vote |
| `RELEASE_CANDIDATE` | The committee is voting on the candidate |
| `RELEASE_PREVIEW` | The vote passed; files are staged for announcement |
| `RELEASE` | The release has been announced and is publicly distributed |

### Operation access by phase

| Operation | DRAFT | CANDIDATE | PREVIEW | RELEASE | Authorization | Enforced in |
| ----------- | :-----: | :---------: | :-------: | :-------: | ---------------- | ------------- |
| Upload artifacts (web or rsync) | yes | no | no | no | Project participants | [`storage/writers/revision.py`](/ref/atr/storage/writers/revision.py) (`create_revision_with_quarantine`, `allowed_phases`) |
| SSH/rsync write | yes | no | no | no | Committers (committee members to create a new release) | [`ssh.py`](/ref/atr/ssh.py) (`_step_06b_validate_write_permissions`) |
| SSH/rsync read | yes | yes | yes | no | Committers; committee members (or admins) if embargoed | [`ssh.py`](/ref/atr/ssh.py) (`_step_06a_validate_read_permissions`) |
| Download over HTTP | yes | yes | yes | yes | Public, subject to embargo | [`get/download.py`](/ref/atr/get/download.py) |
| Start a vote (DRAFT to CANDIDATE) | yes | — | — | — | Release managers | [`storage/writers/release.py`](/ref/atr/storage/writers/release.py) (`start_vote_no_commit`) |
| Cast or change a vote | — | yes | — | — | Project participants | See [Access control for releases](#access-control-for-releases) |
| Resolve a vote: pass moves to PREVIEW, fail or cancel returns to DRAFT | — | yes | — | — | Release managers | [`storage/writers/vote.py`](/ref/atr/storage/writers/vote.py) (`resolve`) |
| Announce a release (PREVIEW to RELEASE) | — | — | yes | — | Release managers | [`storage/writers/announce.py`](/ref/atr/storage/writers/announce.py) (`release`) |
| Record or automate a distribution | — | — | — | — | Release managers, any phase once the release exists | [Access control for distribution management](#access-control-for-distribution-management) |

### Starting a vote: preconditions

Moving a release from `RELEASE_CANDIDATE_DRAFT` to `RELEASE_CANDIDATE` additionally requires, all checked in `start_vote_no_commit`:

* No ongoing tasks (checks still running) and no pending quarantine for the release's latest revision
* No unresolved blocker checks (`interaction.has_blocker_checks`)
* At least one file present in the draft
* Any required concern acknowledgements (for example license or provenance concerns) have been acknowledged

## Implementation patterns

Authorization checks in ATR follow consistent patterns.

### Checking PMC membership

To verify a user is a PMC member for a committee:

```python
from atr.principal import Authorisation

auth = await Authorisation()
if not auth.is_member_of(committee_key):
    raise Forbidden("PMC membership required")
```

### Checking project participation

To verify a user is a committer on a project:

```python
auth = await Authorisation()
if not auth.is_participant_of(project.committee_key):
    raise Forbidden("Project participation required")
```

### Getting all memberships

To get the set of committees or projects a user belongs to:

```python
auth = await Authorisation()
committees = auth.member_of()      # Returns frozenset of committee names
projects = auth.participant_of()   # Returns frozenset of project names
```

### Web vs API authorization

For web requests, the [`Authorisation`](/ref/atr/principal.py) (Authorisation) class reads the session automatically:

```python
auth = await Authorisation()  # Uses ASFQuart session
```

For API requests, the ASF UID is extracted from the JWT and passed explicitly:

```python
auth = await Authorisation(asf_uid)  # Uses LDAP lookup
```

Both paths use the same authorization logic and caching.

## Caching behavior

LDAP queries are expensive, so authorization data is cached in [`principal.Cache`](/ref/atr/principal.py) (Cache). The cache stores:

* `member_of` - Set of committees where the user is a PMC member
* `participant_of` - Set of projects where the user is a committer
* `last_refreshed` - Timestamp of last LDAP query

The cache TTL is 300 seconds (`cache_for_at_most_seconds`). When the cache is stale, the next authorization check triggers an LDAP refresh.

The cache is per-user and in-memory. It does not persist across server restarts. If LDAP group memberships change, users may need to wait up to 5 minutes for ATR to reflect the change, or log out and back in.

### Test mode

When running in test mode (env == `TESTS`), a special "test" user and "test" committee are available. **This should never be enabled in production.** The security implications are significant:

1. All authenticated users (not just the test user) are granted membership in the "test" committee and project [`principal`](/ref/atr/principal.py).
2. Authorization checks in the storage layer are completely skipped for the test committee [`release`](/ref/atr/storage/writers/release.py).
3. Rate limiting is disabled [`server`](/ref/atr/server.py).
4. A hardcoded "test" user bypasses LDAP verification.

If this is accidentally left enabled in production, every authenticated user gains unauthorized access to the test committee and its resources. This flag is intended for use only in development and test environments where `DEBUG_MODE` is also set.
As such, on starting the server in production mode (env == `PRODUCTION`), a safety check will run to ensure certain sensitive values are not misconfigured.

## Implementation references

* [`principal.py`](/ref/atr/principal.py) - Core authorization classes and LDAP integration
* [`web.py`](/ref/atr/web.py) - Request context and committer access
* [`ldap.py`](/ref/atr/ldap.py) - Low-level LDAP search functionality
