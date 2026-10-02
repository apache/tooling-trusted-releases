# 2.1. Moving from the current process

**Up**: `2.` [Getting started](getting-started)

**Prev**: `2.` [Getting started](getting-started)

**Next**: `3.` [Projects](projects)

**Sections**:

* [Introduction](#introduction)
* [What ATR replaces](#what-atr-replaces)
* [What stays the same](#what-stays-the-same)
* [Before your first release](#before-your-first-release)
* [The KEYS file](#the-keys-file)

## Introduction

This page is for a PMC that has been releasing in the traditional way, by staging candidates in
`svn:dist:dev`, voting on them, and then moving them to `svn:dist:release` by hand. It covers what
ATR takes over from that process, what does not change, and what to check before your first release
through ATR. Most of it is covered in more detail elsewhere in this documentation, and each section
links to the relevant page.

## What ATR replaces

* **Staging in `dist/dev`.** The candidate is held by ATR for the whole of the compose and vote
  phases, and ATR is the staging location that voters download from. You can still
  [import files](uploading-files#svn-import) from `dist/dev` if that suits you, but you do not need
  to use it. See [Where release candidates are staged](staging-and-voting#where-release-candidates-are-staged)
  and [The earlier dist/dev workflow](staging-and-voting#the-earlier-distdev-workflow).
* **The vote email and tally.** ATR sends the vote email, linking to the candidate page and the
  committee's `KEYS` file, and tallies the votes from the thread. The release manager should still
  check the tally against the thread before resolving the vote. Projects can also choose to have
  votes cast in ATR itself. See [Vote phase](vote-phase) and the vote settings in
  [Project configuration](project-configuration).
* **Moving files to `dist/release`.** ATR commits the approved artifacts to `svn:dist:release`
  itself, either when the vote resolves or when you press the publish button. See
  [Promoting to release](promoting-to-release).
* **The announcement.** ATR sends the announcement email, and only once the artifacts have reached
  the download servers.
* **Removing superseded releases.** ATR can archive the prior release in the same cycle when the
  new one is announced, or you can archive a release from its page. See
  [Archiving and lifecycle](archiving-releases).

## What stays the same

* The PMC still votes on each release, the vote thread is still on the project's mailing list,
  and whose votes are binding is still a matter of ASF policy. Podlings still vote twice, as described in
  [Podling releases](podling-releases).
* Each release manager still signs the artifacts with their own OpenPGP key, and the committee
  still publishes a single `KEYS` file at `svn:dist:release`. See [The KEYS file](#the-keys-file)
  below.
* Published releases still live in `svn:dist:release`, are served from `downloads.apache.org`, and
  are picked up by `archive.apache.org` automatically.
* Distributing to package platforms such as Maven Central, PyPI, or Docker Hub is still up to the
  project. You can record those distributions in ATR, as described in [Finish phase](finish-phase).
* Committing to `svn:dist:release` directly still works. ATR watches it, so a release committed
  there without ATR is added to the [Release catalog](release-catalog), and removing a release's
  files archives it.

One thing does change for release managers: a PMC member can designate a committer who is not on
the PMC as a release manager. See [Release managers](release-manager-setup).

## Before your first release

Work through [Getting started](getting-started) first, which covers MFA, your signing key, and
upload credentials. In addition, as a PMC coming from the traditional process, check the following.

* **Your seeded projects.** ATR seeds each committee's projects from DOAP files and observed
  release activity, so your projects are probably already there. Check that the list reflects your
  true number of projects. See [Creating and maintaining projects](projects#creating-and-maintaining-projects).
* **Your catalog.** The [Release catalog](release-catalog) was built from the files in the
  distribution area and archive, and can attach releases to the wrong project or read a version
  wrongly. Review your committee's pages and tell us what needs correcting, as described in
  [Reviewing and correcting your catalog](release-catalog#reviewing-and-correcting-your-catalog).
* **Your `KEYS` file mode.** By default ATR imports the committee's `KEYS` file from SVN and does
  not write back, so your keys are read-only in ATR. If you would rather manage keys in ATR, change
  the mode first. See [The KEYS file](#the-keys-file).
* **Scripts that fetch from SVN.** If your project has scripts that download a candidate from
  `dist/dev` to check it, point them at ATR instead. See
  [Verifying artifacts during a vote](staging-and-voting#verifying-artifacts-during-a-vote).
* **Your vote email template.** If you have your own template, it should link to the ATR
  candidate page rather than to a copy in `dist/dev`. See
  [What to link in a vote announcement](staging-and-voting#what-to-link-in-a-vote-announcement).
* **Release scripts that move files to `dist/release`.** These are no longer needed, and should
  not run alongside ATR's own publication. Check where your project's files should land, set by
  the download path suffix described in [Promoting to release](promoting-to-release#publishing-to-asf-distribution-area).

## The KEYS file

Signing releases is done with individual keys, as described in [Signing artifacts](signing-artifacts). Separately, each committee publishes a single `KEYS` file listing the public keys that its release managers sign with. The file lives at `https://dist.apache.org/repos/dist/release/<committee>/KEYS`, is managed independently of any individual release, and is what downstream users fetch to verify release signatures. When you upload your key to ATR and associate it with a committee, it becomes part of the set that ATR holds for that committee's `KEYS` file.

Committee members choose how the file is kept in step with ATR, using the KEYS file management setting on the committee's page. There are three modes:

* **Automatically update the committee's KEYS file.** ATR owns the file. Whenever the committee's keys change in ATR, ATR regenerates the `KEYS` file and commits it to SVN. This is the simplest option if the committee manages its keys in ATR.
* **Automatically import changes to the KEYS file made in SVN.** SVN owns the file, and this is the default. ATR watches the committee's `KEYS` file in SVN and imports updates to it, but never writes back. In this mode the committee's keys are read-only in ATR, so uploads, associations, and deletions for the committee are refused; make the change in SVN instead.
* **Manually upload KEYS files in ATR.** ATR holds the keys, but commits to SVN only on an explicit request. The committee page offers two actions: import keys from an uploaded `KEYS` file, or regenerate the published file from the keys ATR already holds. Either publishes the result to SVN. Other key changes are not published until then, so the file in SVN can lag what ATR holds.

| | The keys are managed... | ATR commits the file to SVN... | ATR imports changes from SVN... |
| --- | --- | --- | --- |
| Automatically update | in ATR | on every key change | no |
| Automatically import (default) | in SVN | never | when the file is updated |
| Manually upload | in ATR | when you upload or regenerate | no |

Changing the mode does not, of itself, delete any keys from ATR. Switching to the import mode starts an import of the current file in SVN, however, which can remove keys from the committee that the file does not contain. A key that has signed artifacts catalogued by ATR is never removed this way: it stays associated with the committee, marked on the committee page as missing from SVN, until it reappears in the file or the situation is resolved by hand. Deleting the `KEYS` file in SVN does not remove any keys either. Switching to either of the other two modes does not publish anything; the file in SVN changes only on the triggers described above.

Whichever mode is chosen, the public keys of any keypairs used to sign a release must be present in the committee's `KEYS` file before you publish signed artifacts.
