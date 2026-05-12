# 2.5. Check ignores

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.4.` [License checks](license-checks)

**Next**: `2.6.` [Trusted Publishing](trusted-publishing)

**Sections**:

* [Overview](#overview)
* [Syntax](#syntax)
* [Examples](#examples)

## Overview

Check ignores let project committee members hide specific check results in the UI. Ignored checks are removed from the suggestion and concern counts shown on the checks pages and are shown separately in _Ignored checks_ sections.

### Where to manage ignores

You can manage ignores from the release checks page by selecting _Manage check ignores_. The ignores page shows the existing rules and lets you add, update, or delete them, and the per-file report page shows ignored checks for that specific file.

### Permissions and visibility

Any committer can view the ignores page for a project, but only committee members can add, update, or delete ignores. Ignores are stored per project and apply only to that project's releases.

Please note that on the release checks page, the _Ignored checks_ list includes primary check results only; archive member checks are not shown there. On the per-file report page, the _Ignored checks_ list can include member checks for that file.

## Syntax

### Fields and what they match

Each ignore rule can match on any combination of fields, and any field you leave blank does not restrict matching. The release pattern matches the release name in the `project-version` format; the revision number is a literal string match (for example `00005`) and cannot use patterns; the checker pattern matches the full checker key; the primary rel path pattern matches the artifact filename; the member rel path pattern matches a path inside the archive; the status matches `Concern`, `Exception`, or `Suggestion`; and the message pattern matches the check message text.

### Matching rules

Notes and blockers are never ignored, so only `Concern`, `Exception`, and `Suggestion` results can match. When a rule includes multiple fields, all filled fields must match for the ignore to apply, and if any ignore rule matches a check result then the check result is ignored.

### Pattern syntax

Patterns use one of two modes.

A pattern is treated as a glob-like string when it does not start with `^` and does not end with `$`. In this mode, `*` matches any number of characters, all other characters are treated literally, and matching is a substring match rather than a full string match.

A pattern is treated as a regular expression when it starts with `^` or ends with `$`, and the regex is applied as a search. To keep rules portable across engines, use the common subset of RE2 and Hyperscan:

* Anchor with `^` and `$` (mandatory to enable the regex mode)
* Match any chracter with `.`
* Escape metacharacters with `\`
* Use character ranges using classes like `[A-Za-z0-9_.-]`
* Group with `(...)`
* Alternate with `|`
* Quantify with `*`, `+`, `?`

Patterns longer than 128 characters are rejected, and cannot be used.

Negation and missing values are handled with a leading exclamation mark. Prefix a pattern of either mode with `!` to negate it, and use the special pattern `!` by itself to match a missing value such as a missing member path. Negated patterns do not match missing values unless you use the standalone `!`. Use `!` before the opening anchor `^` in regular expression mode.

## Examples

To ignore all RAT suggestions for your project, set the checker pattern to `atr.tasks.checks.rat.check` and the status to `Suggestion`.

To ignore license header concerns for a specific release series, set the release pattern to `apache-example-1.2.*`, the checker pattern to `atr.tasks.checks.license.headers`, and the status to `Concern`.

To ignore only a specific revision, set the release pattern to `apache-example-2.0.0`, set the revision number to the literal value `00005`, and set the status to `Suggestion`.

To ignore a single artifact file, set the primary rel path pattern to `apache-example-2.0.0-source.tar.gz`, the checker pattern to `atr.tasks.checks.signature.check`, and the status to `Concern`.

To ignore suggestions for one file inside the archive, set the member rel path pattern to `apache-example-2.0.0/src/main/java/Foo.java`, set the checker pattern to `atr.tasks.checks.license.headers`, and set the status to `Suggestion`.

To match only primary results with no member path, set the member rel path pattern to `!`, set the checker pattern to `atr.tasks.checks.paths.check`, and set the status to `Suggestion`.

To use regex for an exact release name, set the release pattern to `^apache-example-[0-9]+\.[0-9]+\.[0-9]+$` and set the status to `Concern`.

To ignore all suggestions except the ATR license header checks, set the checker pattern to `!atr.tasks.checks.license.headers` and set the status to `Suggestion`.

Try setting multiple ignores to understand how matching works. Separate ignores are ORed together, so e.g. one rule that ignores RAT suggestions and another separate rule that ignores signature concerns will result in any checks that match either rule being ignored.
