# 3.12. Authorization security

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.11.` [Authentication security](security-authentication)

**Next**: `3.13.` [Input validation](input-validation)

**Sections**:

* [Overview](#overview)
* [Roles and principals](#roles-and-principals)
* [LDAP integration](#ldap-integration)
* [Access control for releases](#access-control-for-releases)
* [Access control for tokens](#access-control-for-tokens)
* [Implementation patterns](#implementation-patterns)
* [Caching behavior](#caching-behavior)
* [Implementation references](#implementation-references)

## Overview

ATR uses role-based access control (RBAC) where roles are derived from ASF LDAP group memberships. Authentication (covered in [Authentication security](security-authentication)) establishes *who* a user is; authorization determines *what* they can do.

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

* **Project Participant**: A committer who is a member of a specific project (has commit access). Can start releases, upload artifacts, and cast votes for that project. Determined by the `member` attribute in the project's LDAP group.

* **PMC Member**: A committer who is on the PMC (Project Management Committee) for a specific committee. Has all participant permissions plus can resolve votes, finish releases, configure project settings, and manage signing keys. Determined by the `owner` attribute in the committee's LDAP group.

* **Chair**: A PMC chair. Currently has the same permissions as PMC Member in ATR. Determined by membership in `cn=pmc-chairs,ou=groups,ou=services,dc=apache,dc=org`.

* **ASF Member**: An ASF Member. Currently has the same permissions as a regular committer in ATR, though this may change. Determined by membership in `cn=member,ou=groups,dc=apache,dc=org`.

* **Infrastructure Root**: ASF Infrastructure team with root access. Has administrative capabilities. Determined by membership in `cn=infrastructure-root,ou=groups,ou=services,dc=apache,dc=org`.

* **Tooling Team**: Members of the ASF Tooling team. Treated as PMC members of the "tooling" committee. Determined by membership in `cn=tooling,ou=groups,ou=services,dc=apache,dc=org`.

## LDAP integration

Authorization data is fetched from ASF LDAP using the [`principal`](/ref/atr/principal.py) module. The key LDAP bases are:

* `ou=people,dc=apache,dc=org` - All committers
* `ou=project,ou=groups,dc=apache,dc=org` - Project and committee groups
* `cn=member,ou=groups,dc=apache,dc=org` - ASF Members
* `cn=pmc-chairs,ou=groups,ou=services,dc=apache,dc=org` - PMC Chairs
* `cn=infrastructure-root,ou=groups,ou=services,dc=apache,dc=org` - Infrastructure root
* `cn=tooling,ou=groups,ou=services,dc=apache,dc=org` - Tooling team

The [`Committer`](/ref/atr/principal.py:Committer) class fetches a user's full authorization profile from LDAP, including their committee memberships (PMC membership) and project participations (committer access).

## Access control for releases

Release operations have the following access requirements:

**View release information** (public pages, download links):

* Allowed for: Everyone, including unauthenticated users

**Start a new release**:

* Allowed for: Project participants (committers on the project)
* Checked via: `is_participant_of(project.committee_name)`

**Upload release artifacts**:

* Allowed for: Project participants
* Additional constraint: Must be the user who started the release, or a PMC member

**Cast a vote on a release**:

* Allowed for: Project participants
* Constraint: Cannot vote multiple times; can change existing vote

**Resolve a vote (tally votes and determine outcome)**:

* Allowed for: PMC members only
* Checked via: `is_member_of(project.committee_name)`

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

**Exchange PAT for JWT**:

* Allowed for: Anyone with a valid PAT
* Note: This is an unauthenticated endpoint; the PAT serves as the credential

## Implementation patterns

Authorization checks in ATR follow consistent patterns.

### Checking PMC membership

To verify a user is a PMC member for a committee:

```python
from atr.principal import Authorisation

auth = await Authorisation()
if not auth.is_member_of(committee_name):
    raise Forbidden("PMC membership required")
```

### Checking project participation

To verify a user is a committer on a project:

```python
auth = await Authorisation()
if not auth.is_participant_of(project.committee_name):
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

For web requests, the [`Authorisation`](/ref/atr/principal.py:Authorisation) class reads the session automatically:

```python
auth = await Authorisation()  # Uses ASFQuart session
```

For API requests, the ASF UID is extracted from the JWT and passed explicitly:

```python
auth = await Authorisation(asf_uid)  # Uses LDAP lookup
```

Both paths use the same authorization logic and caching.

## Caching behavior

LDAP queries are expensive, so authorization data is cached in [`principal.Cache`](/ref/atr/principal.py:Cache). The cache stores:

* `member_of` - Set of committees where the user is a PMC member
* `participant_of` - Set of projects where the user is a committer
* `last_refreshed` - Timestamp of last LDAP query

The cache TTL is 300 seconds (`cache_for_at_most_seconds`). When the cache is stale, the next authorization check triggers an LDAP refresh.

The cache is per-user and in-memory. It does not persist across server restarts. If LDAP group memberships change, users may need to wait up to 5 minutes for ATR to reflect the change, or log out and back in.

### Test mode

When `ALLOW_TESTS` is enabled in the configuration, a special "test" user and "test" committee are available. All authenticated users are automatically added to the test committee for testing purposes. This should never be enabled in production.

## Implementation references

* [`principal.py`](/ref/atr/principal.py) - Core authorization classes and LDAP integration
* [`web.py`](/ref/atr/web.py) - Request context and committer access
* [`ldap.py`](/ref/atr/ldap.py) - Low-level LDAP search functionality
