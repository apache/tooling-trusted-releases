# 2.5. Trusted Publishing

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.4.` [License checks](license-checks)

**Next**: `3.1.` [Running the server](running-the-server)

**Sections**:

* [Overview](#overview)
* [How to set up Trusted Publishing](#how-to-set-up-trusted-publishing)
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

## How ATR detects automated release keys

ATR identifies automated release keys in two ways, at two different levels.

### Signature verification

When ATR verifies an `.asc` signature file, it loads all public signing keys that are linked to the release committee and checks each one. For personal committer keys, the key has an ASF UID field in ATR behind the scenes that ties it to a specific ASF account. Automated project keys do not have an ASF UID because they belong to the project rather than to a person. Instead, ATR looks at the email address in the key's primary UID. If the email matches exactly `private@`_committee_`.apache.org`, ATR treats the key as valid for that committee. In other words, it acts as a kind of committee key. A signature made by either kind of key, i.e. personal with an ASF UID or a committee key with the correct email address, will pass signature verification.

You can read more about [signature verification](checks#signature-verification) on the checks page.

### Committee eligibility

Separately, ATR determines which committees are eligible for Trusted Publishing by querying for keys whose primary UID contains "Automated Release Signing" or "Services RM" and whose email matches the `private@`_committee_`.apache.org` pattern. A committee must have at least one such key before ATR will accept releases triggered by GitHub workflows for projects in that committee.

Registering a correctly named key therefore does two things at once: it enables signature verification for artifacts signed by that key, and it marks the committee as eligible for Trusted Publishing.
