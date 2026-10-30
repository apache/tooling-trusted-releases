# 5.7. Troubleshooting

**Up**: `5.` [Making releases](making-releases)

**Prev**: `5.6.` [Making a release with the client](client-guide)

**Next**: `6.` [Release catalog](release-catalog)

**Sections**:

* [Overview](#overview)
* [Files missing at announcement](#files-missing-at-announcement)
* [Choosing the published layout](#choosing-the-published-layout)

## Overview

This page describes problems that release managers commonly encounter, and how to resolve them. If
you encounter a problem that is not described here, please
[open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=BLANK_ISSUE).

## Files missing at announcement

When you announce a release, ATR checks that the artifacts it published can be downloaded from the
location to which it published them. If any cannot, the announcement is refused, for one of two
reasons.

If the files are still in `svn:dist:release`, they have not yet reached the download servers. Files
usually take a few minutes to propagate, so wait and then announce again.

If the files are no longer in `svn:dist:release` at the location to which ATR published them, they
have been moved, renamed, or removed since publication. ATR expects to manage publication and
archival in `svn:dist:release` itself. It records the location of each file when it publishes a
release, and continues to watch the distribution area afterwards, as described in
[Archiving outside ATR](archiving-and-lifecycle#archiving-outside-atr). If the source archives of a
release are removed from that location, ATR archives the release. Copying the files back so that the
announcement succeeds, and then removing the copies, therefore results in archival of the release that was just
announced.

How to resolve this error depends on which location is correct:

* if the files can live at the location to which ATR published them, move them back there, and
  attempt to announce again
* if the files are now at the correct location, do not move them back to satisfy ATR. Instead,
  [open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=release-files-moved.yml)
  giving the release and the current location of its files, so that ATR's record can be updated.

## Choosing the published layout

ATR publishes a release with the same layout that it has in ATR, so the layout should be decided
before publication rather than changed afterwards:

* to place files in subdirectories such as `binaries/` and `source/`, create those subdirectories
  when you upload the files in the compose phase,
* to publish the whole release to a particular directory, set the download path suffix in the project's
  release policy, for example `maven-{{MAJOR_VERSION}}/{{VERSION}}`, as described in
  [Publishing to ASF Distribution Area](promoting-to-release#publishing-to-asf-distribution-area),
* to distribute binaries as well as source archives, include them in the release, because ATR only
  publishes and records the files that the release contains.
