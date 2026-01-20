# 3.4. Storage interface

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.3.` [Database](database)

**Next**: `3.5.` [User interface](user-interface)

**Sections**:

* [Introduction](#introduction)
* [How do we read from storage?](#how-do-we-read-from-storage)
* [How do we write to storage?](#how-do-we-write-to-storage)
* [How do we add new storage functionality?](#how-do-we-add-new-storage-functionality)
* [How do we use outcomes?](#how-do-we-use-outcomes)
* [What about audit logging?](#what-about-audit-logging)
* [How is the filesystem organized?](#how-is-the-filesystem-organized)
* [How should the filesystem be backed up?](#how-should-the-filesystem-be-backed-up)

## Introduction

All database writes, and some reads, in ATR go through the [`storage`](/ref/atr/storage/__init__.py) interface. This interface **enforces permissions**, **centralizes audit logging**, and **provides type-safe access** to the database. In other words, avoid calling [`db`](/ref/atr/db/__init__.py) directly in route handlers if possible.

The storage interface recognizes several permission levels: general public (unauthenticated visitors), foundation committer (any ASF account), committee participant (committers and PMC members), committee member (PMC members only), and foundation admin (infrastructure administrators). Each level inherits from the previous one, so for example committee members can do everything committee participants can do, plus additional operations.

The storage interface does not make it impossible to bypass authorization, because you can always import `db` directly and write to the database. But it makes bypassing authorization an explicit choice that requires deliberate action, and it makes the safer path the easier path. This is a pragmatic approach to security: we cannot prevent all mistakes, but we can make it harder to make them accidentally.

## How do we read from storage?

Reading from storage is a work in progress. There are some existing methods, but most of the functionality is currently in `db` or `db.interaction`, and much work is required to migrate this to the storage interface. We have given this less priority because reads are generally safe, with the exception of a few components such as user tokens, which should be given greater migration priority.

## How do we write to storage?

To write to storage we open a write session, request specific permissions, use the exposed functionality, and then handle the outcome. Here is an actual example from [`post/start.py`](/ref/atr/post/start.py):

```python
async with storage.write(session) as write:
    wacp = await write.as_project_committee_participant(project_name)
    new_release, _project = await wacp.release.start(project_name, version)
```

The `wacp` object, short for `w`rite `a`s `c`ommittee `p`articipant, provides access to domain-specific writers: `announce`, `checks`, `distributions`, `keys`, `policy`, `project`, `release`, `sbom`, `ssh`, `tokens`, and `vote`.

The write session takes an optional [`Committer`](/ref/atr/web.py:Committer) or ASF UID, typically `session.uid` from the logged-in user. If you omit the UID, the session determines it automatically from the current request context. The write object checks LDAP memberships and raises [`storage.AccessError`](/ref/atr/storage/__init__.py:AccessError) if the user is not authorized for the requested permission level.

Because projects belong to committees, we provide [`write.as_project_committee_member(project_name)`](/ref/atr/storage/__init__.py:as_project_committee_member) and [`write.as_project_committee_participant(project_name)`](/ref/atr/storage/__init__.py:as_project_committee_participant), which look up the project's committee and authenticate the user as a member or participant of that committee. This is convenient when, for example, the URL provides a project name.

Here is a more complete example from [`api/__init__.py`](/ref/atr/api/__init__.py) that shows the classic three step pattern:

```python
async with storage.write(asf_uid) as write:
    # 1. Request permissions
    wafc = write.as_foundation_committer()

    # 2. Use the exposed functionality
    outcome = await wafc.keys.ensure_stored_one(data.key)

    # 3. Handle the outcome
    key = outcome.result_or_raise()
```

In this case we decide to raise as soon as there is any error. We could also choose to display a warning, ignore the error, collect multiple outcomes for batch processing, or handle it in any other way appropriate for the situation.

## How do we add new storage functionality?

Add methods to classes in the [`storage/writers`](/ref/atr/storage/writers/) or [`storage/readers`](/ref/atr/storage/readers/) directories. Code to perform any action associated with public keys that involves writing to storage, for example, goes in [`storage/writers/keys.py`](/ref/atr/storage/writers/keys.py).

Classes in writer and reader modules must be named to match the permission hierarchy:

```python
class GeneralPublic:
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsGeneralPublic,
        data: db.Session,
    ) -> None:
        self.__write = write
        self.__write_as = write_as
        self.__data = data

