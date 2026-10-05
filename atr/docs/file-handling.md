# 7.22. File handling

**Up**: `7.` [Developer guide](developer-guide)

**Prev**: `7.21.` [SBOM architecture](sbom-architecture)

**Next**: `8.` [Glossary](glossary)

**Sections**:

* [Overview](#overview)
* [Uploads and limits](#uploads-and-limits)
* [Format checks](#format-checks)
* [Archive quarantine](#archive-quarantine)
* [Downloads](#downloads)

## Overview

ATR validates release uploads in temporary storage before creating a revision. Archives that need
validation stay in quarantine until a worker extracts them successfully. The previous revision
remains available while this happens.

## Uploads and limits

| Route | Content | Default size limit |
| --- | --- | --- |
| Browser upload | Release files in a multipart form | 512 MiB per request |
| API `release/store` | One file as the raw request body | 512 MiB per request |
| API `release/upload` | One file, base64 encoded in JSON | 512 MiB per request |
| rsync | Files and directory trees | Files over 2 GB are skipped |
| SVN import | Files from the committee's `dist/dev` area | No byte limit on the import |
| OpenPGP key | One ASCII armored public key | 1 MiB |
| KEYS file | ASCII armored public keys | 10 MiB |
| Admin catalogue import | Projects, releases, and artifacts CSV files | 512 MiB per request |

`MAX_CONTENT_LENGTH` in [`config`](/ref/atr/config.py) sets the HTTP request limit, enforced by
[`body`](/ref/atr/body.py). Form fields and encoding count towards it, so multipart and base64
uploads have less room for file content. There is no limit on the total size of a release.

[`ssh`](/ref/atr/ssh.py) sets the rsync limit, and [`shared.keys`](/ref/atr/shared/keys.py) defines
the key limits. Key imports reject private key material. Keys and catalogue CSV files are parsed
separately from release uploads.

## Format checks

Release upload paths must satisfy the [path rules](input-validation#file-names). Before creating a
revision, [`revision`](/ref/atr/storage/writers/revision.py) validates the whole proposed tree,
including files carried over from the previous revision.

[`detection`](/ref/atr/detection.py) rejects symlinks and checks file contents against the
extensions below. Empty files, unrecognized contents and format mismatches fail these checks. Files
with other extensions are not inspected at this stage.

| Expected format | Extensions |
| --- | --- |
| ZIP | `.apk`, `.jar`, `.nar`, `.nbm`, `.vsix`, `.war`, `.whl`, `.zip` |
| gzip | `.pack.gz`, `.tar.gz`, `.tgz` |
| bzip2, xz, tar | `.tar.bz2`, `.tar.xz`, `.tar` |
| Debian, RPM, Windows executable, PDF | `.deb`, `.rpm`, `.exe`, `.pdf` |

## Archive quarantine

Quarantine applies to `.tar.gz`, `.tgz`, `.zip`, `.tar.bz2`, `.tar.xz`, `.jar`, `.war`, `.apk`,
`.nar`, and `.whl` archives. ATR can reuse validation for identical content previously accepted in
the same release with the same suffix, and `.tgz` and `.tar.gz` count as one suffix.

If any archive needs validation, the whole proposed revision stays outside download paths while a
[`quarantine`](/ref/atr/tasks/quarantine.py) worker extracts its archives.
[`archives`](/ref/atr/archives.py) sets these default extraction limits:

* 2 GiB per file and per archive, configured by `MAX_EXTRACT_SIZE`
* 100,000 files per archive
* A compression ratio of 100
* A path depth of 32

Absolute paths and hard links are rejected. Symlinks inside archives must stay within the extraction
root.

Once all archives pass validation, the worker creates the revision and starts the release
[checks](checks). Those checks can block a vote, but the files remain downloadable. An extraction
error marks the submission failed and removes the quarantined files. A worker interruption can leave
the quarantine pending, which blocks starting a vote. See [Resource management](resource-management)
for worker limits.

## Downloads

Individual files are public, except in embargoed releases, which are hidden from users without
access. [`download`](/ref/atr/get/download.py) serves local files as `application/octet-stream`
attachments. [`safe.StatePath`](/ref/atr/models/safe.py) keeps resolved paths inside managed
storage. The frontend proxy must supply `X-Content-Type-Options: nosniff`, as noted in
[`server`](/ref/atr/server.py). Published downloads redirect to the ASF download services.

Committers can also download an unreleased revision as a ZIP, with no size or file count limit. File
previews require a committer login and show the first 512 KiB as escaped text or a hex dump.

ATR does not scan files for malware.
