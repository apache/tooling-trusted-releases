# 4.2. Uploads from GitHub Actions

**Up**: `4.` [Release manager setup](release-manager-setup)

**Prev**: `4.1.` [Signing artifacts](signing-artifacts)

**Next**: `5.` [Making releases](making-releases)

**Sections**:

* [Overview](#overview)
* [What reproducible means](#what-reproducible-means)
* [How to set up uploads from GitHub Actions](#how-to-set-up-uploads-from-github-actions)
* [What ATR checks](#what-atr-checks)
* [Restricting which runs can upload](#restricting-which-runs-can-upload)
* [Protecting the branch or tag](#protecting-the-branch-or-tag)
* [The upload action](#the-upload-action)
* [After the vote](#after-the-vote)
* [How ATR detects automated release keys](#how-atr-detects-automated-release-keys)

## Overview

A project can build and sign its release artifacts in a GitHub Actions workflow and have the
workflow upload them into a draft in ATR, rather than a release manager signing and uploading each
file by hand. This is available to projects that can demonstrate reproducible builds, meaning that
anyone can independently rebuild the artifacts from the same source and obtain identical results.

There are two things to set up. The project needs a dedicated signing key, held by ASF
Infrastructure and made available to the workflow, and the PMC has to allow uploads from GitHub
Actions in ATR. After that, a workflow in one of the project's repositories can upload to a draft
when a PMC member or release manager starts it. The workflow stores no ATR credentials. It
identifies itself with a token that GitHub issues for the run.

A workflow upload is not reviewed on its way in. The vote is where the files are checked, and ATR
marks revisions that a workflow made so that voters can see where they came from.

## What reproducible means

The ASF Security team describes a build as
[reproducible](https://cwiki.apache.org/confluence/display/SECURITY/Reproducible+Builds) when the
build process is so deterministic that building the same sources twice, by different people, results
in a bit-by-bit identical artifact. When two builds on independently managed infrastructure produce
the same bits, it improves confidence that nothing was injected into the artifact by a compromise of
either one, which is what allows a signature made in CI to be trusted.

In practice, this means your artifacts must not contain anything that depends on the environment
they were built in. Usernames, hostnames, timestamps, absolute file paths, tool versions, and CI
build numbers are the usual culprits. The Security team's page has guidance for several ecosystems,
including Maven, Python, Helm, and tarballs, for example setting `SOURCE_DATE_EPOCH` so that file
timestamps are fixed.

It is good practice for every ASF release to be reproducible, but for projects that want to build
and sign artifacts in CI it is required. Being able to reproduce a build once is not quite enough.
Your project has to show three things:

1. Independent builds of the same source produce bit-by-bit identical artifacts.
2. The release process documents how and when artifacts are independently rebuilt and verified.
3. That process is followed in practice for every release.

If you have questions about making your build reproducible, ask on the
[security-discuss](https://security.apache.org/mailinglist/) mailing list, or in the
`#security-discuss` channel on the [ASF Slack](https://infra.apache.org/slack.html).

## How to set up uploads from GitHub Actions

Uploads from GitHub Actions are built on top of
[Automated Release Signing](https://infra.apache.org/release-signing.html#automated-release-signing),
which permits signing in CI provided that all signed artifacts can be built reproducibly, that CI
deploys them to a staging area rather than straight to users, and that every artifact is rebuilt and
compared on trusted hardware before it is published. ATR is the staging area in this model, and the
vote is where the comparison happens. The steps below take you from a reproducible build to a
workflow that ATR will accept.

### Step 1: Get approval from the Security team

Tell the ASF Security team that your project intends to request a CI signing key, and show them that
your builds are reproducible, as described [above](#what-reproducible-means). Your request should
highlight the change to your release process that makes sure artifacts are validated on trusted
hardware before release, i.e. who rebuilds the artifacts, where, and how they compare the result
against what CI produced. Infrastructure defines trusted hardware as secure hardware under the
direct control of the release manager, so the rebuild can't happen on GitHub Actions. The Security
team should approve your workflow before you put it into use.

### Step 2: Request a project signing key

Open a Jira ticket with ASF Infrastructure asking for a signing key. Infrastructure generates the
key and keeps the private half, and the public key is either sent to your project or added to your
`KEYS` file for you. Infrastructure's
[automated release signing](https://infra.apache.org/release-signing.html#automated-release-signing)
documentation describes the key and how it is held.

The key must follow a specific naming convention for ATR to recognise it as an automated release
key, so it is worth asking for this in your ticket. The primary UID must contain "Automated Release
Signing" or the deprecated "Services RM", ignoring case, and the email address must be
`private@`_committee_`.apache.org`, where _committee_ is the name of your PMC. For example, the
following UID would be valid for a project named Example:

```text
Example Automated Release Signing <private@example.apache.org>
```

If the UID does not follow this pattern, ATR will not recognise the key as automated and your PMC
will not be able to allow uploads from GitHub Actions.

### Step 3: Configure the GitHub repository

Although your project never sees the private key, Infrastructure makes it available to your CI
system. For GitHub Actions, ask Infrastructure to store it as a repository secret in your project's
GitHub repository. Your workflows can then reference this secret to sign artifacts during the build.
The public half stays with ATR and your `KEYS` file.

### Step 4: Add the public key to your `KEYS` file

If Infrastructure has not already done so, add the public key to your committee's `KEYS` file. This
is the same `KEYS` file that holds committer signing keys, and it is kept in step with ATR as
described in [The KEYS file](moving-to-atr#the-keys-file). Import the updated `KEYS` file through
ATR, or commit it in SVN if your committee's file is managed there, rather than adding this key with
the individual OpenPGP key form. ATR will parse the UID from the key and, because it has no ASF UID
tied to an individual, will match it by its email address during signature verification instead.

### Step 5: Allow uploads on the committee page

Once the key is in ATR, a PMC member can tick _Allow uploads from GitHub Actions_ in the Permissions
card on your committee's page and save. The setting covers every project of the committee. ATR
records the change and sends a notice to the PMC's private list, listing the repositories that can
now upload to each project.

If the box can't be ticked, the page says that the committee has no automated release signing key.
The key is probably not linked to your committee, or its UID does not follow the naming convention
in step 2. See [how ATR detects automated release keys](#how-atr-detects-automated-release-keys)
for the exact rules. If the key is later removed, uploads stop until there is one again.

### Step 6: List the repository on the project

A workflow can only upload to a project from a repository that the project lists. The list is the
_Repositories_ field of the project's reference metadata, on the project page. ATR counts an entry
when it is a `https://github.com/apache/...` address, and the page shows which entries it
recognises. If your release workflow lives somewhere other than the source repository, add that
repository to the list as well.

While the PMC allows uploads from GitHub Actions, a change to this list is recorded and a notice is
sent to the PMC's private list.

### Step 7: Sign and upload artifacts in your workflow

In your GitHub Actions workflow, sign your release artifacts using the private key from the
repository secret. The resulting `.asc` signature files should be uploaded to ATR alongside the
artifacts, the same way that manually signed artifacts would be. The ASF Tooling team provides
[an action](#the-upload-action) to do the upload for you. The workflow says which project it is
uploading to.

### Step 8: Confirm reproducibility during the vote

When the project starts a release vote, the artifacts must be independently rebuilt from source on
trusted hardware and confirmed to be bit-by-bit identical to the ones uploaded to ATR, following the
process your project documented in step 1. This has to happen before the release is published. The
automated signature shows that the artifacts came from a GitHub workflow, and the independent
rebuild shows that the build output is genuine, matching what was built on the GitHub runners.

## What ATR checks

When a workflow asks to upload, GitHub gives it an OIDC token that says which repository and run it
is and who started it. ATR accepts the upload when all of these hold:

* The workflow runs on a GitHub-hosted runner in the `apache` organisation.
* The run was triggered by a `push`, `release` or `workflow_dispatch` event. Other events, such as
  `pull_request` or `schedule`, are refused.
* The project given by the workflow exists and is not archived.
* The project's PMC allows uploads from GitHub Actions, and has an automated release signing key.
* The repository the workflow runs in is on the project's
  [repository list](#step-6-list-the-repository-on-the-project). The comparison ignores case.
* The person who started the run is a member of the PMC or one of its release managers, and their
  GitHub account is linked to their ASF account. ATR records the upload against that person.
* The run is a first attempt. GitHub keeps the original person on a re-run and doesn't say who
  re-ran it, so a re-run is refused. Start a new run of the workflow instead.
* The run is inside the project's
  [GitHub OIDC restrictions](#restricting-which-runs-can-upload), if its PMC has set any.

When any of these fails, the response says which one, and who can change it.

A workflow can't create, upload to, or download an embargoed release. ATR answers as it would for a
release that has already left its draft, so a workflow can't tell an embargoed release from a
finished one.

## Restricting which runs can upload

With nothing more set, any workflow on any branch or tag of a listed repository can upload. A PMC
member can narrow that for a project under _Advanced settings_, which is linked from the Actions
card on the project page. The settings there are the project's GitHub OIDC restrictions, after the
OIDC token that a workflow presents when it uploads. All of them are optional:

* **Workflow paths.** Only these workflow files can upload, for example
  `.github/workflows/release.yml`. The path has to match in full.
* **Branches or tags.** Only a run on a branch, or only a run on a tag, can upload.
* **Branch or tag patterns.** Only a branch or tag whose name matches one of these can upload, for
  example `main`, `release/*` or `v*`. A `*` stops at a slash.

A refused upload says which restriction it was outside.

These settings may not be secure without matching configuration on GitHub. A workflow path alone
does little, because anyone who can push to the repository can change that file on a branch of their
own. A branch or tag restriction only has an effect if that branch or tag is
[protected](#protecting-the-branch-or-tag), because anyone who can push could otherwise create or
move it. ATR doesn't refuse an upload from an unprotected branch or tag. It records a concern on the
release.

The restrictions are held in ATR and can't be set through `.asf.yaml`, the API or the `atr` client.

## Protecting the branch or tag

When a workflow uploads files, ATR asks GitHub whether the branch or tag that the workflow ran from was protected when it reached that commit. It asks once for each commit on each branch or tag in a release, and keeps the answer. If ATR can't confirm this, it records a concern on the release, which a release manager must acknowledge before starting a vote. The upload itself is not refused.

This matters because anyone who can push to your repository could otherwise create a branch or tag pointing to their own commit, or move an existing one, and then run your workflow from it.

GitHub keeps a record of each push that a [ruleset](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets) covers, and ATR reads that record. Classic branch protection alone leaves no record, so it produces a concern. ATR can only see the past month of records, so a branch or tag that last changed before then also produces a concern. The ruleset needs these rules, and must already be active when the branch or tag changes:

| Branch or tag | Rules needed |
| --- | --- |
| Tag | `creation`, `update` |
| Branch | `creation`, and either `update`, or `pull_request` and `non_fast_forward` |

ATR doesn't check reviews, so a `pull_request` rule that requires no approvals is enough.

Only people allowed to bypass `creation` and `update` rules can create or move the branches and tags they cover, so pushing a release tag is normally a bypass. ATR accepts a bypass made by a member of the committee or one of its release managers, and records a concern for anyone else. Don't use the `exempt` bypass mode, because it leaves no record.

For example, this `.asf.yaml` protects tags starting with `v`, and lets anybody with write access to the repository push them:

```yaml
github:
  rulesets:
    - name: "Release tags"
      target: tag
      enforcement: active
      conditions:
        ref_name:
          include:
            - "refs/tags/v*"
          exclude: []
      bypass_actors:
        - actor_id: 4
          actor_type: RepositoryRole
          bypass_mode: always
      rules:
        - type: creation
        - type: update
```

## The upload action

The ASF Tooling team maintains
[`upload-to-atr`](https://github.com/apache/tooling-actions/tree/main/upload-to-atr), which uploads
a directory of artifacts and signatures into a draft. It requests a GitHub OIDC token, which ATR
uses to identify the repository, the run and the person who started it, so you don't need to store
any ATR credentials in your repository.

There are a few things to be aware of when using it:

* The action is not tagged, so you must refer to it by a full commit hash, e.g.
  `apache/tooling-actions/upload-to-atr@<commit>`, and not by `@main`.
* The job must have the `id-token: write` permission, otherwise the action can't request an OIDC
  token.
* `upload-to-atr` takes the `project` and `version` to upload to. It uploads the `dist` directory by
  default, which you can change with the `src` input. It also sends ATR the commit that the
  artifacts were built from, which you can override with the `source-commit` input. ATR compares the
  source archives against that commit in the repository the workflow ran in, and records a concern
  unless the commit is the one the run started from or one of its ancestors.
* The key that `upload-to-atr` registers lasts twenty minutes. It can add, replace and delete files
  in the project's drafts, and download the project's releases that are not yet announced.

The action's README has the full list of inputs and some example workflows. When you create a draft
release in ATR, the upload page also shows an example workflow already filled in with your project
name.

### Uploading through the API

`upload-to-atr` uploads with rsync over SSH. A build tool that already talks to the ATR API, such as
a Maven plugin, can upload without `ssh` or `rsync` by exchanging its GitHub OIDC token for a short
lived ATR JWT:

```shell
POST /api/publisher/jwt/create
{"publisher": "github", "jwt": "<GitHub OIDC token>", "project": "example"}
```

The request takes the same optional `source_commit` and `declare_sources` fields as the SSH
registration that `upload-to-atr` makes, at `/api/publisher/ssh/register`. The response carries a
`jwt` to send as `Authorization: Bearer` to `/api/release/create` and `/api/release/store`.
[What ATR checks](#what-atr-checks) applies to the exchange. There are a few limits to be aware of:

* The JWT works for the one project given in the exchange, and only for creating a release, storing
  files in it, and recording a staging distribution for it.
* It lasts five minutes and can't be refreshed. Request a new OIDC token and exchange again if the
  upload needs longer. A single file that takes more than five minutes to send is not cut off.
* Each OIDC token can be exchanged once, and a token that has registered an SSH key can't then be
  exchanged for a JWT.
* The source commit is set at the exchange. `/api/release/store` refuses a `source_commit` of its
  own when it is called with this JWT.

### Recording a staging distribution

A workflow that has pushed a staging package to a platform such as Maven Central or PyPI can tell
ATR about it with the same JWT:

```shell
POST /api/distribution/stage
{"project": "example", "version": "1.0.0", "platform": "PYPI",
 "distribution_package": "example", "distribution_version": "1.0.0rc1", "details": false}
```

The record is always a staging one, and the release has to be a draft. A personal JWT works here
too, for a PMC member or release manager.

### Projects with several source repositories

When the project metadata lists more than one GitHub repository, ATR cannot tell which repository a
source archive was built from, so it does not record a single source commit for the release.
Instead, each source archive is declared on its own, as a repository and a commit, and ATR compares
the archive against that commit.

You can declare an archive from the compose page, using the _Declare source_ button beside it. A
workflow does not usually need to, because ATR declares the source archives that a workflow uploads
as coming from the repository the workflow ran in. The commit is the one the workflow declared when
it registered its SSH key, or otherwise the commit the run started from.

If your workflow builds archives from more than one repository, that would record the wrong
repository for some of them. Pass `"declare_sources": false` when the workflow registers its key,
and ATR will leave the archives undeclared. The workflow then declares them itself after the upload,
by calling `POST /api/publisher/release/source` with the path, repository, and commit of each
archive:

```json
{
  "publisher": "github",
  "jwt": "<OIDC token>",
  "project": "example",
  "version": "1.0.0",
  "sources": [
    {"relpath": "example-1.0.0-src.tar.gz", "repository": "apache/example", "commit": "<40 hex digits>"}
  ]
}
```

Each call creates a new revision of the draft, so declare all of the archives in one call. The call
uses up its OIDC token, so request a new one for it and don't reuse the token that registered the
SSH key. [What ATR checks](#what-atr-checks) applies to this call too. The repository must be one
that the project metadata lists, and each path must be a source archive in the draft. When ATR
compares an archive, it also records a concern unless the repository is in the `apache`
organisation and the commit is on one of its branches or tags. If you pass `expected_revision`, ATR
refuses the call when the draft has changed since that revision. You can make the same call with a
personal API token as `POST /api/release/source`, giving `project` in place of `publisher` and
`jwt`. That call is not limited to projects with several repositories. A project with one
repository can also declare an archive this way, and the declaration then takes precedence over the
commit recorded for the release.

A declaration applies to the archive as it was when the declaration was made, so uploading the
archive again removes it. Until an archive has a declaration, ATR cannot compare it against its
source, and records a concern that the release manager must note before starting a vote.

## After the vote

A workflow gets files into a draft and can record a staging distribution. Starting and resolving
the vote, recording a final distribution and announcing the release are done by a person, in ATR or
with the `atr` client.

To finish a release without waiting at the keyboard, use [Announce when ready](finish-phase), which
a release manager queues on the finish page and ATR sends once the files have been published.

Don't store a [personal access token](release-manager-setup#personal-access-tokens) in your
repository's secrets to automate these steps. A token like that acts as you on every project you
can change, it lasts for months, and any workflow in the repository that can read the secret can use
it. Run the steps yourself, or with a token on your own machine.

## How ATR detects automated release keys

ATR identifies automated release keys in two ways, at two different levels.

### Signature verification

When ATR verifies an `.asc` signature file, it loads all public signing keys that are linked to the
release committee and checks each one. For personal committer keys, the key has an ASF UID field in
ATR behind the scenes that ties it to a specific ASF account. Automated project keys do not have an
ASF UID because they belong to the project rather than to a person. Instead, ATR checks the key's
primary UID against the automated release key naming convention: the UID must contain "Automated
Release Signing" or "Services RM" (ignoring case), and its email address must be exactly
`private@`_committee_`.apache.org`. A key following the convention acts as a kind of committee key.
A signature made by either kind of key, i.e. personal with an ASF UID or a committee key following
the naming convention, will pass signature verification.

You can read more about [signature verification](checks#signature-verification) on the checks page.

### Committee eligibility

Separately, ATR looks for keys whose primary UID contains "Automated Release Signing" or "Services
RM" (ignoring case) and whose email is exactly `private@`_committee_`.apache.org` for the committee
that the key is linked to. A committee must have at least one such key before its PMC can allow
uploads from GitHub Actions, and for as long as it wants them accepted.

Registering a correctly named key therefore does two things at once: it enables signature
verification for artifacts signed by that key, and it lets the PMC allow uploads from GitHub
Actions.
