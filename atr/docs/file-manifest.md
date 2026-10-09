# 5.2.2. File manifest

**Up**: `5.2.` [Vote phase](vote-phase)

**Prev**: `5.2.1.` [Staging and voting](staging-and-voting)

**Next**: `5.3.` [Finish phase](finish-phase)

**Sections**:

* [Overview](#overview)
* [SHA3-256](#sha3-256)
* [Inner-directory SWHID](#inner-directory-swhid)
* [Not recorded](#not-recorded)

## Overview

Every revision of a release candidate has a file manifest. It lists each file in that revision with
its size and up to two identifiers: a SHA3-256 digest and, for archives, an inner-directory SWHID.
ATR computes these values itself when files are added to the revision, and records them alongside
the revision. They do not change afterwards, so the manifest for a revision describes exactly the
files that were in that revision. The same data is available as JSON, except for embargoed releases.
An expedited security release is embargoed until it is published.

The manifest is there to let you identify files. It is not a signature, and it does not by itself
tell you who produced a file or whether it is safe to use. For that, verify the detached signature
against the committee `KEYS` file, as you would for any ASF release.

## SHA3-256

The SHA3-256 value is a digest of the exact bytes of the file as stored in ATR. If you download the
file and compute its SHA3-256 digest, you should get the same value. For example:

```shell
openssl dgst -sha3-256 apache-example-1.0.0-src.tar.gz
```

A match means that the file you have is byte for byte the file in this revision. It does not mean:

* That the file is signed, or that the signature is valid. The digest is computed by ATR, not by the
  release manager.
* That the file is the same as any `.shaXXX` checksum files published with the release. These files are
  provided by the project, use a different algorithm, and are separate artifacts listed in the
  manifest in their own right.
* That the vote has passed, or that the file has been published. A manifest exists for every
  revision, including ones that are later replaced.

## Inner-directory SWHID

A SWHID is a [Software Hash Identifier](https://www.swhid.org/). The value shown in the
manifest is a directory identifier, starting `swh:1:dir:`, and it is computed over the contents of
an archive rather than over the archive file. ATR extracts the archive, finds the single top-level
directory inside it, and computes the identifier of that directory.

The identifier covers file and directory names, file contents, the executable bit, and symbolic
links. It is the same calculation that Git uses for a tree, so a source archive built from a tag
has the same identifier as the tree of that tag's commit, provided that nothing was added, removed,
or changed when the archive was made. You can get the tree identifier of a commit with:

```shell
git rev-parse 'v1.0.0^{tree}'
```

The SWHID then is `swh:1:dir:` followed by that value.

Because the identifier ignores how the archive was packaged, a `.tar.gz` and a `.zip` with the same
contents have the same SWHID. ATR uses this to check that archives offered in several formats
contain the same files.

The inner-directory SWHID does not:

* Identify the archive file. Two archives with different compression, timestamps, or ownership can
  share a SWHID. Use the SHA3-256 value to identify the file you downloaded.
* Record timestamps, file ownership, or permissions other than the executable bit.
* Include empty directories, which Git cannot represent.
* Include anything outside the top-level directory.

## Not recorded

A value is shown as "Not recorded" when ATR did not store one for that file in this revision. This
is not a check failure, and does not mean that anything is wrong with the file. The common reasons
are:

* The file is not an archive, such as a signature or checksum file. These only ever have a
  SHA3-256 value.
* The archive does not contain exactly one top-level directory, so there is no inner directory to
  identify.
* The revision was created before ATR started recording that value.
