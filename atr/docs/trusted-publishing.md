# 2.5. Trusted Publishing

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.4.` [License checks](license-checks)

**Next**: `2.6.` [SBOM workflows](sbom-workflows)

**Sections**:

* [Overview](#overview)
* [How to set up Trusted Publishing](#how-to-set-up-trusted-publishing)
* [Configuring repository and workflow paths](#configuring-repository-and-workflow-paths)
* [How ATR detects automated release keys](#how-atr-detects-automated-release-keys)

## Overview

Trusted Publishing lets a project sign release artifacts automatically during a GitHub Actions workflow rather than requiring a release manager to sign each file locally. This is available to projects that can demonstrate reproducible builds, meaning that anyone can independently rebuild the artifacts from the same source and obtain identical results. The ASF uses Trusted Publishing to strengthen supply chain integrity for projects that meet this requirement.

The process involves creating a dedicated GPG signing key for the project, storing it as a GitHub repository secret, and registering the public half with ATR. When ATR sees a signature made by a key that follows the automated release key naming convention, it accepts the signature in the same way that it would accept one from an individual committer's key.

## How to set up Trusted Publishing

### Step 1: Demonstrate reproducibility

Contact the ASF Security team and demonstrate to them that your project's builds are reproducible. This means that, given the same source input, your build process produces bit-for-bit identical output regardless of where or when it runs. The security team will evaluate your build pipeline and confirm that it qualifies for Trusted Publishing.

### Step 2: Request a project signing key

Ask ASF Infrastructure to generate a GPG keypair for your project. The key must follow a specific naming convention for ATR to recognise it as an automated release key. The primary UID must contain either "Automated Release Signing" (we also recognise "Services RM", but that form is deprecated), and the email address must be `private@`_committee_`.apache.org`, where _committee_ is the name of your PMC. For example, the following UID would be valid for a project named Example:

```text
Example Automated Release Signing <private@example.apache.org>
```

If the UID does not follow this pattern, ATR will not recognise the key as automated and your committee will not be eligible for Trusted Publishing.

### Step 3: Configure the GitHub repository

Request ASF Infrastructure to store the private half of the key as a repository secret in your project's GitHub repository. Your GitHub Actions workflows can then reference this secret to sign artifacts during the build. The public half stays with ATR and your `KEYS` file.

### Step 4: Add the public key to your `KEYS` file

Add the public key to your committee's `KEYS` file. This is the same `KEYS` file that holds committer signing keys, and you manage it through the committee keys section on ATR. Import the updated `KEYS` file through ATR rather than adding this key with the individual OpenPGP key form. ATR will parse the UID from the key and, because it has no ASF UID tied to an individual, will match it by its email address during signature verification instead.

### Step 5: Sign artifacts in your workflow

In your GitHub Actions workflow, sign your release artifacts using the private key from the repository secret. The resulting `.asc` signature files should be uploaded to ATR alongside the artifacts, the same way that manually signed artifacts would be.

### Step 6: Confirm reproducibility during the vote

When the project starts a release vote, PMC members should independently rebuild the artifacts and confirm that they match the ones uploaded to ATR. This is the trust model behind Trusted Publishing: the automated signature proves that the artifacts came from a specific GitHub workflow, and the reproducibility check by voters proves that the build output is genuine, matching what was built on the GitHub runners.

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

## How ATR detects automated release keys

ATR identifies automated release keys in two ways, at two different levels.

### Signature verification

When ATR verifies an `.asc` signature file, it loads all public signing keys that are linked to the release committee and checks each one. For personal committer keys, the key has an ASF UID field in ATR behind the scenes that ties it to a specific ASF account. Automated project keys do not have an ASF UID because they belong to the project rather than to a person. Instead, ATR checks the key's primary UID against the automated release key naming convention: the UID must contain "Automated Release Signing" or "Services RM", and its email address must be exactly `private@`_committee_`.apache.org`. A key following the convention acts as a kind of committee key. A signature made by either kind of key, i.e. personal with an ASF UID or a committee key following the naming convention, will pass signature verification.

You can read more about [signature verification](checks#signature-verification) on the checks page.

### Committee eligibility

Separately, ATR determines which committees are eligible for Trusted Publishing by querying for keys whose primary UID contains "Automated Release Signing" or "Services RM" and whose email is exactly `private@`_committee_`.apache.org` for the committee that the key is linked to. A committee must have at least one such key before ATR will accept releases triggered by GitHub workflows for projects in that committee.

Registering a correctly named key therefore does two things at once: it enables signature verification for artifacts signed by that key, and it marks the committee as eligible for Trusted Publishing.
