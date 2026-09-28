# 5.2.1. Staging and voting

**Up**: `5.2.` [Vote phase](vote-phase)

**Prev**: `5.2.` [Vote phase](vote-phase)

**Next**: `5.3.` [Finish phase](finish-phase)

**Sections**:

* [Where release candidates are staged](#where-release-candidates-are-staged)
* [What to link in a vote announcement](#what-to-link-in-a-vote-announcement)
* [Verifying artifacts during a vote](#verifying-artifacts-during-a-vote)
* [How files reach ATR](#how-files-reach-atr)
* [The earlier dist/dev workflow](#the-earlier-distdev-workflow)

## Where release candidates are staged

When you compose a release candidate in ATR, the candidate artifacts are held by ATR itself. They stay there for the whole of the compose and vote phases. Nothing is copied into the Apache distribution SVN repository until the vote has passed and you finish the release, which is covered in [Promoting to release](promoting-to-release).

This means that ATR is the staging location for your candidate. Voters download the files from ATR, run whatever checks they want to run against them, and vote on that basis.

ASF release policy accommodates this. The policy [recommends](https://www.apache.org/legal/release-policy.html#stage) that projects use either the `dist/dev` tree or the staging features of `repository.apache.org` to host release candidates, and it describes `dist/dev` as a staging location intended for use before a release becomes official. That recommendation does not preclude other staging locations, and the policy does not require any particular one. ATR is a further place where candidates may be staged.

## What to link in a vote announcement

The default ATR vote template links to two things: the release candidate page on ATR, which is where the downloads are, and the committee `KEYS` file. It deliberately does not link to any other source of the same artifacts.

We recommend that you keep it that way, and link only to the ATR release candidate page in your vote emails.

The reason is that a vote is a vote on one identified set of files. ATR pins the candidate to a specific revision, so the files behind the link cannot change underneath the voters. If a vote email also points at a copy of the artifacts held somewhere else, then there are two sets of files in play and nothing guarantees that they stay identical. Voters may then be voting on different bytes to one another, and it becomes the release manager's job to keep the copies in step for the duration of the vote.

If your project does choose to link to another source as well, mark those links clearly as informational. They should not be presented as a formal part of the vote. The artifacts that the vote is being held on are the ones on ATR.

## Verifying artifacts during a vote

Some projects may have scripts that download a candidate and run checks over it. If yours does, and it currently fetches the files from SVN, point it at ATR instead.

The release candidate page gives you a one line `curl` command that fetches every file in the candidate into the current directory. If you would rather drive the downloads yourself, a plain list of the file URLs is available at `/download/urls/<project>/<version>`. Both that list and the script behind the `curl` command are public, so a voter who is not a committer can still use them.

ATR also runs its own [checks](checks) over every revision, and the results are visible on the candidate page. These are intended to cover as many mechanically checkable ASF policy requirements as possible, so in some cases they may already cover what a project's own script was written to check.

## How files reach ATR

Files are added to a candidate during the compose phase, and there are many ways to do so. They are listed under [How files reach ATR](compose-phase#how-files-reach-atr) in the compose phase documentation.

## The earlier dist/dev workflow

Before ATR published anything to SVN, we advised release managers to upload their candidate to `dist/dev`, copy the files into ATR, hold the vote, and then move the files from `dist/dev` to `dist/release` to publish. Under that workflow it was the release manager's responsibility to make sure that the files in `dist/dev` matched the ones in ATR for the duration of the vote.

That workflow still works, but it is no longer necessary, because ATR now publishes the approved artifacts itself, directly to `dist/release`, after the vote passes. Unless you have a specific reason to keep using `dist/dev`, you do not need it when using ATR.
