# 8. Admin dashboards

**Up**: [Documentation](.)

**Prev**: `7.23.` [Application logs](application-logs)

**Next**: `9.` [Glossary](glossary)

**Sections**:

* [Access](#access)
* [Menu](#menu)
* [How the pages are drawn](#how-the-pages-are-drawn)
* [Users](#users)
* [System](#system)
* [Catalog](#catalog)
* [Database](#database)

## Access

* The Admin menu is shown only when `is_viewing_as_admin_fn(current_user.uid)` is true. Other users see a **User** menu with just **Permissions**.
* Every `/admin/...` route is also protected on the server by [`_check_admin_access`](/ref/atr/blueprints/admin.py:_check_admin_access). An unauthenticated request gets `401 Not authenticated`. An authenticated non-admin gets `403 You are not authorized to access the admin interface`. The check uses [`is_admin`](/ref/atr/user.py:is_admin).
* Administrators come from the Infrastructure Root and Tooling service LDAP groups, plus `ADMIN_USERS_ADDITIONAL` (see [Authorization security](https://releases.apache.org/docs/authorization-security)).
* **Toggle admin view** switches your session between admin and regular-user views. While viewing as a regular user, the Admin menu is hidden.

## Menu

| Menu item | Route | Handler | Notes |
| --- | --- | --- | --- |
| Permissions | `/user/permissions` | | Also shown to non-admins, in the User menu |
| **Users** | `/admin/users` | [`users_get`](/ref/atr/admin/__init__.py:users_get) | Dashboard 1 |
| **System** | `/admin/system` | [`system_get`](/ref/atr/admin/__init__.py:system_get) | Dashboard 2 |
| **Catalog** | `/admin/catalog` | [`catalog_get`](/ref/atr/admin/__init__.py:catalog_get) | Dashboard 3 |
| **Database** | `/admin/data` | [`data`](/ref/atr/admin/__init__.py:data) | Dashboard 4 |
| Toggle admin view | `/admin/toggle-view` | [`toggle_view_get`](/ref/atr/admin/__init__.py:toggle_view_get) | Action (POST), not a dashboard |
| Validate JWT | `/admin/validate-jwt` | [`validate_jwt_get`](/ref/atr/admin/__init__.py:validate_jwt_get) | Shown only in test mode |

Some admin routes are not in the menu, such as `/admin/ongoing-tasks` ([`ongoing_tasks_get`](/ref/atr/admin/__init__.py:ongoing_tasks_get)) and `/admin/raise-error` ([`raise_error`](/ref/atr/admin/__init__.py:raise_error)).

## How the pages are drawn

Admin routes use the [`typed`](/ref/atr/blueprints/admin.py:typed) decorator, which builds the URL from the function’s type annotations. A `Literal["users"]` parameter becomes the path segment `users`, and the blueprint prefix is `/admin`. A function with a form or body parameter is a POST handler. Otherwise it is a GET handler.

Each dashboard has a GET handler that draws the page and a matching POST handler that processes its forms. The page is a set of tabs chosen by the `tab` query parameter, for example `/admin/users?tab=sessions`. All four render through the `admin-blank.html` template.

| Dashboard | GET handler | Page builder | POST handler |
| --- | --- | --- | --- |
| Users | [`users_get`](/ref/atr/admin/__init__.py:users_get) | [`_users_page`](/ref/atr/admin/__init__.py:_users_page) | [`users_post`](/ref/atr/admin/__init__.py:users_post) |
| System | [`system_get`](/ref/atr/admin/__init__.py:system_get) | [`_system_page`](/ref/atr/admin/__init__.py:_system_page) | [`system_post`](/ref/atr/admin/__init__.py:system_post) |
| Catalog | [`catalog_get`](/ref/atr/admin/__init__.py:catalog_get) | [`_catalog_page`](/ref/atr/admin/__init__.py:_catalog_page) | [`catalog_post`](/ref/atr/admin/__init__.py:catalog_post) |
| Database | [`data`](/ref/atr/admin/__init__.py:data) | [`_database_page`](/ref/atr/admin/__init__.py:_database_page) | [`data_post`](/ref/atr/admin/__init__.py:data_post) |

## Users

**Route:** `GET /admin/users` (`?tab=browse-as` by default). Form submissions go to `POST /admin/users`.

| Tab | `tab=` | Purpose |
| --- | --- | --- |
| Browse as user | `browse-as` | Act as another user. Your real identity is kept on the session as `admin_uid`. |
| LDAP lookup | `ldap` | Look up a user in ASF LDAP. |
| Sessions | `sessions` | Every recorded session, most recently active first. Paginated. |
| Revoke tokens | `revoke-tokens` | Revoke one user’s tokens. **Also revokes system tokens the user created.** See below. |
| Revoke all user PATs | `revoke-all-tokens` | Revoke every user’s PATs in one action. Does not revoke system tokens. Afterwards a notice lets you email the affected users, change the site banner, or do nothing. |
| Revoke SSH keys | `revoke-ssh-keys` | Revoke a user’s SSH keys. |
| System tokens | `system-tokens` | Create and revoke system tokens for service identities, and edit their IP restrictions. |
| Rotate JWT key | `rotate-jwt` | Rotate the JWT signing key immediately. This invalidates all currently usable JWTs. |
| Embargo denials | `embargo-denials` | Recent embargo access denials, newest first. Each row shows the time, release key, requesting user (or Anonymous), any admin acting as that user, source IP and request ID. |

### Revoke tokens and system tokens

The Revoke tokens tab calls [`_users_revoke_tokens`](/ref/atr/admin/__init__.py:_users_revoke_tokens), which calls [`revoke_all_user_tokens`](/ref/atr/storage/writers/tokens.py:revoke_all_user_tokens). That method deletes every personal access token where the target user is either:

* the **owner** (`asfuid`), meaning their own user PATs, or
* the **creator** (`created_by`), meaning any system PATs that user minted on the System tokens tab.

A system token has no owning user, so `created_by` is the only link between it and the administrator who created it. Revoking that administrator’s tokens therefore deletes the service’s token too, and the service can no longer exchange it for a JWT.

What an administrator sees and what happens:

* The tab ([`_users_revoke_tokens_tab`](/ref/atr/admin/__init__.py:_users_revoke_tokens_tab), template [`revoke-user-tokens.html`](/ref/atr/admin/templates/revoke-user-tokens.html)) lists each user with columns **User PATs owned**, **System PATs owned** and **Total**. “System PATs owned” counts the system tokens that user *created*. The form’s submit button reads “Revoke all tokens”, and the tab’s text says only that it revokes all PATs for a user account.
* The success message reports a single count, for example “Revoked 3 tokens for alice”, which does not separate the two kinds.
* The user is emailed “ATR - Security alert: API tokens revoked by administrator”. The count in that email includes the system tokens.
* The action is written to the audit log with the target user and the total count.

By contrast, [`revoke_all_users_tokens`](/ref/atr/storage/writers/tokens.py:revoke_all_users_tokens) (the Revoke all user PATs tab) deletes only tokens that are not system tokens.

**Open question:** whether revoking a user’s tokens should keep removing the system tokens they created. Until that is decided, revoke individual system tokens from the System tokens tab if you want to leave a service running, and check the “System PATs owned” column before revoking an administrator’s tokens.

## System

**Route:** `GET /admin/system`. Form submissions go to `POST /admin/system`.

| Tab | `tab=` | Purpose |
| --- | --- | --- |
| Site banner | `banner` | Set the banner shown at the top of every page. Content is Markdown and raw HTML is stripped. A banner can be edited or restored. |
| Configuration | `configuration` | Shows the server configuration. |
| Test message | `test-message` | Send a test message. |
| Performance | `performance` | Per-route timing statistics from the performance log (count, mean, median, min, max, standard deviation). |
| Recent tasks | `tasks` | Tasks from the last N minutes, with links to change the window. |

The System POST handler also handles a presentations vote bypass form.

## Catalog

**Route:** `GET /admin/catalog` (`?tab=update-committees` by default). The Current releases tab also accepts `?q=<text>` to filter by release key. Form submissions go to `POST /admin/catalog`.

| Tab | `tab=` | Purpose |
| --- | --- | --- |
| Update | `update-committees` | Update PMC and podling information from Whimsy and LDAP. It runs daily on its own, so use it to bring the next run forward. It covers PMC member lists, release manager assignments, podling status, committee and podling retirements, and mailing list addresses. (Previously `update-projects`, “Update projects”.) |
| Current releases | `releases` | Releases that have at least one workflow revision. Paginated, and filterable with `?q=`. Individual releases can be deleted from here. |
| Catalog admin | `admin` | Lists committees, with an A–Z letter bar to jump to a letter. Each committee links to `/admin/catalog/<committee_key>` to review and correct its catalogued projects. |
| Dist watcher rules | `dist-rules` | List, add, enable or disable, and delete distribution watcher rules. |
| Reproducible builds | `reproducible-builds` | Automated release signing keys, grouped by committee. |
| Rebuild catalog | `rebuild` | Queue a full site regeneration, or one for a single PMC chosen from a list, and show its status. Catalogue changes refresh affected pages on their own, so a rebuild is rarely needed. |

### Per-committee and per-project pages

Under the Catalog admin tab, these pages are also drawn by `@admin.typed` handlers:

| Route | Handler | Purpose |
| --- | --- | --- |
| `/admin/catalog/<committee_key>` | [`catalog_committee_get`](/ref/atr/admin/__init__.py:catalog_committee_get) | A committee’s catalogued projects, each with an **Archive** or **Restore** button. Archiving marks a live project dormant (its PMC can unarchive it), or retired when its committee is dissolved or is the Attic. Restore returns an archived project to active, even when its committee is in the Attic. A standing project gets neither button. |
| `/admin/catalog/<committee_key>/project/<project_key>` | [`catalog_project_get`](/ref/atr/admin/__init__.py:catalog_project_get) | A project’s releases, with options to move catalogued releases elsewhere. |
| `/admin/catalog/<committee_key>/project/<project_key>/release/<version_key>/date` | [`catalog_release_date_get`](/ref/atr/admin/__init__.py:catalog_release_date_get) | **Set date**: correct a catalogued release’s date. Only released releases have a date. The form takes a release date, taken as UTC. |

## Database

**Route:** `GET /admin/data` (`?tab=data&model=Committee` by default). Form submissions go to `POST /admin/data`.

| Tab | `tab=` | Purpose |
| --- | --- | --- |
| Data browser | `data` | Browse database tables. The `model` parameter selects the table. Paginated. |
| Consistency | `consistency` | Compares release directories recorded in the database with those on disk. |
| Validation | `validation` | Runs validation across the database and lists divergences. |
| Keys check | `keys-check` | Checks stored keys. |
| Update keys | `update-keys` | Re-imports keys from remote KEYS files by starting a background process. **Disabled on instances where ATR publishes to the release area**, because it would overwrite every committee’s own KEYS file there. |
