# 2.1. Terminology

**Up**: `2.` [User guide](user-guide)

**Prev**: (none)

**Next**: `2.2.` [Signing artifacts](signing-artifacts)

**Sections**:

* [Introduction](#introduction)
* [Committee](#committee)
* [Project](#project)
* [Release](#release)
* [Revision](#revision)
* [Artifact](#artifact)

## Introduction

ATR knows about several kinds of components that correspond to organizational resources at the ASF, but for some of these components we make refinements. This page documents the major terminology as used in ATR.

The way that ATR models components has allowed us to incorporate some extreme release models of certain PMCs like Airflow, Maven, Commons, Sling, etc. We studied the release patterns before designing this system.

## Committee

A ***Committee*** in ATR is a PMC (Project Management Committee), or a PPMC (Podling Project Management Committee). The concept of a committee is important in ATR because both its members and all release managers have elevated permissions compared to non-member and non-release-manager committers.

* Committees in ATR have one or more ***Projects***.
* The ***Roster*** shows the committers and PMC Members of the committee and allows for designation of committers as Release Managers
* ***Permissions*** shows if the Committee is allowed to build release artifacts in ***CI***.
* Each committee has a list of ***Signing Keys*** which may be used to sign artifacts.

## Project

A ***Project*** in ATR produces software or bundle of software that a committee votes on. This means that if your committee bundles several kinds of software together for a vote, that still counts as only one project in ATR. You must pick a version number for the "bundled" software in such a case, but of course your constituent software still has its own individual version numbers and we plan for ATR to be made aware of those too.

* Projects in ATR have one or more ***Releases***.
* Each project has ***Metadata*** which includes descriptions and various urls. A download page url is required.
* There are ***Security*** settings for each project.
* The project has a ***Lifecycle*** which can follow several version schemas and allow multiple active branches.
* See [***Trusted Publishing***](trusted-publishing).
* There are project ***Compose***, ***Vote***, and ***Finish*** policies.
* ***Common Lifecycle Events*** are generated for project releases.

## Release

A ***Release*** in ATR is a specific version of software or bundle of software produced by a project. As mentioned in the project section above, bundled software must have an overall version number that may be different from the version numbers of the constituent software in the bundle. Also, we allow the release of more than one release concurrently, and we allow the release of prior versions (e.g. security patch level versions) after later versions.

* Releases in ATR have one or more ***Versions***.
* While a Release Candidate there may be multiple ***Revisions*** of the release.
* Once ***Released*** a version may be ***Archived***.

## Revision

A ***Revision*** is a snapshot of a release during the process of it being prepared by the release manager or release managers. Revisions are only accessible before voting in the ***Compose*** phase. In this phase it is possible, subject to certain constraints, to add files, move files, edit files, and delete files, and each such modification produces a new revision. This helps release managers to audit each other's activity, restore more easily after mistakes, and pin to a specific set for voting and final release without incurring races.

Revisions in ATR have one or more artifacts.

## Artifact

An ***Artifact*** is a file that has been uploaded by a release manager to a revision, and will constitute part of the release to be voted on, distributed, and officially announced. ATR will automatically check artifacts for adherence to as many ASF policies as we can automate checking for. It provides them for download during all phases before final release, and then will publish them through official channels during final release.

* Each release has one or more ***Artifacts*** one of which must be a ***Source*** artifact.
* Every artifact must have a detached ***Signature*** and ***Checksum***.
* ***SBOMs*** may be offered as artifacts or generated as additional artifact metadata.
* Various [***Checks***](checks) are run on the artifacts.