class FoundationCommitter(GeneralPublic):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsFoundationCommitter,
        data: db.Session
    ) -> None:
        super().__init__(write, write_as, data)
        self.__write = write
        self.__write_as = write_as
        self.__data = data

class CommitteeParticipant(FoundationCommitter):
    def __init__(
        self,
        write: storage.Write,
        write_as: storage.WriteAsCommitteeParticipant,
        data: db.Session,
        committee_name: str,
    ) -> None:
        super().__init__(write, write_as, data)
        self.__committee_name = committee_name

class CommitteeMember(CommitteeParticipant):
    ...
```

This hierarchy that this creates is: `GeneralPublic` → `FoundationCommitter` → `CommitteeParticipant` → `CommitteeMember`. You can add methods at any level. A method on `CommitteeMember` is only available to committee members, while a method on `FoundationCommitter` is available to everyone who has logged in.

Use `__private_methods` for helper code that is not part of the public interface. Use `public_methods` for operations that should be available to callers at the appropriate permission level. Consider returning [`Outcome`](/ref/atr/storage/outcome.py:Outcome) types to allow callers flexibility in error handling. Refer to the [section on using outcomes](#how-do-we-use-outcomes) for more details.

After adding a new writer module, register it in the appropriate `WriteAs*` classes in [`storage/__init__.py`](/ref/atr/storage/__init__.py). For example, when adding the `distributions` writer, it was necessary to add `self.distributions = writers.distributions.CommitteeMember(write, self, data, committee_name)` to the [`WriteAsCommitteeMember`](/ref/atr/storage/__init__.py:WriteAsCommitteeMember) class.

## How do we use outcomes?

Consider using **outcome types** from [`storage.outcome`](/ref/atr/storage/outcome.py) when returning results from writer methods. Outcomes let you represent both success and failure without raising exceptions, which gives callers flexibility in how they handle errors.

An [`Outcome[T]`](/ref/atr/storage/outcome.py:Outcome) is either a [`Result[T]`](/ref/atr/storage/outcome.py:Result) wrapping a successful value, or an [`Error[T]`](/ref/atr/storage/outcome.py:Error) wrapping an exception. You can check which it is with the `ok` property or pattern matching, extract the value with `result_or_raise()`, or extract the error with `error_or_raise()`.

Here is an example from [`post/keys.py`](/ref/atr/post/keys.py) that processes multiple keys and collects outcomes:

```python
async with storage.write() as write:
    wacm = write.as_committee_member(selected_committee)
    outcomes = await wacm.keys.ensure_associated(keys_text)

