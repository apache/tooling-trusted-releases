# 6. Release catalog

**Up**: [Documentation](.)

**Prev**: `5.5.` [Podling releases](podling-releases)

**Next**: `7.` [Developer guide](developer-guide)

**Sections**:

* [Introduction](#introduction)
* [Browsing the catalog](#browsing-the-catalog)
* [Machine readable data](#machine-readable-data)
* [Package URLs](#package-urls)
* [How the catalog was built](#how-the-catalog-was-built)
* [Keeping the catalog up to date](#keeping-the-catalog-up-to-date)
* [Reviewing and correcting your catalog](#reviewing-and-correcting-your-catalog)

## Introduction

ATR maintains a ***Catalog*** of every ASF release, current and archived, across every committee,
project, and podling. The catalog is published as a static public website at
[release-catalog.apache.org](https://release-catalog.apache.org/), so end users do not need an ATR
account to use it.

For each release, the catalog lists its ***Artifacts***, and for each artifact it links to the
download along with its detached signature, its checksum, and its SBOM where one was published.
Download links for current releases go through the ASF mirror system, and links for archived
releases go to [archive.apache.org](https://archive.apache.org/). Signatures, checksums, and each
committee's public `KEYS` file are always linked from the ASF's own servers.

The catalog includes releases made through ATR and releases made in the traditional way, by
committing files directly to `svn:dist:release`. Releases made through ATR carry an ***ATR
certified*** badge.

## Browsing the catalog

The catalog follows the same structure as the rest of ATR, described in [Projects](projects):

* The front page shows a card for each committee, with a count of its current releases and the date
  of the latest one. You can search the cards by committee or project name.
* A committee page lists the committee's projects. Where a committee has only one project, the front
  page links straight to the project instead.
* A project page lists its current releases, and links to a separate archive page listing its
  archived releases.
* A release page lists the release's artifacts with their downloads, and links to the release's vote
  thread where ATR knows it.

Podlings are grouped together on a single Incubator page, which lists current podlings and retired
podlings separately. Retired projects are gathered on a single Attic page, which links to each
project's archived releases.

## Machine readable data

Each page in the catalog has a JSON counterpart, so that tools can consume the same data:

* `artifacts.json`, on each release, is the release manifest. It lists the release's artifacts, with
  their download, signature, checksum, and SBOM URLs. The release page links to it as "Metadata".
* `cle.json`, on each project and on each release that has lifecycle events, is the ***Common
  Lifecycle Enumeration*** (***CLE***) document for that project or release. See
  [Lifecycle events](archiving-and-lifecycle#lifecycle-events).
* `project_releases.json` and `podling_releases.json`, at the root of the site, summarise every
  project and podling with its releases. They mirror the split, and many of the fields, of the ASF's
  existing `projects.json` and `podlings.json` files.

## Package URLs

Every release in the catalog has a ***Package URL*** (***PURL***) of the form
`pkg:software-id/apache.org/the+asf/<project_key>@<version>`. Replacing the `pkg:software-id/`
prefix with `https://` gives a working link. A bare link resolves to the release's `artifacts.json`,
and adding a query string, such as `?class=src&ext=tar.gz`, resolves to a single download. The
catalog explains the full set of qualifiers at [the PURL prefix](https://apache.org/the+asf/).

## How the catalog was built

The ASF has no single historical record of its releases, so the catalog was built from the files
that were actually published. ATR read the complete file listings of the current distribution area
and of archive.apache.org, and grouped each committee's files into projects, releases, and
artifacts. It paired each artifact with its signature, checksum, and SBOM where present, and took
release dates from the history of the distribution area. We then matched the results against the
ASF's existing project records, such as DOAP files and `projects.json`, to name projects and assign
them to committees.

Directory layouts on the distribution servers vary a great deal between committees and over time, so
this process is not perfect. A release may be attached to the wrong project, a project may be
missing or split in two, or a version may have been read wrongly from a file name. Until a committee
has reviewed its catalog, each of its pages carries a notice saying that the information was
assembled by ATR from the distribution archives and has not yet been reviewed.

## Keeping the catalog up to date

Once built, the catalog is kept up to date automatically:

* When you announce a release through ATR, it is added to the catalog, as described in
  [Promoting to release](promoting-to-release).
* ATR watches `svn:dist:release`, so a release committed there directly, without ATR, is catalogued
  when the commit lands.
* When a release is archived, whether through ATR or by removing its files from `svn:dist:release`,
  the catalog moves it to the project's archive. See
  [Archiving and lifecycle](archiving-and-lifecycle).

## Reviewing and correcting your catalog

We ask every PMC to review its catalog, starting from its committee page on
[release-catalog.apache.org](https://release-catalog.apache.org/). Check that:

* the list of projects reflects your *true number of projects*,
* each release is under the right project, with the right version,
* current releases and archived releases are shown as such.

Correcting the structure of a catalog, such as renaming, moving, merging, or deleting projects, or
moving a release to a different project, is done by the Tooling team. Contact us on
[dev@tooling.apache.org](mailto:dev@tooling.apache.org) with what needs to change, or to tell us
that your catalog is correct. Once your PMC is satisfied, we mark the catalog as reviewed and the
notice is removed from your pages. For larger-scale corrections or agent-led reviews, we can provide
an export of your PMC's data as held by ATR.
