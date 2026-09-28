# 5.4. Archiving and lifecycle

**Up**: `5.` [Making releases](release-process-description)

**Prev**: `5.3.1.` [Promoting to release](promoting-to-release)

**Next**: `5.5.` [Podling releases](podling-releases)

**Sections**:

* [Overview](#overview)
* [What archiving does](#what-archiving-does)
* [Archiving a release](#archiving-a-release)
* [Archiving the prior release automatically](#archiving-the-prior-release-automatically)
* [Archiving outside ATR](#archiving-outside-atr)
* [Lifecycle events](#lifecycle-events)
* [Retiring a project](#retiring-a-project)

## Overview

A release does not end at its announcement. ASF
[release distribution policy](https://infra.apache.org/release-distribution.html#archival) is that
the distribution area should hold only the current release of each line, so an older release is
***archived*** once it has been superseded or is no longer supported. An archived release is not lost: its files remain
available from [archive.apache.org](https://archive.apache.org/), and it stays in the
[Release catalog](release-catalog).

This page describes how a release is archived, what archiving does, and how to record the end of
support for a line of releases.

## What archiving does

When a release is archived, ATR:

* marks it as archived, so that it moves from the project's current releases to its archive, both
  in ATR and in the release catalog,
* removes the release's files from `svn:dist:release`, and so from the download servers,
* records the event, which is published as an "end of distribution" event among the project's
  [lifecycle events](#lifecycle-events).

ATR removes only the files that it knows belong to the release. Where a release has a directory
to itself, the whole directory is removed. Where it shares a directory with other releases, only
its own files are removed. If other files remain in the release's own directory afterwards, ATR
tells you, because they may need to be cleaned up by hand.

Files committed to `svn:dist:release` are picked up by archive.apache.org automatically, so you do
not have to copy a release there before archiving it.

## Archiving a release

Members of the project's committee archive a release from the "Release actions" card on the
release's page, which you reach from the release's entry on the project page. What happens depends
on whether the release has been superseded:

* **A superseded release**, one that is not the latest in its cycle, can be archived straight
  away. Use "Archive release", and type `ARCHIVE` to confirm.
* **The latest release in its cycle** cannot be archived on one person's say. It requires
  ***Contingent Approval*** from the committee, which is a vote held through the ASF's Contingent
  Approval Portal (***CAP***), currently at [cap-test.apache.org](https://cap-test.apache.org/).
  Use "Request archival vote" to start the vote. The card shows the vote's CAP number and closing
  time while it runs, and committee members cast their votes on the portal. If the vote passes,
  ATR archives the release itself.

A release is the latest in its cycle according to the project's version scheme, described under
[Lifecycle](project-configuration#lifecycle).

## Archiving the prior release automatically

Most releases supersede the one before them, so ATR can archive the prior release for you when
the new one is announced:

1. Enable "Allow auto-archive" on the project's [Finish](project-configuration#finish) tab.
2. When you start a release, select "Auto archive prior release".
3. When you announce the release, the announcement form shows the version that will be archived,
   and you can still choose to clear the option there.

The prior release is the previous release in the same cycle, so a new `2.x` release does not
archive a `1.x` release that is still supported.

## Archiving outside ATR

ATR watches `svn:dist:release`. If the files of a release are removed from it directly, without
ATR, the release is archived in ATR and in the release catalog once the removal has been
committed. If the files of an archived release are later committed back, the release is restored
as a current release.

## Lifecycle events

Archiving records that a release is no longer distributed. The end of development and support
are properties of a whole ***cycle***, such as all `2.x` releases, and you record them as the
[cycle's dates](project-configuration#cycle-dates).

ATR publishes both as lifecycle events in the [Release catalog](release-catalog#machine-readable-data),
in a `cle.json` document for each project. The document follows the
[Common Lifecycle Enumeration](https://ecma-international.org/publications-and-standards/standards/ecma-428/)
(***CLE***) standard, which defines the events. What you do in ATR produces them as follows:

* announcing a release, or having one catalogued, records a **released** event,
* archiving a release records an **end of distribution** event,
* setting a cycle's dates records its **end of development**, **end of support**, and **end of
  life** events.

If you correct a cycle date, ATR publishes the change as a correction in the way the standard
defines, so you can fix a mistake without hiding it from consumers of the feed.

## Retiring a project

A project that is no longer maintained can be retired. Retiring a project requires
[Contingent Approval](#archiving-a-release) from the committee. A member of the committee uses
"Request archival" in the Actions card on the project page, which starts the vote. It is offered
once all the project's releases have been archived, and only where the committee has another
active project. If the vote passes, the member who asked returns to complete the archival, which
deletes any draft releases and retires the project.

A project that has never had a release, for example one created by mistake, is offered "Request
deletion" instead. It works in the same way, and completing it removes the project from ATR
altogether rather than retiring it.

A retired project cannot be edited and cannot start releases. Its archived releases remain in
the release catalog, and the project is listed on the catalog's Attic page.

Contingent Approval is not available to podlings. Retiring a podling is a decision for the
Incubator PMC.
