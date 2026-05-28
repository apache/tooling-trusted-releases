# 2.7. Promoting to release

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.6.` [SBOM workflows](sbom-workflows)

**Next**: `3.1.` [Running the server](running-the-server)

**Sections**:

* [Overview](#overview)
* [Prerequisites](#prerequisites)
* [Moving the files](#moving-the-files)
* [Verifying the move](#verifying-the-move)
* [Removing superseded releases](#removing-superseded-releases)
* [The KEYS file](#the-keys-file)

## Overview

> **This is ATR Alpha behaviour which will change in Beta.**

When the ATR workflow completes, i.e. when the vote is resolved and the release announced, ATR commits the approved artifacts to its own area of the Apache distribution SVN repository:

* TLP: `https://dist.apache.org/repos/dist/atr/<committee>/`
* Podling: `https://dist.apache.org/repos/dist/atr/incubator/<committee>/`

Files in `/dist/atr/` are **not mirrored** by the Apache distribution mirror network and are not considered official releases. To make the release official and available on `downloads.apache.org` and the mirror network, a project committer with write permissions on the destination must move the files into the canonical release area:

* TLP: `https://dist.apache.org/repos/dist/release/<committee>/`
* Podling: `https://dist.apache.org/repos/dist/release/incubator/<committee>/`

The move is a manual SVN step performed once per release. ATR does not do it for you yet because automatic publishing to SVN is currently in testing, so we use `/dist/atr/` instead of `/dist/release/`. In future, ATR will do this for you.

## Prerequisites

You need a working `svn` client and your ASF committer credentials. The destination path under `/dist/release/` must be writable by your ASF user. You can check committee membership on [the Whimsy roster](https://whimsy.apache.org/roster/).

Before you start, confirm what ATR published and where. List the source directory with:

```shell
svn ls https://dist.apache.org/repos/dist/atr/<committee>/
```

**Podling note**: For a podling, prefix the committee path with `incubator/`. Podlings replace `release/<committee>/` with `release/incubator/<committee>/` everywhere below. The source paths under `atr/incubator/<committee>/` follow the same shape. Incubator policy additionally requires the IPMC to have voted on the release before promotion; the PPMC vote that ATR records is not sufficient on its own.

You should also decide the destination layout. Two common conventions exist. The first is a flat layout that matches what ATR produced, where files sit directly under `release/<committee>/`. The second is a per version subdirectory, where files live at `release/<committee>/<version>/`_file_. Most TLPs use the per version layout. Stick to whatever your project has used for previous releases, which you can check with:

```shell
svn ls https://dist.apache.org/repos/dist/release/<committee>/
```

## Moving the files

There are two general ways to perform the move. Choose based on whether the destination layout matches the source.

### Option A: flat to flat

If the destination layout matches the source layout, a single `svn move` from URL to URL moves every artifact for one version in one revision. List each source URL explicitly so that unrelated files in `/dist/atr/<committee>/`, e.g. artifacts from other versions, are left alone:

```shell
svn move -m "Promote <committee> <version> to release" \
  https://dist.apache.org/repos/dist/atr/<committee>/<artifact>.tar.gz \
  https://dist.apache.org/repos/dist/atr/<committee>/<artifact>.tar.gz.asc \
  https://dist.apache.org/repos/dist/atr/<committee>/<artifact>.tar.gz.sha256 \
  https://dist.apache.org/repos/dist/release/<committee>/
```

Multiple sources moved to a single destination directory commit atomically as one revision.

### Option B: flat to per version subdirectory

If you need to place files under a new _version_ directory, or you need to rename anything in flight, use `svnmucc`. This is the _Multiple URL Command Client_, and it ships with the standard `subversion` package. It can performs many URL operations in a single atomic revision:

```shell
svnmucc -m "Promote <committee> <version> to release" \
  mkdir   https://dist.apache.org/repos/dist/release/<committee>/<version> \
  mv      https://dist.apache.org/repos/dist/atr/<committee>/<artifact>.tar.gz \
          https://dist.apache.org/repos/dist/release/<committee>/<version>/<artifact>.tar.gz \
  mv      https://dist.apache.org/repos/dist/atr/<committee>/<artifact>.tar.gz.asc \
          https://dist.apache.org/repos/dist/release/<committee>/<version>/<artifact>.tar.gz.asc \
  mv      https://dist.apache.org/repos/dist/atr/<committee>/<artifact>.tar.gz.sha256 \
          https://dist.apache.org/repos/dist/release/<committee>/<version>/<artifact>.tar.gz.sha256
```

Drop the `mkdir` line if the version directory already exists. `svnmucc` will refuse with error `E160020` if it does.

## Verifying the move

After the commit, list both directories and confirm that the artifacts have moved:

```shell
svn ls https://dist.apache.org/repos/dist/release/<committee>/
svn ls https://dist.apache.org/repos/dist/atr/<committee>/
```

The artifacts should appear under `release/` and be gone from `atr/`. The files should soon become visible at `https://downloads.apache.org/<committee>/...`.

## Removing superseded releases

Apache distribution policy is that `/dist/release/` should hold only the current release of each line, with everything older moved to `archive.apache.org`. Releases committed to `/dist/release/` are picked up by the archive automatically, so you do not have to copy them there yourself. You do, however, have to delete the superseded ones:

```shell
svn rm -m "Remove superseded <committee> <previous-version>" \
  https://dist.apache.org/repos/dist/release/<committee>/<previous-version>
```

Do this as a separate commit, after promotion, once you have verified that the new release is reachable.

## The KEYS file

The `KEYS` file lives at `https://dist.apache.org/repos/dist/release/<committee>/KEYS` and is managed independently of any individual release. ATR does not publish it. If a new signer's key is used for this release, update `KEYS` from your local SVN checkout in a separate commit before users begin verifying artifacts.

ATR imports all keys from existing `KEYS` files automatically, if it can, and allows users to associate additional new keys with committees. These `KEYS` files are then exported to ATR's own download area in Alpha, but this download area will be removed when SVN publication is out of testing. We are currently discussing the mechanisms for exporting and managing `KEYS` files in ATR going forward.
