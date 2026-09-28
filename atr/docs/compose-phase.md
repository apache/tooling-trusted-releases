# 5.1. Compose phase

**Up**: `5.` [Making releases](release-process-description)

**Prev**: `5.` [Making releases](release-process-description)

**Next**: `5.1.1.` [Uploading files](uploading-files)

**Pages**:

* `5.1.1.` [Uploading files](uploading-files)
* `5.1.2.` [Checks](checks)
* `5.1.3.` [License checks](license-checks)
* `5.1.4.` [SBOM workflows](sbom-workflows)

**Sections**:

* [Overview](#overview)
* [Responsibilities](#responsibilities)
* [How files reach ATR](#how-files-reach-atr)
* [Checks and compliance](#checks-and-compliance)

## Overview

The ***compose*** phase is where a ***Release Candidate*** is built up from its parts. The
***Release Manager*** assembles the ***Release Artifacts***, uploads them to ATR, and runs the
platform's checks against them. Composing is deliberately iterative: the Release Manager can add,
replace, and remove artifacts, and re-run the checks, until the candidate is ready to put to a
vote. The candidate is held in ATR throughout, and nothing is committed to the Apache
distribution servers until the [finish phase](finish-phase).

For a step by step walkthrough of this phase with screenshots, see the
[Compose section of the tutorial](/tutorial#compose). You need to be logged in to ATR to view it.

## Responsibilities

The compose phase is mostly the work of the Release Manager, on behalf of the PMC:

* Assemble the release artifacts and upload them to the candidate. The ways of doing so are
  listed under [How files reach ATR](#how-files-reach-atr).
* Sign each artifact and register the corresponding key, so that ATR and, later, end users can
  verify the signatures. See [Signing artifacts](signing-artifacts), and
  [Release managers](release-manager-setup) for the keys and access methods available to you.
* Run the checks and resolve anything they report before starting a vote.

Other PMC members are not required to act during compose, but the candidate is visible to them,
and it is good practice to review it before it goes to a vote.

## How files reach ATR

There are many ways to get files into a candidate:

* upload through the [browser](uploading-files#browser),
* upload over [rsync](uploading-files#rsync),
* [import](uploading-files#svn-import) from the committee's `dist/dev` area in SVN,
* upload with [`atr` CLI](uploading-files#command-line-client)
  ([Trusted Releases Client](https://github.com/apache/tooling-releases-client)),
* upload with [ATR Maven Plugin](uploading-files#maven-plugin),
* upload from a [GitHub Actions workflow](uploading-files#github-actions) using
  [Trusted Publishing](trusted-publishing).

[Uploading files](uploading-files) gives the commands and setup for each of these, and explains
how each change to a candidate is recorded as a [revision](uploading-files#revisions).

The SVN `dist/dev` import is one option among others, and we expect most release managers not to
need it. See [The earlier dist/dev workflow](staging-and-voting#the-earlier-distdev-workflow) for
the background.

## Checks and compliance

ATR runs a number of automated checks over the composed artifacts to catch problems early, while
they are still cheap to fix. These cover the integrity of the artifacts, their licensing, and the
software bill of materials where one is provided:

* [Checks](checks) - the checks ATR runs and how to read their results.
* [License checks](license-checks) - how ATR helps you follow ASF licensing policy.
* [SBOM workflows](sbom-workflows) - generating and attaching a software bill of materials.

Most check results are there to help you produce a candidate that the PMC can vote on with
confidence, and you can keep iterating whatever they report. A ***blocker***, though, means that a
mandatory policy condition has been violated, and the candidate cannot proceed to a vote until it
is resolved.
