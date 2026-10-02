# 3. Projects

**Up**: [Documentation](.)

**Prev**: `2.1.` [Moving from the current process](moving-to-atr)

**Next**: `3.1.` [Project configuration](project-configuration)

**Pages**:

* `3.1.` [Project configuration](project-configuration)

**Sections**:

* [Introduction](#introduction)
* [Committee](#committee)
* [Project](#project)
* [Release](#release)
* [Revision](#revision)
* [Artifact](#artifact)
* [Creating and maintaining projects](#creating-and-maintaining-projects)
* [Maintaining committee keys](#maintaining-committee-keys)
* [Tutorial](#tutorial)

## Introduction

The Apache Trusted Releases (***ATR***) platform provides a standard and easy way for a Project Management Committee (***PMC***) or incubating project (***PPMC***)
to manage their releases in order to easily follow the Apache Way of governance. Most ASF documentation and systems use the following
interchangeable terms for PMCs: "Projects", Top Level Project (TLP), and "Podlings". For the ATR we felt it was important to make a distinction
between the people involved in the PMC and the software produced. The platform provides a way for a committee to manage their project's software
releases.

ATR models several kinds of component that correspond to organizational resources at the ASF, with some refinements of our own. We studied the
release patterns of PMCs with unusually demanding release models - Airflow, Maven, Commons, Sling, and others - before designing this system. The
sections below define the major terminology as it is used in ATR.

## Committee

A ***Committee*** in ATR is a PMC (Project Management Committee), or a PPMC (Podling Project Management Committee). The concept of a committee is
important in ATR because both its members and all release managers have elevated permissions compared to non-member and non-release-manager
committers. PMC members have [***binding release votes***](https://www.apache.org/legal/release-policy.html#release-approval) and can be ***Release Managers***; designated committers may be explicitly allowed to be
Release Managers too. Committee status is determined outside of the ATR system, by either the Board of Directors for PMCs or by the Incubator PMC
for PPMCs.

* Committees in ATR have one or more ***Projects***.
* The ***Roster*** shows the committers and PMC Members of the committee, and allows for designation of committers as Release Managers.
* ***Permissions*** shows whether the committee is allowed to build release artifacts in ***CI***.
* Each committee has a list of ***Signing Keys*** - the members' ***OpenPGP public keys*** - which may be used to sign artifacts.

## Project

A ***Project*** in ATR produces the software, or bundle of software, that a committee votes on. If your committee bundles several kinds of software
together for a single vote, that still counts as only one project in ATR: you pick one version number for the bundled release, though of course your
constituent software still has its own individual version numbers, and we plan for ATR to be made aware of those too.

We make a deliberate distinction between the committee and its software project(s). While most PMCs have a single project, some have several, and in
some cases a large number. ***Projects*** typically each have different repositories and release cycles within a PMC, and are often called
sub-projects; some have active releases on multiple versions at once. ATR can handle all of this structural diversity, so when using ATR it is
important for PMCs to acknowledge their *true number of projects*.

* Projects in ATR have one or more ***Releases***.
* Each project has [***Metadata***](project-configuration#metadata), which includes descriptions and various URLs; a download page URL is required.
* There are [***Security***](project-configuration#security) settings for each project.
* The project has a [***Lifecycle***](project-configuration#lifecycle) which can follow several version schemas and allow multiple active branches.
* There are project [***Compose***](project-configuration#compose), [***Vote***](project-configuration#vote), and [***Finish***](project-configuration#finish) policies.
* [***Lifecycle events***](archiving-releases#lifecycle-events) are generated for project releases.
* See [Trusted Publishing](trusted-publishing).

Projects are initially defined based on ***DOAP files*** and observed release activity. Once a PMC uses ATR, projects are defined both within the
platform and via [`.asf.yaml`](https://github.com/apache/infrastructure-asfyaml#atrsync). See [Project configuration](project-configuration) for the metadata fields and naming requirements. The tooling team
will work with PMCs to properly update their projects.

## Release

A ***Release*** in ATR is a specific version of software or bundle of software produced by a project. As mentioned in the project section above, bundled software must have an overall version number that may be different from the version numbers of the constituent software in the bundle. Also, we allow the release of more than one release concurrently, and we allow the release of prior versions (e.g. security patch level versions) after later versions.

* Releases in ATR have one or more ***Versions***.
* While a Release Candidate there may be multiple ***Revisions*** of the release.
* Once ***Released*** a version may be ***Archived***.

## Revision

A ***Revision*** is a snapshot of a release during the process of it being prepared by the release manager or release managers. Revisions can only be created before voting, in the ***Compose*** phase. In this phase it is possible, subject to certain constraints, to add files, move files, edit files, and delete files, and each such modification produces a new revision. This helps release managers to audit each other's activity, restore more easily after mistakes, and pin to a specific set for voting and final release without incurring races.

Revisions in ATR have one or more artifacts. See [Revisions](uploading-files#revisions) for how to view the revision history of a release and return to an earlier revision.

## Artifact

An ***Artifact*** is a file that has been uploaded by a release manager to a revision, and will constitute part of the release to be voted on, distributed, and officially announced. ATR will automatically check artifacts for adherence to as many ASF policies as we can automate checking for. It provides them for download during all phases before final release, and then will publish them through official channels during final release.

* Each release has one or more ***Artifacts*** one of which must be a ***Source*** artifact.
* Every artifact must have a detached ***Signature*** and ***Checksum***.
* ***SBOMs*** may be offered as artifacts or generated as additional artifact metadata.
* Various [Checks](checks) are run on the artifacts.

## Creating and maintaining projects

When a PMC first uses ATR, its projects are seeded from ***DOAP files*** and observed release activity, so the list is
usually already populated. Your first step is to check that list against your PMC's
[release catalog](https://release-catalog.apache.org) and confirm that it reflects your *true number of projects*.

If a project is missing, a committee member can add it using the ***Create project*** button under their committee in
the Committee directory, which opens the ***Add project*** form. You supply two
things: a project name in title case, to which ATR adds the "Apache" prefix for you, and a project key in lower case.
The key must be your committee key, or your committee key followed by a hyphen and a suffix - for example `example` or
`example-components` for the `example` committee. The key is what identifies the project in ATR URLs and API requests.
A key that starts with an existing project's key followed by a hyphen creates a sub-project of that project, and ATR
copies settings such as the description and release policy from the existing project once, when the new one is created.

Once a project exists, you maintain its metadata either in ATR or in the `project` block of your repository's
`.asf.yaml` file. See [Project configuration](project-configuration) for the fields, the naming rules, and how
synchronisation with `.asf.yaml` behaves. Structural corrections that ATR does not yet let you make yourself, such as
renaming, moving, or removing a project, are handled by the tooling team, so reach out to us for those.

## Maintaining committee keys

Release artifacts are signed with individual keys, as described in [Release managers](release-manager-setup) and
[Signing artifacts](signing-artifacts). Separately, each committee publishes a single `KEYS` file that gathers the
public keys of everyone who signs its releases, so that end users can verify those signatures. A key must be present in
the committee's `KEYS` file before you publish signatures made with it.

Committee members manage the committee's ***Signing Keys*** at [`/keys`](/keys), and choose how the `KEYS` file itself is
kept in step with ATR from the committee's page. There are three modes - ATR can own the file and regenerate it, ATR can
import changes made to the file in SVN (the default), or the committee can upload and commit it manually. See
[The KEYS file](moving-to-atr#the-keys-file) for what each mode does and when to use it.

## Tutorial

We offer an [ATR tutorial](/tutorial) for release managers, which walks through the compose, vote, and finish phases
with screenshots. Its [Projects section](/tutorial#projects) covers creating and configuring a project.
