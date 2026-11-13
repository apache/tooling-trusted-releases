# 5.3.1. Promoting to release

**Up**: `5.3.` [Finish phase](finish-phase)

**Prev**: `5.3.` [Finish phase](finish-phase)

**Next**: `5.4.` [Archiving and lifecycle](archiving-and-lifecycle)

**Sections**:

* [Overview](#overview)
* [Choosing the published layout](#choosing-the-published-layout)
* [Publishing to ASF Distribution Area](#publishing-to-asf-distribution-area)
* [Announcing](#announcing)
* [Removing superseded releases](#removing-superseded-releases)

## Overview

SVN dist area is not an intrinsic part of the ATR release process until publication. The candidate
is staged in ATR and voted on there, whatever method was used to upload the files, as described in
[Staging and voting](staging-and-voting). When the vote passes, the release moves to the finish
phase, in which ATR commits the approved artifacts to the canonical release area of the Apache
distribution SVN repository:

* TLP: `https://dist.apache.org/repos/dist/release/<committee>/`
* Podling: `https://dist.apache.org/repos/dist/release/incubator/<committee>/`

Files committed there are served from `downloads.apache.org` and the download CDN, and are picked up
by `archive.apache.org` automatically. No manual SVN step is needed to publish a release.

**Podling note**: for a podling, prefix the committee path with `incubator/` in every path below,
including the `downloads.apache.org` URL.

## Choosing the published layout

ATR records where it publishes each file, and **does not follow files that are moved afterwards**. A
release whose files have been moved cannot be announced, and its entry in the
[Release catalog](release-catalog) will be wrong. Decide the layout before you publish, rather than
changing it afterwards.

Unless you say otherwise, ATR publishes to these locations:

| Project | Publish location | Example |
| --- | --- | --- |
| The committee's top level project | `dist/release/<committee>/` | `dist/release/maven/` |
| Any other project | `dist/release/<committee>/<project>-<version>/` | `dist/release/maven/maven-surefire-3.5.4/` |

If your project keeps its releases somewhere else, set the **Default publish location** on the
Finish tab of the project's settings, as described in
[Project configuration](project-configuration#finish). It is a path beneath your committee's
directory, and can use the `{{PROJECT_KEY}}`, `{{VERSION}}`, and `{{MAJOR_VERSION}}` tokens:

| Layout you use | Default publish location |
| --- | --- |
| `<committee>/<version>/` | `{{VERSION}}` |
| `<committee>/<project>/<version>/` | `{{PROJECT_KEY}}/{{VERSION}}` |
| `<committee>/maven-3/<version>/` | `maven-{{MAJOR_VERSION}}/{{VERSION}}` |

The release manager can change the **Publish location** for a single release when starting it,
when starting a vote that publishes automatically, and when publishing manually. Each of those forms
shows the full path that the files will be published to, so check it there before you continue.

The layout within the release is decided separately:

* to place files in subdirectories such as `binaries/` and `source/`, create those subdirectories
  when you upload the files in the compose phase,
* to distribute binaries as well as source archives, include them in the release, because ATR only
  publishes and records the files that the release contains.

## Publishing to ASF Distribution Area

Publication to [ASF Distribution Area](https://downloads.apache.org/) happens in the finish phase,
once the vote has resolved successfully. There are two ways to trigger it:

* Automatically, by selecting "Automatically publish to SVN distribution area when this vote
  resolves" when starting the vote. This option is offered when a committee member starts a
  non-expedited vote in email or Trusted Vote mode.
* Manually, by pressing the publish button on the finish page for the release.

The publish form on the finish page shows the full path that the files will be published to, beneath
its **Publish location** field. Once publication completes, the page shows the resulting SVN dist/
revision and download URL. See [Choosing the published layout](#choosing-the-published-layout) for
the default location and how to change it.

The public keys used to sign the release must be in the committee's `KEYS` file before you publish.
See [The KEYS file](moving-to-atr#the-keys-file) for how that file is kept in step with ATR.

## Announcing

A release cannot be announced until it has been published to SVN. When you announce, ATR verifies
that the published artifacts are reachable on the download servers, and asks you to try again later
if they have not finished propagating. Once verified, ATR sends the announcement email and adds the
release to the release catalog.

You can also verify the publication yourself:

```shell
svn ls https://dist.apache.org/repos/dist/release/<committee>/
```

The files should soon become visible at `https://downloads.apache.org/<committee>/...`.

## Removing superseded releases

Once a release has been superseded, it should be archived, which removes its files from the
distribution area. If the project has auto-archive enabled in its settings, select
"Auto archive prior release" to do this for the previous release in the same cycle when you announce
the new release. See
[Archiving and lifecycle](archiving-and-lifecycle) for the policy behind this, the other ways to
archive a release, and what archiving does.
