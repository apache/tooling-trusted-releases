# 2.1. Components

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

ATR knows about several kinds of components that correspond to organizational resources at the ASF, but some of the components mean a slightly different thing in ATR or are customized in some way. This page documents the major things to know about, and what's special about them.

The way that ATR models components has the effect of incorporating extremes such as Airflow, Maven, Commons, Sling, etc. We studied the release patterns before designing this system, and we plan eventually to catalog all current project releases in a new system adjacent to or included within ATR.

## Committee

A **committee** in ATR is a PMC (Project Management Committee), or a PPMC (Podling Project Management Committee). The concept of a committee is important in ATR because both its members and all release managers have elevated permissions compared to non-member and non-release-manager committers.

Committees in ATR have one or more projects.

## Project

A **project** in ATR produces software or bundle of software that a committee votes on. This means that if your committee bundles several kinds of software together for a vote, that still counts as only one project in ATR. You must pick a version number for the "bundled" software in such a case, but of course your constituent software still has its own individual version numbers and we plan for ATR to be made aware of those too.

Projects in ATR have one or more releases.

## Release

A **release** in ATR is a specific version of software or bundle of software produced by a project. As mentioned in the project section above, bundled software must have an overall version number that may be different from the version numbers of the constituent software in the bundle. Also, we allow the release of more than one release concurrently, and we allow the release of prior versions (e.g. security patch level versions) after later versions.

Releases in ATR have one or more revisions.

## Revision

A **revision** is a snapshot of a release during the process of it being prepared by the release manager or release managers. There are two preparation phases: before and after voting. The phase before voting is called the _compose_ phase and the phase after voting is called the _finish_ phase. In these phases it is possible, subject to certain constraints (many more after voting than before), to add files, move files, edit files, and delete files, and each such modification produces a new revision. This helps release managers to audit each other's activity, restore more easily after mistakes, and pin to a specific set for voting and final release without incurring races.

Revisions in ATR have one or more artifacts.

## Artifact

An **artifact** is a file that has been uploaded by a release manager to a revision, and will constitute part of the release to be voted on, distributed, and officially announced. ATR will automatically check artifacts for adherence to as many ASF policies as we can automate checking for. It provides them for download during all phases before final release, and then will publish them through official channels during final release. At the moment, in alpha phase (as of January 2026), we do not yet do publication, and this must be done manually by a release manager.
