# 6. Release catalog

**Up**: [Documentation](.)

**Prev**: `5.7.` [Troubleshooting](troubleshooting)

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
podlings separately. Projects that have retired to the Attic are displayed on a dedicated Attic
page.

## Machine readable data

Each page in the catalog has a JSON counterpart, so that tools can consume the same data:

* `artifacts.json`, on each release, is the release manifest. It lists the release's artifacts, with
  their download, signature, checksum, and SBOM URLs, and gives its [Package URLs](#package-urls).
  The release page links to it as "Metadata".
* `cle.json`, on each project and on each release that has lifecycle events, is the ***Common
  Lifecycle Enumeration*** (***CLE***) document for that project or release. See
  [Lifecycle events](archiving-and-lifecycle#lifecycle-events).
* `project_releases.json` and `podling_releases.json`, at the root of the site, summarise every
  project and every podling project with its releases. See [Project summaries](#project-summaries).
* `project.json`, on each project and podling, is that project's summary on its own.
* `activity.json` and `activity.yaml`, on each committee, are the committee's recent activity for
  a board report: every current release, and every release archived in the last ninety days, as
  two dated lists with the newest first.

### Project summaries

`project_releases.json` and `podling_releases.json`. Each is a single JSON object with one entry per project, keyed by ATR
project key and sorted by key. Podling projects appear only in `podling_releases.json`, and move to
`project_releases.json` when the podling graduates. Projects whose committee never makes releases
of its own, such as Whimsy and Gump, are left out of `project_releases.json`, but each still has a
`project.json`.

Every entry has these fields:

| Field | Type | Contents |
| --- | --- | --- |
| `name` | string | The project's name, such as `Apache Accumulo` |
| `pmc` | string | The key of the committee that owns the project |
| `status` | string | `active`, `dormant`, `retired` or `standing` |
| `license` | string | The URL of the Apache License 2.0 |
| `created` | string | The date the project was founded |
| `maintainer` | list | One object, with the `name` of the PMC or PPMC and the `mbox` of its development list. A project whose PMC has been dissolved refers to the Attic PMC instead |
| `release` | list | Every release of the project, current and archived, with the newest first |

Each object in `release` has the project's `name`, the version as `revision`, and the release date
as `created`.

The remaining project fields are present only where ATR has a value for them, and are never `null`:

| Field | Type | Contents |
| --- | --- | --- |
| `description` | string | A full description of the project |
| `shortdesc` | string | A one-line description |
| `homepage` | string | The URL of the project's website |
| `download-page` | string | The URL of the project's download page |
| `bug-database` | string | The URL of the project's issue tracker |
| `mailing-list` | string | The URL of the project's mailing list page |
| `repository` | list | The URLs of the project's source repositories |
| `programming-language` | list | The project's programming languages |
| `category` | list | The project's categories |

All dates are calendar dates in `YYYY-MM-DD` form, without a time.

`project.json` is the project's entry on its own, with the same fields.

## Package URLs

Every release in the catalog has a ***Package URL*** (***PURL***) of the form
`pkg:software-id/apache.org/<project_key>@<version>`. Replacing the `pkg:software-id/` prefix with
`https://` gives a working link. A bare link resolves to the release's `artifacts.json`, and adding a
query string, such as `?class=src&ext=tar.gz`, resolves to a single download. Using `latest` as the
version resolves to the project's newest release. The catalog explains the full set of qualifiers
on [its PURL page](https://release-catalog.apache.org/purl/).

A release's `artifacts.json` gives this PURL as `purl`. Where the release was also published to a
package ecosystem, and that distribution was recorded in ATR, `distributions` lists the package's
PURL there. Each entry has these fields:

| Field | Type | Contents |
| --- | --- | --- |
| `platform` | string | The platform's name, such as `Maven Central` |
| `purl` | string or null | The package in the platform's own PURL type, such as `pkg:maven/org.apache.dubbo/dubbo@3.3.7` |
| `web_url` | string or null | The package's page on the platform, where ATR has one |

* An entry names one package. A release that publishes several packages to a platform may not have
  them all listed.
* The version in a distribution's PURL is the package's own, which is not always the release's.
* A Docker Hub PURL refers to a tag, not to an image digest.
* Artifact Hub has no PURL type, so its entries have a `purl` of `null`.

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
moving a release to a different project, is done by the Tooling team. To report something that needs
to change, [open an issue](https://github.com/apache/tooling-trusted-releases/issues/new?template=catalog-data.yml),
or contact us on [dev@tooling.apache.org](mailto:dev@tooling.apache.org). To tell us that your
catalog is correct, use the mailing list. Once your PMC is satisfied, we mark the catalog as
reviewed and the notice is removed from your pages. For larger-scale corrections or agent-led reviews, we can provide
an export of your PMC's data as held by ATR.
