# 3.3. Database

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.2.` [Overview of the code](overview-of-the-code)

**Next**: `3.4.` [Storage interface](storage-interface)

**Sections**:

* [Introduction](#introduction)
* [Core models](#core-models)
* [Other features](#other-features)
* [Cascade deletions](#cascade-deletions)
* [Schema changes and migrations](#schema-changes-and-migrations)

## Introduction

ATR stores all of its data in a SQLite database. The database schema is defined in [`models.sql`](/ref/atr/models/sql.py) using [SQLModel](https://sqlmodel.tiangolo.com/), which uses [Pydantic](https://docs.pydantic.dev/latest/) for data validation and [SQLAlchemy](https://www.sqlalchemy.org/) for database operations. This page explains the main features of the database schema to help you understand how data is structured in ATR.

## Core models

The three most important models in ATR are [`Committee`](/ref/atr/models/sql.py) (Committee), [`Project`](/ref/atr/models/sql.py) (Project), and [`Release`](/ref/atr/models/sql.py) (Release).

A [`Committee`](/ref/atr/models/sql.py) (Committee) represents a PMC or PPMC at the ASF. Each committee has a key, which is the primary key, and an optional display name. Committees can have child committees, which is used for the relationship between the Incubator PMC and individual podling PPMCs. Committees also have lists of committee members and committers stored as JSON arrays.

A [`Project`](/ref/atr/models/sql.py) (Project) belongs to a committee and can have multiple releases. Projects have a key as the primary key, along with metadata such as a description and category and programming language tags. Each project can optionally have a [`ReleasePolicy`](/ref/atr/models/sql.py) (ReleasePolicy) that defines how releases should be handled, including e.g. vote templates and GitHub workflow configuration.

A [`Release`](/ref/atr/models/sql.py) (Release) belongs to a project and represents a specific version of software which is voted on by a committee. The primary key is a key derived from the project key and version. Releases have a phase that indicates their current state in the release process, from draft composition to final publication. Each release can have multiple [`Revision`](/ref/atr/models/sql.py) (Revision) instances before final publication, representing iterations of the underlying files.

## Other features

The models themselves are the most important components, but to support those models we need other components such as enumerations, column types, automatically populated fields, computed properties, and constraints.

### Enumerations

ATR uses Python enumerations to ensure that certain fields only contain valid values. The most important enumeration is [`ReleasePhase`](/ref/atr/models/sql.py) (ReleasePhase), which defines the four phases of a release: `RELEASE_CANDIDATE_DRAFT` for composing, `RELEASE_CANDIDATE` for voting, `RELEASE_PREVIEW` for finishing, and `RELEASE` for completed releases.

The [`TaskStatus`](/ref/atr/models/sql.py) (TaskStatus) enumeration defines the states a task can be in: `QUEUED`, `ACTIVE`, `COMPLETED`, or `FAILED`. The [`TaskType`](/ref/atr/models/sql.py) (TaskType) enumeration lists all the different types of background tasks that ATR can execute, from signature checks to SBOM generation.

The [`DistributionPlatform`](/ref/atr/models/sql.py) (DistributionPlatform) enumeration is more complex, as each value contains not just a name but a [`DistributionPlatformValue`](/ref/atr/models/sql.py) (DistributionPlatformValue) with template URLs and configuration for different package distribution platforms like PyPI, npm, and Maven Central.

### Special column types

SQLite does not support all the data types we need, so we use SQLAlchemy type decorators to handle conversions. The [`UTCDateTime`](/ref/atr/models/sql.py) (UTCDateTime) type ensures that all datetime values are stored in UTC and returned as timezone-aware datetime objects. When Python code provides a datetime with timezone information, the type decorator converts it to UTC before storing. When reading from the database, it adds back the UTC timezone information.

The [`ResultsJSON`](/ref/atr/models/sql.py) (ResultsJSON) type handles storing task results. It automatically serializes Pydantic models to JSON when writing to the database, and deserializes them back to the appropriate result model when reading.

### Automatic field population

Some fields are populated automatically using SQLAlchemy event listeners. When a new [`Revision`](/ref/atr/models/sql.py) (Revision) is created, the [`populate_revision_sequence_and_name`](/ref/atr/models/sql.py) (populate_revision_sequence_and_name) function runs before the database insert. This function queries for the highest existing sequence number for the release, increments it, and sets both the `seq` and `number` fields. It also constructs the revision name by combining the release name with the revision number.

The [`check_release_key`](/ref/atr/models/sql.py) (check_release_key) function runs before inserting a release. If the release name is empty, it automatically generates it from the project name and version using the [`release_key`](/ref/atr/models/sql.py) (release_key) helper function.

### Computed properties

Some properties are computed dynamically rather than stored in the database. The `Release.latest_revision_number` property is implemented as a SQLAlchemy column property using a correlated subquery. This means that when you access `release.latest_revision_number`, SQLAlchemy automatically executes a query to find the highest revision number for that release. The query is defined once in [`RELEASE_LATEST_REVISION_NUMBER`](/ref/atr/models/sql.py) (RELEASE_LATEST_REVISION_NUMBER) and attached to the `Release` class.

Projects have many computed properties that provide access to release policy settings with appropriate defaults. For example, `Project.policy_start_vote_template` returns the custom vote template if one is configured, or falls back to the default vote template if not. This pattern allows projects to customize their release process while providing sensible defaults.

### Constraints and validation

Database constraints ensure data integrity. The [`Task`](/ref/atr/models/sql.py) (Task) model includes a check constraint that validates the status transitions. A task must start in `QUEUED` state, can only transition to `ACTIVE` when `started` and `pid` are set, and can only reach `COMPLETED` or `FAILED` when the `completed` timestamp is set. These constraints prevent invalid state transitions at the database level.

Unique constraints ensure that certain combinations of fields are unique. The `Release` model has a unique constraint on `(project_key, version)` to prevent creating duplicate releases for the same project version. The `Revision` model has two unique constraints: one on `(release_key, seq)` and another on `(release_key, number)`, ensuring that revision numbers are unique within a release.

## Cascade deletions

ATR uses two kinds of cascade deletions: ORM-level cascades managed by SQLAlchemy, and DB-level cascades enforced by SQLite foreign key constraints. Both are configured in [`models/sql.py`](/ref/atr/models/sql.py). This section documents what happens when a parent record is deleted, which child records are automatically removed, and which deletions are blocked by foreign key constraints.

SQLite foreign key enforcement is enabled in ATR via `PRAGMA foreign_keys=ON` in [`db/__init__.py`](/ref/atr/db/__init__.py). This means DB-level cascades and foreign key constraints are active.

### ORM-level vs DB-level cascades

ORM cascades are configured on SQLAlchemy relationships using `sa_relationship_kwargs={"cascade": "all, delete-orphan"}`. When a parent object is deleted through the ORM (via `session.delete(parent)`), SQLAlchemy automatically deletes all related child objects before issuing the SQL DELETE for the parent. These cascades only apply when using the ORM; they do not apply to raw SQL DELETE statements or bulk `sqlmodel.delete()` queries.

DB-level cascades are configured on foreign key columns using `ondelete="CASCADE"` or `ondelete="SET NULL"`. These are enforced by SQLite itself, regardless of whether the deletion happens through the ORM or raw SQL. When a parent row is deleted, SQLite automatically deletes (or nullifies) child rows that reference it.

Some relationships have both ORM and DB cascades configured. This provides defense in depth: the ORM cascade handles the common case of deleting through the ORM, while the DB cascade provides a safety net for bulk SQL deletions, migrations, or manual database operations.

### Entity relationship overview

The following diagram shows the main entity relationships and their cascade behaviour.

```mermaid
erDiagram
    Committee ||--o{ Project : "has projects"
    Committee ||--o{ Committee : "has children"
    Committee }o--o{ PublicSigningKey : "linked via KeyLink"
    Project ||--o{ Release : "has releases"
    Project ||--o| ReleasePolicy : "ORM cascade + reverse DB cascade"
    Release ||--o{ Revision : "ORM cascade only"
    Release ||--o{ CheckResult : "ORM + DB cascade"
    Release ||--o{ Distribution : "ORM + DB cascade"
    Release ||--o{ Quarantined : "DB cascade only"
    Release ||--o| ReleasePolicy : "ORM cascade only"
    Revision ||--o| Revision : "parent chain"
    Task ||--o| WorkflowStatus : "DB SET NULL"
    Task }o--o| Project : "references"
```

### Release deletion cascades

Deleting a Release triggers the most cascades in the system.

```mermaid
flowchart TD
    A["Delete Release"] --> B["ORM cascade"]
    A --> C["DB cascade"]

    B --> B1["Revisions deleted"]
    B --> B2["CheckResults deleted"]
    B --> B3["Distributions deleted"]
    B --> B4["ReleasePolicy deleted"]

    C --> C1["CheckResults deleted"]
    C --> C2["Distributions deleted"]
    C --> C3["Quarantined records deleted"]

    style B fill:#e8f4fd
    style C fill:#fde8e8
```

When a Release is deleted through the ORM (`session.delete(release)`):

**Via ORM cascade** (`cascade: "all, delete-orphan"`):

* All **Revision** records belonging to the release are deleted. Revisions have no DB-level cascade — they are only cleaned up by the ORM or by explicit bulk deletion.
* All **CheckResult** records belonging to the release are deleted.
* All **Distribution** records belonging to the release are deleted.
* The release's **ReleasePolicy** (if any) is deleted. This is a one-to-one relationship with `cascade_delete=True` and `single_parent=True`.

**Via DB cascade** (`ondelete="CASCADE"` on the foreign key):

* All **CheckResult** records referencing the release are deleted. This is redundant with the ORM cascade when using ORM deletion, but provides coverage for raw SQL deletions.
* All **Distribution** records referencing the release are deleted. Same redundancy as above.
* All **Quarantined** records referencing the release are deleted. This has a DB-level cascade only (no ORM cascade), because Quarantined records are not part of the Release's ORM relationships with cascade configuration.

### Project deletion cascades

When a Project is deleted through the ORM:

**Via ORM cascade**: The project's **ReleasePolicy** (if any) is deleted. This uses `cascade_delete=True` with `cascade: "all, delete-orphan"` and `single_parent=True`.

**Note on `ondelete="CASCADE"` on `Project.release_policy_id`:** This foreign key has `ondelete="CASCADE"`, but the direction is from ReleasePolicy to Project — meaning if a ReleasePolicy row were deleted directly in the database, the *Project* referencing it would also be deleted. This is the reverse of the ORM cascade direction (where deleting a Project cascades to its ReleasePolicy). In practice, ReleasePolicy is never deleted independently; it is always deleted as a consequence of its parent Project or Release being deleted via the ORM. The `ondelete="CASCADE"` here prevents a dangling foreign key if a ReleasePolicy were removed manually, but the consequence (deleting the Project) would be severe and unintended. Release does not have this issue because `Release.release_policy_id` has no `ondelete` configured.

Project deletion is explicitly guarded in the application: [`storage/writers/project.py`](/ref/atr/storage/writers/project.py) raises an error if the project has any associated releases. This means in practice, the Release cascades described above are not triggered indirectly through Project deletion.

### SET NULL behaviour

One relationship uses `ondelete="SET NULL"` instead of CASCADE:

* **WorkflowStatus.task_id** → Task.id: When a Task is deleted, the `task_id` column in any referencing WorkflowStatus rows is set to NULL rather than deleting the WorkflowStatus record. This preserves workflow status history even when the associated task is cleaned up.

### Explicit deletions

Some related records are deleted explicitly in application code rather than through cascades.

**When deleting a Release** ([`storage/writers/release.py`](/ref/atr/storage/writers/release.py)): **Task** records matching the release's project name and version are deleted with a bulk SQL DELETE before the Release itself is deleted. Tasks reference `project.key` via a nullable foreign key with no cascade, so they would not be automatically cleaned up. **RevisionCounter** records are deleted only in test mode (when `ALLOW_TESTS` is enabled and the release belongs to the test committee), to allow revision number reuse in tests.

**When announcing a Release** ([`storage/writers/announce.py`](/ref/atr/storage/writers/announce.py)): All **Revision** records for the release are deleted with a bulk SQL DELETE during the announce process, as part of cleaning up draft history after a release is published.

**When deleting a PublicSigningKey** ([`storage/writers/keys.py`](/ref/atr/storage/writers/keys.py)): **KeyLink** records associating the key with committees are explicitly cleared and deleted before the key itself is deleted. KeyLink has no cascade configuration, so this cleanup is mandatory.

### Blocked deletions

Several entities cannot be deleted while other records reference them, because the foreign key constraints have no cascade and the referencing column is non-nullable. Attempting to delete these will raise a database integrity error.

**Committee** cannot be deleted while any **KeyLink** rows reference it (KeyLink.committee_key is a non-nullable primary key component). Although Project.committee_key references Committee, it is nullable, so it does not block deletion at the DB level.

**Project** cannot be deleted while any **Release** rows reference it (Release.project_key is non-nullable, no cascade) or any **CheckResultIgnore** rows reference it (CheckResultIgnore.project_key is non-nullable, no cascade). The application also explicitly prevents Project deletion when releases exist.

**Revision** cannot be deleted individually while another **Revision** references it as a parent (Revision.parent_key is nullable but has no `ondelete`, so deletion of a parent revision that is still referenced would violate the FK constraint). In practice, revisions are deleted in bulk per-release which avoids this issue since all revisions in the chain are removed together.

### Cascade coverage summary

```mermaid
flowchart LR
    subgraph both ["Both ORM and DB cascade"]
        CR["Release → CheckResult"]
        DI["Release → Distribution"]
    end
    subgraph orm_only ["ORM cascade only"]
        RV["Release → Revision"]
        RP["Release → ReleasePolicy"]
        PP["Project → ReleasePolicy"]
    end
    subgraph db_only ["DB cascade only"]
        QR["Release → Quarantined"]
    end
    subgraph reverse ["Reverse DB cascade"]
        RPP["ReleasePolicy → Project"]
    end

    style both fill:#d4edda
    style orm_only fill:#fff3cd
    style db_only fill:#f8d7da
    style reverse fill:#e2d5f1
```

| Parent | Child | ORM cascade | DB cascade | Explicit deletion |
| ------ | ----- | ----------- | ---------- | ----------------- |
| Release | Revision | `all, delete-orphan` | — | Bulk delete during announce |
| Release | CheckResult | `all, delete-orphan` | `CASCADE` | — |
| Release | Distribution | `all, delete-orphan` | `CASCADE` | — |
| Release | Quarantined | — | `CASCADE` | — |
| Release | ReleasePolicy | `all, delete-orphan` | — | — |
| Project | ReleasePolicy | `all, delete-orphan` | — | — |
| ReleasePolicy | Project | — | `CASCADE` ⚠️ | — |
| Task | WorkflowStatus | — | `SET NULL` | — |
| Release | Task | — | — | Bulk delete before release deletion |
| PublicSigningKey | KeyLink | — | — | Explicit clear before key deletion |

The ⚠️ on **ReleasePolicy → Project** indicates a reverse cascade: if a ReleasePolicy were deleted directly at the DB level, the referencing Project would also be deleted. This is a consequence of `ondelete="CASCADE"` on `Project.release_policy_id`.

## Schema changes and migrations

We often have to make changes to the database model in ATR, whether that be to add a whole new model or just to rename or change some existing properties. No matter the change, this involves creating a database migration. We use Alembic to perform migrations, and this allows migrations to be *bidirectional*: we can downgrade as well as upgrade. This can be very helpful when, for example, a migration didn't apply properly or is no longer needed due to having found a different solution.

To change the database, do not edit the SQLite directly. Instead, change the model file in [`atr/models/sql.py`](/ref/atr/models/sql.py). If you're running ATR locally, you should see from its logs that the server is now broken due to having a mismatching database. That's fine! This is the point where you now create the migration. To do so, run:

```shell
uv run --frozen alembic revision -m "Description of changes" --autogenerate
```

Obviously, change `"Description of changes"` to an actual description of the changes that you made. Keep it short, around 50-60 characters. Then when you restart the server you should find that the migration is automatically applied. You should be careful, however, before restarting the server. Not all migrations apply successfully when autogenerated. Always review the automatically produced migrations in `migrations/versions` first, and ensure that they are correct before proceeding. One common problem is that the autogenerator leaves out server defaults. Please note that you do not need to include changes to enums in Alembic migrations, because they are not enforced in the SQLite schema.

It can be helpful to make a backup of the entire SQLite database before performing the migration, especially if the migration is particularly complex. This can help if, for example, the downgrade is broken, otherwise you may find yourself in a state from which there is no easy recovery. *Always* ensure that migrations are working locally before pushing them to GitHub, because we apply changes from GitHub directly to our deployed containers. Note that sometimes the deployed containers contain data that causes an error that was not caught locally. In that case there is usually no option but to provide special triage on the deployed containers.

If testing a migration in a PR, be sure to stop the server and run `uv run --frozen alembic downgrade -1` before switching back to any branch not containing the migration.
