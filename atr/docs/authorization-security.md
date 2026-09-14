# 3.14. Authorization security

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.13.` [Sessions](sessions)

**Next**: `3.15.` [Input validation](input-validation)

**Sections**:

* [Overview](#overview)
* [Roles and principals](#roles-and-principals)
* [LDAP integration](#ldap-integration)
* [Access control for releases](#access-control-for-releases)
* [Phase-based access control](#phase-based-access-control)
* [Access control for third-party distributions](#access-control-for-third-party-distributions)
* [Access control for tokens](#access-control-for-tokens)
* [Access control for keys](#access-control-for-keys)
* [Access control for project policy](#access-control-for-project-policy)
* [Access control for projects](#access-control-for-projects)
* [Access control for administrators](#access-control-for-administrators)
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

* Allowed for: Release managers and PMC members
* Checked via: the release manager storage tier, obtained with `as_project_release_manager`, which admits a project's designated release managers (`is_release_manager`) and its PMC members (`is_member_of`)

**Finish a release (publish to distribution)**:

* Allowed for: Release managers and PMC members
* Constraint: Vote must be resolved with a passing result

**Cancel or delete a release**:

* Draft releases: Project participants
* Finished releases: ATR administrators only

## Phase-based access control

A release moves through four phases - draft (`RELEASE_CANDIDATE_DRAFT`), candidate
(`RELEASE_CANDIDATE`), preview (`RELEASE_PREVIEW`), and published (`RELEASE`). The
phase a release is in, alongside the caller's role, determines which operations are
permitted. The tables below consolidate those phase rules; the roles they name are
defined in [Roles and principals](#roles-and-principals).

### Release lifecycle operations

| Operation | Release phase | Who |
| --- | --- | --- |
| Create a release | starts a new **Draft** | Participant |
| Upload or edit files | **Draft** only | Participant *(the starter, or a PMC member)* |
| Start a vote | **Draft** → Candidate | Release manager or PMC member |
| Cast a vote | **Candidate** | Participant |
| Resolve a vote | **Candidate** → Preview (passed) / Draft (failed) | Release manager or PMC member |
| Announce / publish | **Preview** → Release | Release manager or PMC member |

### rsync (SSH) access

Reads and writes over rsync are gated by phase as well as role.

| Release phase | Read | Write |
| --- | --- | --- |
| Draft `RELEASE_CANDIDATE_DRAFT` | Committer *(committee member if embargoed)* | Committer to upload *(committee member to create)* |
| Candidate `RELEASE_CANDIDATE` | Committer *(committee member if embargoed)* | — |
| Preview `RELEASE_PREVIEW` | Committer *(committee member if embargoed)* | — |
| Published `RELEASE` | — | — |

ATR administrators may act throughout.

## Access control for third-party distributions

A distribution records where a release's artifacts have been published to a
third-party platform - for example a package registry or a download platform.
Release managers record and remove these distributions:

* **Record** a distribution - note a distribution that exists on a platform.
* **Delete** a distribution - remove a recorded distribution.

**Allowed for**: Release managers and PMC members, obtained with
`as_project_release_manager`.

**Constraints** (applied to both):

* The release must belong to the caller's own committee.
* The project must be active; all release actions are disabled once a project is
  archived.
* Writing to an embargoed release additionally requires PMC membership.

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
* Interface: Admin Users dashboard, "Revoke user tokens" tab
* Constraint: Requires typing "REVOKE" as confirmation

**Revoke all SSH keys for a user (admin)**:

* Allowed for: ATR administrators only
* Interface: Admin Users dashboard, "Revoke user SSH keys" tab
* Constraint: Requires typing "REVOKE" as confirmation

**Exchange PAT for JWT**:

* Allowed for: Anyone with a valid PAT
* Note: This is an unauthenticated endpoint; the PAT serves as the credential

**System tokens**:

* Allowed for: Foundation administrators only (not committer self-service)
* Note: System tokens are PATs for a service identity rather than a person. Endpoints that accept them (via `auth_scheme=api_auth.Auth.SYSTEM_BEARER`) apply no committee membership check, so the calling service must establish any committee authorisation upstream. See [System tokens](authentication-security#system-tokens) in the authentication guide for the mechanism.

## Access control for keys

ATR holds two kinds of key: OpenPGP signing keys, used to sign release artifacts,
and SSH keys, used to authenticate rsync uploads.

### OpenPGP signing keys

**Upload an OpenPGP key**:

* Allowed for: Any committer, for their own key
* Checked via: `ensure_stored_one` on the committer tier; the key is stored
  against the ASF UID found in its own user IDs

**Associate a key with a committee**:

* Allowed for: Participants of the committee
* Checked via: `associate_fingerprint` on the committee participant tier

**Delete an OpenPGP key**:

* Allowed for: The key's owner (a committer)
* Checked via: `delete_key` on the committer tier, which matches the key by the
  caller's ASF UID
* Constraint: a key managed in a committee's SVN KEYS file (reflect mode) cannot
  be deleted in ATR; it must be removed in SVN

**Import a committee KEYS file**:

* Allowed for: Participants of the committee
* Checked via: `import_keys_file` on the committee participant tier

**Delete all of a committee's keys**:

* Allowed for: ATR administrators only
* Checked via: `delete_committee_keys` on the foundation admin tier

### SSH keys

**Add or delete your own SSH key**:

* Allowed for: Any committer, for their own keys
* Checked via: `add_key` and `delete_key` on the committer tier

**Add a workflow SSH key**:

* Allowed for: Participants of the committee
* Checked via: `add_workflow_key` on the committee participant tier
* Note: workflow keys authenticate automated release workflows rather than a
  person; each is minted once against a single-use OIDC token

**Revoke all of a user's SSH keys**:

* Allowed for: ATR administrators only
* See [Access control for tokens](#access-control-for-tokens), which documents
  this operation alongside token revocation

## Access control for project policy

Each project has a release policy governing how its releases are composed, voted
on, and finished. The policy is changed through a set of edit operations, all on
the release manager tier:

* **Compose policy** (`edit_compose`) - composition and artifact settings.
* **Vote policy** (`edit_vote`) - voting settings.
* **Finish policy** (`edit_finish`) - settings applied when a release is finished.
* **Trusted publishing** (`edit_trusted_publishing`) - trusted publishing
  configuration (see [Trusted Publishing](trusted-publishing)).
* **Version scheme** (`edit_version_scheme`) - the project's version numbering.
* **Cycle dates** (`edit_cycle_dates`) - release cycle dates.

**Allowed for**: Release managers and PMC members, obtained with
`as_project_release_manager`.

**See also**: value and type constraints on these settings - such as which vote
modes a project may use - are validation rather than access control, and are
covered under [Business logic validation](input-validation#business-logic-validation).

## Access control for projects

Project metadata and lifecycle operations span two tiers.

**Edit project metadata and security settings** (`edit_metadata`, `edit_security`):

* Allowed for: Release managers and PMC members
* Obtained with: `as_project_release_manager`

**Create a project** (`create`):

* Allowed for: PMC members
* Obtained with: `as_committee_member`

**Archive or delete a project** (`archive`, `delete`):

* Allowed for: PMC members
* Obtained with: `as_committee_member`
* Constraint: each takes an approval request, tying the action to a recorded
  approval rather than a single click

## Access control for administrators

ATR administrators can act across every committee and project, beyond what any
committee role grants. Administrative rights are not tied to a committee.

### Who is an administrator

Administrator identity is determined by [`is_admin`](/ref/atr/user.py). The
administrator set is drawn from ASF LDAP - the Infrastructure Root and Tooling
service groups (see [Roles and principals](#roles-and-principals)) - fetched by
`fetch_admin_users`, cached, and refreshed periodically. Additional
administrators can be configured through `ADMIN_USERS_ADDITIONAL`.

### Downgrade and impersonation

Administrators have two controls that change how their session acts, both recorded
on the session (see [Sessions](sessions)):

* **Downgrade**: an administrator can drop their own admin rights for the session.
  While the session is downgraded, `is_admin` returns false and the administrator
  is treated as an ordinary user.
* **Browse as**: an administrator can act as another user. The session records the
  real administrator in `admin_uid`, so both the acting identity and the
  administrator behind it are known.

### Administrator-only operations

Administrator rights gate operations that are foundation-wide or destructive,
rather than belonging to a single committee. These include:

* Revoking all of a user's personal access tokens or SSH keys (see
  [Access control for tokens](#access-control-for-tokens)).
* Issuing system tokens for service identities (see
  [Access control for tokens](#access-control-for-tokens)).
* Deleting all of a committee's signing keys (see
  [Access control for keys](#access-control-for-keys)).
* Deleting or cancelling a finished, published release (see
  [Access control for releases](#access-control-for-releases)).
* Foundation-wide catalogue and site administration, such as catalogue rebuilds,
  structural corrections, and the site banner.

The admin dashboard is the definitive surface for these operations; this list
describes the categories rather than every individual action.

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
