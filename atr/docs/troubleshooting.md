# 5.7. Troubleshooting

**Up**: `5.` [Making releases](making-releases)

**Prev**: `5.6.` [Making a release with the client](client-guide)

**Next**: `6.` [Release catalog](release-catalog)

**Sections**:

* [Overview](#overview)
* [Files missing at announcement](#files-missing-at-announcement)
* [Upload refused by a quota](#upload-refused-by-a-quota)

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

## Upload refused by a quota

ATR limits what a release can hold while it is in progress. A change that would take a release past
a limit is refused, and the release is left as it was.

| Limit | Value |
| --- | --- |
| Size of one file | 2 GB |
| Total size of a release | 10 GiB |
| Number of files in a release | 5,000 |
| Total size of a project's releases in progress | 25 GiB |
| Releases in progress for a project | 10 |

If a project needs more than one of these allows, please
[open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=BLANK_ISSUE).

### ATR is low on storage space {#server-low-on-space}

ATR itself is short of storage space, and refuses new files for every project until it has more.
Nothing is wrong with your release, and there is nothing to change in it. Please
[open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=BLANK_ISSUE)
so that we know, and try again once we have replied.

### A file is too large {#file-too-large}

The file is over 2 GB, which is the most that ATR accepts for one file. The
[Release Distribution Policy](https://infra.apache.org/release-distribution.html) gives 1GB as the
most that the ASF will host for one artifact, and ATR reports a file over that as a check concern.

### The release is too large {#release-too-large}

The files in the release would come to more than its limit. Remove files that the release does not
need, such as artifacts from an earlier attempt, and try again.

### The release has too many files {#release-too-many-files}

The release would have more files than its limit. Signatures and checksums count as files. Remove
any files that the release does not need, and try again.

### The project holds too much {#project-too-large}

The releases that the project has in progress would hold more than its limit. ATR keeps every
revision of a release until the release is announced, so a file that was replaced or removed still
counts, and removing files does not bring the total down. Announce releases that are ready, or
delete drafts that are no longer needed.

### The project has too many releases in progress {#project-too-many-releases}

A release is in progress from when it is started until it is announced, and the project already has
as many as its limit allows. Announce releases that are ready, or delete drafts that are no
longer needed.