success_count = outcomes.result_count
error_count = outcomes.error_count
```

The `ensure_associated` method returns an [`outcome.List`](/ref/atr/storage/outcome.py:List), which is a collection of outcomes. Some keys might import successfully, and others might fail because they are malformed or already exist. The caller can inspect the list to see how many succeeded and how many failed, and present that information to the user.

The `outcome.List` class provides many useful methods: [`results()`](/ref/atr/storage/outcome.py:results) to get only the successful values, [`errors()`](/ref/atr/storage/outcome.py:errors) to get only the exceptions, [`result_count`](/ref/atr/storage/outcome.py:result_count) and [`error_count`](/ref/atr/storage/outcome.py:error_count) to count them, and [`results_or_raise()`](/ref/atr/storage/outcome.py:results_or_raise) to extract all values or raise on the first error.

Use outcomes when an operation might fail for some items but succeed for others, or when you want to give the caller control over error handling. Do not use them when failure should always raise an exception, such as authorization failures or database connection errors. Those should be raised immediately.

## What about audit logging?

Storage write operations can be logged to [`config.AppConfig.STORAGE_AUDIT_LOG_FILE`](/ref/atr/config.py:STORAGE_AUDIT_LOG_FILE), which is `state/storage-audit.log` by default. Each log entry is a JSON object containing the timestamp, the action name, and relevant parameters. When you write a storage method that should be audited, call `self.__write_as.append_to_audit_log(**kwargs)` with whatever parameters are relevant to that specific operation. The action name is extracted automatically from the call stack using [`log.caller_name()`](/ref/atr/log.py:caller_name), so if the method is called [`i_am_a_teapot`](https://datatracker.ietf.org/doc/html/rfc2324), the audit log will show `i_am_a_teapot` without you having to pass the name explicitly.

Audit logging must be done manually because the values to log are often those computed during method execution, not just those passed as arguments which could be logged automatically. When deleting a release, for example, we log `asf_uid` (instance attribute), `project_name` (argument), and `version` (argument), but when issuing a JWT from a PAT, we log `asf_uid` (instance attribute) and `pat_hash` (_computed_). Each operation logs what makes sense for that operation.

## How is the filesystem organized?

The storage interface writes to the [database](database) and the filesystem. There is one shared state directory for all of ATR, configured by the `STATE_DIR` parameter in [atr/config.py]. By default this is `$PROJECT_ROOT/state`, where `PROJECT_ROOT` is another ATR configuration parameter.

Only a small number of subdirectories of the state directory are written to by the storage interface, and many of these locations are also configurable. These directories, and their configuration variables, are:

* `attestable`, configured by `ATTESTABLE_STORAGE_DIR`
* `downloads`, configured by `DOWNLOADS_STORAGE_DIR`
* `finished`, configured by `FINISHED_STORAGE_DIR`
* `subversion`, configured by `SVN_STORAGE_DIR`
* `temporary`, which is unconfigurable
* `unfinished`, configured by `UNFINISHED_STORAGE_DIR`

And the purposes of these directories is as follows. Note that "immutable" here means that existing files cannot be modified, but does not preclude new files from being added.

* `attestable` [**immutable**] holds JSON files of data that ATR has automatically verified and which must now be held immutably. (We could store this data in the database, but the aim is to eventually write attestation files here, so this prepares for that approach.)
* `downloads` [**mutable**] are hard links to released artifacts in the `finished` directory. The `finished` directory contains the files exactly as they were arranged by the release managers upon announcing the release, separated strictly into one directory per release. The `downloads` folder, on the other hand, has no restrictions on its organisation and can be rearranged.
* `finished` [**immutable**, except for moving to external archive] contains, as mentioned above, all of the files of a release as they were when announced. This therefore constitutes an historical record and allows us to rewrite the hard links in the `downloads` directory without having to consider not accidentally deleting files by removing all references, etc.
* `subversion` [**mutable**] is designed to mirror two subdirectories, `dev` and `release`, of `https://dist.apache.org/repos/dist`. This is currently unused.
* `temporary` [**mutable**] holds temporary files during operations where the data cannot be modified in place. One important example is when creating a staging directory of a new revision. A subdirectory with a random name is made in this directory, and then the files in the prior version are hard linked into it. The modifications take place in this staging area before the directory is finalised and moved to `unfinished`.
* `unfinished` [**immutable**, except for moving to `finished`] contains all of the files in a release before it is announced. In other words, when the release managers compose a release, when the committee votes on the release, and when the release has been voted on but not yet announced, the files for that release are in this directory.

This list does not include any configuration files, logs, or log directories.

## How should the filesystem be backed up?

Only the `attestable`, `downloads`, `finished`, and `unfinished` directories need to be backed up. The `subversion` directory is unused, and the `temporary` directory is for temporary staging.

The structure of the directories that need backing up is as follows. An ellipsis, `...`, means any number of further files or subdirectories containing subdirectories or files recursively.

* `attestable/PROJECT/VERSION/REVISION.json`
* `downloads/COMMITTEE/PATH/...`
* `finished/PROJECT/VERSION/...`
* `unfinished/PROJECT/VERSION/REVISION/`

Because of the versioning scheme used for the `attestable`, `finished`, and `unfinished` directories, these can be incrementally updated by simple copying without deletion. The downloads directory, however, must be snapshotted as its organization is random.

This list does not include any configuration files, logs, or log directories. All configuration files and the audit logs, at a minimum, should also be backed up.
