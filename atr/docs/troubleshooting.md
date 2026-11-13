# 5.7. Troubleshooting

**Up**: `5.` [Making releases](making-releases)

**Prev**: `5.6.` [Making a release with the client](client-guide)

**Next**: `6.` [Release catalog](release-catalog)

**Sections**:

* [Overview](#overview)
* [Files missing at announcement](#files-missing-at-announcement)

## Overview

This page describes problems that release managers commonly encounter, and how to resolve them. If
you encounter a problem that is not described here, please
[open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=BLANK_ISSUE).

## Files missing at announcement

You tried to announce a release and ATR refused, saying either that the files it published are no
longer in SVN where it put them, or that they are not yet available on the download server.

When you announce a release, ATR checks that the artifacts it published can be downloaded from the
location to which it published them. That location is shown on the finish page for the release. To
find out which case you are in, list it:

```shell
svn ls https://dist.apache.org/repos/dist/release/<committee>/<publish location>/
```

### The files are listed

The files are where ATR put them, and have not yet reached the download servers. They usually take a
few minutes to propagate, so wait and then announce again.

### The files are not listed

The files have been moved, renamed, or removed since ATR published them. ATR expects to manage
publication and archival in `svn:dist:release` itself. It records the location of each file when it
publishes a release, and does not follow files that are moved afterwards. What to do depends on
which location is the right one.

**If the files can live where ATR published them**, move them back to that location, and announce
again.

**If the files are now in the right place**, leave them there, and
[open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=release-files-moved.yml)
giving the release and the current location of its files. We will update ATR's record, after which
you can announce.

**Do not copy the files back just to get past the check.** ATR continues to watch the distribution
area after a release is announced, as described in
[Archiving outside ATR](archiving-and-lifecycle#archiving-outside-atr). If the source archives of a
release are removed from the location that ATR recorded, ATR archives the release. Copying the files
back so that the announcement succeeds, and then removing the copies, therefore archives the release
that was just announced.

### For your next release

This happens when ATR's publish location does not match the layout that the project uses, most often
because the release manager did not notice that the default for a committee's top level project is
the committee's own directory, `dist/release/<committee>/`. Set the **Default publish location** in
the project's settings to the layout you use, and ATR will publish there in the first place. See
[Choosing the published layout](promoting-to-release#choosing-the-published-layout).
