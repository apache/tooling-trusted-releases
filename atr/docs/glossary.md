# 8. Glossary

**Up**: [Documentation](.)

**Prev**: `7.22.` [File handling](file-handling)

**Next**: (none)

**Sections**:

* [Overview](#overview)
* [Committees and people](#committees-and-people)
* [Projects and releases](#projects-and-releases)
* [Making releases](#making-releases)
* [Keys and credentials](#keys-and-credentials)

## Overview

Short definitions of the terms used across this documentation, grouped by area. Each links to the
page that covers it in full.

## Committees and people

* **Committee** - a PMC, or a PPMC for a podling. Committee membership is decided outside ATR. See
  [Committee](projects#committee).
* **PMC** - Project Management Committee, the body that oversees a top level project and votes on
  its releases.
* **PPMC** - Podling Project Management Committee, the committee of a project in the Apache
  Incubator. See [Podling releases](podling-releases).
* **Podling** - a project in the Apache Incubator. Its releases are voted on twice, by the PPMC and
  then by the Incubator PMC.
* **Roster** - the committers and PMC members of a committee, where committers can be designated as
  release managers.
* **Release manager** - a person who guides a candidate through the compose, vote, and finish
  phases. See [Release manager setup](release-manager-setup).

## Projects and releases

* **Project** - the software, or bundle of software, that a committee votes on. A committee has one
  or more projects. See [Project](projects#project).
* **Release** - a specific version of a project's software. See [Release](projects#release).
* **Release candidate** - a release that has not yet been voted on and published.
* **Revision** - a snapshot of a release candidate during the compose phase. Every change to the
  candidate's files makes a new revision. See [Revision](projects#revision).
* **Artifact** - a file in a release, such as a source archive, a binary, or an SBOM. See
  [Artifact](projects#artifact).
* **Cycle** - a line of releases maintained together, such as all `2.x` releases. See
  [Lifecycle](project-configuration#lifecycle).
* **Version scheme** - how a project numbers its releases and groups them into cycles.
* **Lifecycle event** - a published record of a change in a release's status, such as end of
  distribution. See [Lifecycle events](archiving-and-lifecycle#lifecycle-events).
* **Archived** - a superseded release whose files have been removed from the distribution area but
  remain on archive.apache.org. See [Archiving and lifecycle](archiving-and-lifecycle).
* **Catalog** - the public list of every ASF release, current and archived. See
  [Release catalog](release-catalog).

## Making releases

* **Compose phase** - where a release candidate is assembled and checked. See
  [Compose phase](compose-phase).
* **Vote phase** - where the PMC decides whether the candidate becomes a release. See
  [Vote phase](vote-phase).
* **Finish phase** - where an approved candidate is published and announced. See
  [Finish phase](finish-phase).
* **Check** - an automated test that ATR runs on the artifacts in a revision. See [Checks](checks).
* **Blocker** - a check result reporting a violation of mandatory policy, which stops the candidate
  going to a vote.
* **SBOM** - software bill of materials, a list of the components in an artifact. See
  [SBOM workflows](sbom-workflows).
* **Contingent Approval** - a vote held through the Contingent Approval Portal (CAP), used to
  approve retiring a project. See [Retiring a project](archiving-and-lifecycle#retiring-a-project).

## Keys and credentials

* **Signing key** - an OpenPGP key used to sign release artifacts. A committee's signing keys are
  its members' public keys. See [Signing artifacts](signing-artifacts).
* **PAT** - Personal Access Token, a credential that identifies you to ATR's API and command line
  tooling. See [Access credentials](release-manager-setup#access-credentials).
* **JWT** - JSON Web Token, a short-lived credential issued in exchange for a PAT.
* **Trusted Publishing** - signing release artifacts automatically in a GitHub Actions workflow,
  available to projects with reproducible builds. See [Trusted Publishing](trusted-publishing).
* **PURL** - Package URL, a standard identifier given to every release in the catalog. See
  [Package URLs](release-catalog#package-urls).
