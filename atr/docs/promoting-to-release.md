# 2.8. Promoting to release

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.7.` [Staging and voting](staging-and-voting)

**Next**: `3.1.` [Running the server](running-the-server)

**Sections**:

* [Overview](#overview)
* [Publishing to SVN](#publishing-to-svn)
* [Announcing](#announcing)
* [Removing superseded releases](#removing-superseded-releases)
* [The KEYS file](#the-keys-file)

## Overview

SVN is not an intrinsic part of the ATR release process until publication. The candidate is staged in ATR and voted on there, whatever method was used to upload the files, as described in [Staging and voting](staging-and-voting). When the vote passes, the release moves to the finish phase, in which ATR commits the approved artifacts to the canonical release area of the Apache distribution SVN repository:

* TLP: `https://dist.apache.org/repos/dist/release/<committee>/`
* Podling: `https://dist.apache.org/repos/dist/release/incubator/<committee>/`

Files committed there are served from `downloads.apache.org` and the download CDN, and are picked up by `archive.apache.org` automatically. No manual SVN step is needed to publish a release.

**Podling note**: for a podling, prefix the committee path with `incubator/` in every path below, including the `downloads.apache.org` URL.

## Publishing to SVN

Publication happens in the finish phase, once the vote has resolved successfully. There are two ways to trigger it:

* Automatically, by selecting "Automatically publish to SVN when this vote resolves" when starting the vote. This option is offered when a committee member starts a non-expedited vote in email or Trusted Vote mode.
* Manually, by pressing the publish button on the finish page for the release.

The finish page shows the destination, and the resulting SVN revision and URL once publication completes.

By default the files are placed in a per release subdirectory, `release/<committee>/<project>-<version>/`, except for a committee's top level project, whose files go directly into `release/<committee>/`. Projects can configure this layout with the download path suffix in their release policy, using the `{{PROJECT_KEY}}`, `{{VERSION}}`, and `{{MAJOR_VERSION}}` tokens, and the release manager can adjust the suffix when publishing manually.

## Announcing

A release cannot be announced until it has been published to SVN. When you announce, ATR verifies that the published artifacts are reachable on the download servers, and asks you to try again later if they have not finished propagating. Once verified, ATR sends the announcement email and adds the release to the release catalog.

You can also verify the publication yourself:

```shell
svn ls https://dist.apache.org/repos/dist/release/<committee>/
```

The files should soon become visible at `https://downloads.apache.org/<committee>/...`.

## Removing superseded releases

Apache distribution policy is that `/dist/release/` should hold only the current release of each line, with everything older moved to `archive.apache.org`. Releases committed to `/dist/release/` are picked up by the archive automatically, so you do not have to copy them there yourself. You do, however, have to delete the superseded ones. What to delete depends on the layout your project publishes to, described above. With the default layout for a project that is not the committee's top level project, a superseded release is a single subdirectory:

```shell
svn rm -m "Remove superseded <committee> <previous-version>" \
  https://dist.apache.org/repos/dist/release/<committee>/<project>-<previous-version>
```

A top level project's files sit directly in the committee directory, so list each superseded file in the `svn rm` command instead.

Do this once you have verified that the new release is reachable. Archiving a release in ATR records the archival in the release catalog, but does not currently remove the files from `/dist/release/`, so this step remains manual.

## The KEYS file

The `KEYS` file lives at `https://dist.apache.org/repos/dist/release/<committee>/KEYS` and is managed independently of any individual release. Depending on a setting chosen by committee members on the committee's page in ATR, either ATR maintains and publishes this file from the keys that users have uploaded and associated with the committee, or ATR follows the copy managed directly in SVN.

Whichever way the file is managed, the public keys of any keypairs used to sign a release must be present in the committee's `KEYS` file before you publish signed artifacts.
