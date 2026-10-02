# 5.3.1. Promoting to release

**Up**: `5.3.` [Finish phase](finish-phase)

**Prev**: `5.3.` [Finish phase](finish-phase)

**Next**: `5.4.` [Archiving and lifecycle](archiving-releases)

**Sections**:

* [Overview](#overview)
* [Publishing to ASF Distribution Area](#publishing-to-asf-distribution-area)
* [Announcing](#announcing)
* [Removing superseded releases](#removing-superseded-releases)

## Overview

SVN dist area is not an intrinsic part of the ATR release process until publication. The candidate is staged in ATR and voted on there, whatever method was used to upload the files, as described in [Staging and voting](staging-and-voting). When the vote passes, the release moves to the finish phase, in which ATR commits the approved artifacts to the canonical release area of the Apache distribution SVN repository:

* TLP: `https://dist.apache.org/repos/dist/release/<committee>/`
* Podling: `https://dist.apache.org/repos/dist/release/incubator/<committee>/`

Files committed there are served from `downloads.apache.org` and the download CDN, and are picked up by `archive.apache.org` automatically. No manual SVN step is needed to publish a release.

**Podling note**: for a podling, prefix the committee path with `incubator/` in every path below, including the `downloads.apache.org` URL.

## Publishing to ASF Distribution Area

Publication to [ASF Distribution Area](https://downloads.apache.org/) happens in the finish phase, once the vote has resolved successfully. There are two ways to trigger it:

* Automatically, by selecting "Automatically publish to SVN distribution area when this vote resolves" when starting the vote. This option is offered when a committee member starts a non-expedited vote in email or Trusted Vote mode.
* Manually, by pressing the publish button on the finish page for the release.

The finish page shows the destination, and the resulting SVN dist/ revision and download URL once publication completes.

By default the files are placed in a per release subdirectory, `dist/release/<committee>/<project>-<version>/`, except for a committee's top level project, whose files go directly into `dist/release/<committee>/`. Projects can configure this layout with the download path suffix in their release policy, using the `{{PROJECT_KEY}}`, `{{VERSION}}`, and `{{MAJOR_VERSION}}` tokens, and the release manager can adjust the suffix when publishing manually.

The public keys used to sign the release must be in the committee's `KEYS` file before you publish. See [The KEYS file](moving-to-atr#the-keys-file) for how that file is kept in step with ATR.

## Announcing

A release cannot be announced until it has been published to SVN. When you announce, ATR verifies that the published artifacts are reachable on the download servers, and asks you to try again later if they have not finished propagating. Once verified, ATR sends the announcement email and adds the release to the release catalog.

You can also verify the publication yourself:

```shell
svn ls https://dist.apache.org/repos/dist/release/<committee>/
```

The files should soon become visible at `https://downloads.apache.org/<committee>/...`.

## Removing superseded releases

Once a release has been superseded, it should be archived, which removes its files from the distribution area. If enabled in the project settings, select "Auto archive prior release" to do this for the previous release in the same cycle when you announce the new release. See [Archiving and lifecycle](archiving-releases) for the policy behind this, the other ways to archive a release, and what archiving does.
