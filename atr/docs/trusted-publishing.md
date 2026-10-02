# 4.2. Trusted Publishing

**Up**: `4.` [Release managers](release-manager-setup)

**Prev**: `4.1.` [Signing artifacts](signing-artifacts)

**Next**: `5.` [Making releases](release-process-description)

**Sections**:

* [Overview](#overview)
* [What reproducible means](#what-reproducible-means)
* [How to set up Trusted Publishing](#how-to-set-up-trusted-publishing)
* [Configuring repository and workflow paths](#configuring-repository-and-workflow-paths)
* [GitHub Actions for ATR](#github-actions-for-atr)
* [How ATR detects automated release keys](#how-atr-detects-automated-release-keys)

## Overview

Trusted Publishing lets a project sign release artifacts automatically during a GitHub Actions workflow rather than requiring a release manager to sign each file locally. This is available to projects that can demonstrate reproducible builds, meaning that anyone can independently rebuild the artifacts from the same source and obtain identical results. The ASF uses Trusted Publishing to strengthen supply chain integrity for projects that meet this requirement.

The process involves creating a dedicated GPG signing key for the project, storing it as a GitHub repository secret, and registering the public half with ATR. When ATR sees a signature made by a key that follows the automated release key naming convention, it accepts the signature in the same way that it would accept one from an individual committer's key.

## What reproducible means

The ASF Security team describes a build as [reproducible](https://cwiki.apache.org/confluence/display/SECURITY/Reproducible+Builds) when the build process is so deterministic that building the same sources twice, by different people, results in a bit-by-bit identical artifact. When two builds on independently managed infrastructure produce the same bits, it improves confidence that nothing was injected into the artifact by a compromise of either one, which is what allows a signature made in CI to be trusted.

In practice, this means your artifacts must not contain anything that depends on the environment they were built in. Usernames, hostnames, timestamps, absolute file paths, tool versions, and CI build numbers are the usual culprits. The Security team's page has guidance for several ecosystems, including Maven, Python, Helm, and tarballs, for example setting `SOURCE_DATE_EPOCH` so that file timestamps are fixed.

It is good practice for every ASF release to be reproducible, but for projects that want to build and sign artifacts in CI it is required. Being able to reproduce a build once is not quite enough. Your project has to show three things:

1. Independent builds of the same source produce bit-by-bit identical artifacts.
2. The release process documents how and when artifacts are independently rebuilt and verified.
3. That process is followed in practice for every release.

If you have questions about making your build reproducible, ask on the [security-discuss](https://security.apache.org/mailinglist/) mailing list, or in the `#security-discuss` channel on the [ASF Slack](https://infra.apache.org/slack.html).

## How to set up Trusted Publishing

ATR Trusted Publishing is built on top of [Automated Release Signing](https://infra.apache.org/release-signing.html#automated-release-signing), which permits signing in CI provided that all signed artifacts can be built reproducibly, that CI deploys them to a staging area rather than straight to users, and that every artifact is rebuilt and compared on trusted hardware before it is published. ATR is the staging area in this model, and the vote is where the comparison happens. The steps below take you from a reproducible build to a workflow that ATR will accept.

### Step 1: Get approval from the Security team

Tell the ASF Security team that your project intends to request a CI signing key, and show them that your builds are reproducible, as described [above](#what-reproducible-means). Your request should highlight the change to your release process that makes sure artifacts are validated on trusted hardware before release, i.e. who rebuilds the artifacts, where, and how they compare the result against what CI produced. Infrastructure defines trusted hardware as secure hardware under the direct control of the release manager, so the rebuild can't happen on GitHub Actions. The Security team should approve your workflow before you put it into use.

### Step 2: Request a project signing key

Open a Jira ticket with ASF Infrastructure asking for a signing key. Infrastructure generates the key and keeps the private half, and the public key is either sent to your project or added to your `KEYS` file for you. Infrastructure's [automated release signing](https://infra.apache.org/release-signing.html#automated-release-signing) documentation describes the key and how it is held.

The key must follow a specific naming convention for ATR to recognise it as an automated release key, so it is worth asking for this in your ticket. The primary UID must contain "Automated Release Signing" or the deprecated "Services RM", ignoring case, and the email address must be `private@`_committee_`.apache.org`, where _committee_ is the name of your PMC. For example, the following UID would be valid for a project named Example:

```text
Example Automated Release Signing <private@example.apache.org>
```

If the UID does not follow this pattern, ATR will not recognise the key as automated and your committee will not be eligible for Trusted Publishing.

### Step 3: Configure the GitHub repository

Although your project never sees the private key, Infrastructure makes it available to your CI system. For GitHub Actions, ask Infrastructure to store it as a repository secret in your project's GitHub repository. Your workflows can then reference this secret to sign artifacts during the build. The public half stays with ATR and your `KEYS` file.

### Step 4: Add the public key to your `KEYS` file

If Infrastructure has not already done so, add the public key to your committee's `KEYS` file. This is the same `KEYS` file that holds committer signing keys, and it is kept in step with ATR as described in [The KEYS file](moving-to-atr#the-keys-file). Import the updated `KEYS` file through ATR, or commit it in SVN if your committee's file is managed there, rather than adding this key with the individual OpenPGP key form. ATR will parse the UID from the key and, because it has no ASF UID tied to an individual, will match it by its email address during signature verification instead.

### Step 5: Check that your committee can use CI

Once the key is in ATR, your committee becomes eligible for Trusted Publishing. You can check this on your committee's page in ATR, where the Permissions card should show _CI builds: Enabled_. If it still shows _Disabled_, the key is probably not linked to your committee, or its UID does not follow the naming convention in step 2. See [how ATR detects automated release keys](#how-atr-detects-automated-release-keys) for the exact rules.

Eligibility alone does not let any workflow act on your releases. You also have to tell ATR which repository and which workflows it should trust, as described in [configuring repository and workflow paths](#configuring-repository-and-workflow-paths).

### Step 6: Sign and upload artifacts in your workflow

In your GitHub Actions workflow, sign your release artifacts using the private key from the repository secret. The resulting `.asc` signature files should be uploaded to ATR alongside the artifacts, the same way that manually signed artifacts would be. The ASF Tooling team provides [GitHub Actions](#github-actions-for-atr) to do the upload, and the later release steps, for you.

### Step 7: Confirm reproducibility during the vote

When the project starts a release vote, the artifacts must be independently rebuilt from source on trusted hardware and confirmed to be bit-by-bit identical to the ones uploaded to ATR, following the process your project documented in step 1. This has to happen before the release is published. This is the trust model behind Trusted Publishing: the automated signature proves that the artifacts came from a specific GitHub workflow, and the independent rebuild proves that the build output is genuine, matching what was built on the GitHub runners.

## Configuring repository and workflow paths

For ATR to accept release operations that a GitHub Actions workflow performs on the project's behalf, it has to know which repository the workflow runs in, and which workflows are permitted to perform the operations of each phase of a release. You configure this in the project's release policy, under the Trusted Publishing tab of the project settings.

There are three groups of settings.

### Repository name

The name of the project's GitHub repository, without the `apache/` prefix. For example, if the repository is `apache/example`, enter `example`. The name must not contain a slash. You have to set this before any workflow path will be accepted, because ATR matches the repository named in the GitHub token against this value.

### Repository branch

The branch that release builds run from, for example `main` or `2.5.x`. This is optional, but if you do set a branch you must also set a repository name.

### Workflow paths

There is a separate field for each phase of a release: compose, vote, and finish. Each field lists the workflows that ATR will accept as performing that phase's operations. List one workflow path per line, and start each path with `.github/workflows/`. For example, the compose field might contain:

```text
.github/workflows/release-compose.yml
.github/workflows/release-compose-rc.yml
```

A field can hold more than one path, so you can list several workflows for a single phase. Any path that does not begin with `.github/workflows/` is rejected when you save the form.

The three fields are kept separate so that a workflow is only trusted for the phase it is registered against. A workflow listed under compose can compose a candidate, but it cannot, for instance, finish a release unless it is also listed under finish.

### How ATR matches a workflow

When a workflow calls one of the Trusted Publishing endpoints, GitHub sends ATR an OIDC token that names the repository, such as `apache/example`, and the workflow reference, such as `apache/example/.github/workflows/release-compose.yml@refs/heads/main`. ATR strips the `apache/` prefix and the trailing `@` git ref, then looks for a project whose release policy has a matching repository name and lists that workflow path under the phase being requested. If there is no match, the request is refused. The committee must also be eligible for Trusted Publishing, as described below.

## GitHub Actions for ATR

The ASF Tooling team maintains GitHub Actions in [apache/tooling-actions](https://github.com/apache/tooling-actions) that call ATR on your workflow's behalf. Each action requests a GitHub OIDC token, which ATR uses to identify the repository and workflow, so you don't need to store any ATR credentials in your repository.

| Action | What it does | Workflow path field |
| --- | --- | --- |
| [`upload-to-atr`](https://github.com/apache/tooling-actions/tree/main/upload-to-atr) | Uploads a directory of artifacts and signatures into a draft | Compose |
| [`release-on-atr`](https://github.com/apache/tooling-actions/tree/main/release-on-atr) | Resolves the vote, announces the release, or both | Vote to resolve, finish to announce |
| [`record-atr-distribution`](https://github.com/apache/tooling-actions/tree/main/record-atr-distribution) | Records a distribution made to an external platform such as PyPI (experimental) | Finish or compose |

The workflow path field column shows which of the [workflow paths](#workflow-paths) your workflow has to be listed under for ATR to accept the call. A workflow that both resolves a vote and announces the release, for example, must be listed under both vote and finish.

There are a few things to be aware of when using these actions:

* The actions are not tagged, so you must refer to each one by a full commit hash, e.g. `apache/tooling-actions/upload-to-atr@<commit>`, and not by `@main`.
* The job must have the `id-token: write` permission, otherwise the action can't request an OIDC token.
* The workflow must run on a GitHub-hosted runner. ATR rejects tokens from self-hosted runners.
* The workflow must be triggered by a committer whose GitHub account is linked to their ASF account, as ATR records the release operations against that committer.
* `upload-to-atr` uploads the `dist` directory by default, which you can change with the `src` input. It also sends ATR the commit that the artifacts were built from, which you can override with the `source-commit` input.

Each action's README has the full list of inputs and some example workflows. When you create a draft release in ATR, the upload page also shows an example workflow already filled in with your project name.

## How ATR detects automated release keys

ATR identifies automated release keys in two ways, at two different levels.

### Signature verification

When ATR verifies an `.asc` signature file, it loads all public signing keys that are linked to the release committee and checks each one. For personal committer keys, the key has an ASF UID field in ATR behind the scenes that ties it to a specific ASF account. Automated project keys do not have an ASF UID because they belong to the project rather than to a person. Instead, ATR checks the key's primary UID against the automated release key naming convention: the UID must contain "Automated Release Signing" or "Services RM" (ignoring case), and its email address must be exactly `private@`_committee_`.apache.org`. A key following the convention acts as a kind of committee key. A signature made by either kind of key, i.e. personal with an ASF UID or a committee key following the naming convention, will pass signature verification.

You can read more about [signature verification](checks#signature-verification) on the checks page.

### Committee eligibility

Separately, ATR determines which committees are eligible for Trusted Publishing by querying for keys whose primary UID contains "Automated Release Signing" or "Services RM" (ignoring case) and whose email is exactly `private@`_committee_`.apache.org` for the committee that the key is linked to. A committee must have at least one such key before ATR will accept releases triggered by GitHub workflows for projects in that committee.

Registering a correctly named key therefore does two things at once: it enables signature verification for artifacts signed by that key, and it marks the committee as eligible for Trusted Publishing.
