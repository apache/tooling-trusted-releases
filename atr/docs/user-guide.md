# 2. User guide

**Up**: [Documentation](.)

**Prev**: `1.` [Introduction to ATR](introduction-to-atr)

**Next**: `3.` [Developer guide](developer-guide)

**Pages**:

* `2.1.` [Terminology](terminology)
* `2.2.` [Signing artifacts](signing-artifacts)
* `2.3.` [Checks](checks)
* `2.4.` [License checks](license-checks)
* `2.5.` [Trusted Publishing](trusted-publishing)
* `2.6.` [SBOM workflows](sbom-workflows)
* `2.7.` [Staging and voting](staging-and-voting)
* `2.8.` [Promoting to release](promoting-to-release)

**Sections**:

* [Introduction](#introduction)
* [Committee](#committee)
* [Project](#project)
* [Releases](#releases)
* [Catalog](#catalog)
* [Tutorial](#tutorial)

## Introduction

The Apache Trusted Releases (***ATR***) provides a standard and easy way for a Project Management Committee (***PMC***) or incubating project (***PPMC***)
to manage their releases in order to easily follow the Apache Way of governance. Most ASF documentation and systems use the following
interchangable terms for PMCs: "Projects", Top Level Project (TLP), and "Podlings". For the ATR we felt it was important to make a distinction
between the people involved in the PMC and the software produced. The platform provides a way for a committee to manage their project's software
releases.

## Committee

Within the ATR platform when we describe PMCs as ***Committees***. We refer to the committee's ***Roster*** as having ***PMC Members*** and ***Committers***. Each of
these individuals may have assigned ***GPG public keys*** to the PMC. PMC members have ***binding release votes*** and can be ***Release Managers***.
Designated committers may be explicitly allowed to be Release Mangers.

Committee status is determined outside of the ATR system by either the Board of Directors for PMCs or by the Incubator PMC for PPMCs.

## Project

Within the ATR platform we make a distinction between the committee and its software project(s). While most PMCs have a single project some have
multiple projects and in some cases a large number. ***Projects*** typically each have different repositories and release cycles within a PMC.
Often these are called sub-projects. Some projects have active releases on multiple versions. ATR can handle all of this structural diversity.
When using ATR it will be important for PMCs to acknowledge their *true number of projects*.

Projects are initially defined based on ***DOAP files*** and observed release activity. Once a PMC uses the ATR then projects are defined both within
the platform and via `.asf.yaml`. The tooling team will work with PMCs to properly update their projects.

## Releases

The ASF releases open source software. The ATR platform helps PMCs release their project's artifacts through several stages:

1. ***Candidate***. Guiding a ***Release Candidate*** through the phases of ASF Governance to make a Release is the primary purpose of the ATR platform.
   * ***Compose***. In this phase ***Release Artifacts*** are assembled and checked for compliance.
     The Release Manager can iterate on the artifacts until the Candidate is ready.
   * ***Vote***. In this phase the PMC votes on the release and approves (or not). Votes may be canceled. Once the vote passes it is ready to publish.
     The artifacts being voted on remain in ATR for the duration of the vote, and vote emails should link to them there. See [Staging and voting](staging-and-voting).
   * ***Finish***. In this phase the Release Artifacts are committed to the Apache distribution SVN repository and announcement is deferred until the artifacts have made it
     onto the download servers. The announcement is sent and the artifacts published to the Release Catalog. During Alpha the commit goes to `svn:dist:atr` and a
     manual move is needed to publish, as described in [Promoting to release](promoting-to-release).
2. ***Released***. Releases that are released are active and cataloged and available via both a standard catalog api and at the proper urls using the CDN and download servers.
   Releases that use the current legacy methods skipping the ATR are also cataloged,
3. ***Archived***. All cataloged releases may be archived using the ATR platform or by direct removal from `svn:dist:release`.

## Catalog

ATR includes a ***Catalog*** of all active and archived release artifacts with a set url pattern that includes the ***Project key***, ***Release version***, and
***Artifact name***. Using this pattern on an endpoint using curl the ASF's preferred urls for a release's artifacts and metadata are obtained.

## Tutorial

We offer an [ATR tutorial](/tutorial).
