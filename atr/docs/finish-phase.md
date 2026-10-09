# 5.3. Finish phase

**Up**: `5.` [Making releases](making-releases)

**Prev**: `5.2.2.` [File manifest](file-manifest)

**Next**: `5.3.1.` [Promoting to release](promoting-to-release)

**Pages**:

* `5.3.1.` [Promoting to release](promoting-to-release)

**Sections**:

* [Overview](#overview)
* [Responsibilities](#responsibilities)
* [Publishing and announcing](#publishing-and-announcing)

## Overview

The ***finish*** phase turns an approved ***Release Candidate*** into a published release. Once a
vote has passed, the ***Release Artifacts*** are committed to the Apache distribution SVN
repository, at `svn:dist:release`, and from there they propagate to the download servers and the
CDN. The announcement is held back until the artifacts have actually reached the download servers,
so that the links in the announcement work when readers follow them. Once the artifacts are
available and the announcement has been sent, the release is published to the
[Release catalog](release-catalog).

For a step by step walkthrough of this phase with screenshots, see the
[Finish section of the tutorial](/tutorial#finish).

## Responsibilities

The finish phase is again mostly the work of the Release Manager:

* Distribute the release artifacts to any package distribution platforms your project uses, such as
  Maven Central, PyPI, or Docker Hub. ATR does not do this for you, but you can record the results
  on ATR. If your project's [tagging spec](project-configuration#compose) names a platform, ATR will
  not let you announce the release until a distribution to it has been recorded.
* Publish the approved candidate, which commits the artifacts to `svn:dist:release`. This can be
  done automatically, if requested when the vote is started.
* Announce the release through ATR. ATR checks that the artifacts have reached the download servers
  first, and if they have not finished propagating it asks you to try again later.

## Publishing and announcing

ATR commits the artifacts directly to `svn:dist:release` and defers the announcement until they are
available for download, so there is no window in which the announcement points at files that are not
yet there. After publication the release is cataloged alongside every other current and archived ASF
release. See [Promoting to release](promoting-to-release) for the mechanics of the commit and
announcement, and [Release catalog](release-catalog) for where the finished release ends up.
