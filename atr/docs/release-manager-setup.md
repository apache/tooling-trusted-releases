# 4. Release manager setup

**Up**: [Documentation](.)

**Prev**: `3.1.` [Project configuration](project-configuration)

**Next**: `4.1.` [Signing artifacts](signing-artifacts)

**Pages**:

* `4.1.` [Signing artifacts](signing-artifacts)
* `4.2.` [Trusted Publishing](trusted-publishing)

**Sections**:

* [Introduction](#introduction)
* [Signing keys](#signing-keys)
* [Ways to work with ATR](#ways-to-work-with-atr)
* [Access credentials](#access-credentials)
* [Worked examples](#worked-examples)

## Introduction

A ***Release Manager*** guides a candidate release through the compose, vote, and finish phases
described in [Making releases](making-releases). Any PMC member can act as a release manager, and a
PMC member can designate a committer as one from the committee's Roster, as described under
[Committee](projects#committee). This is a change from legacy policy, under which only PMC members
could manage releases.

Before your first release you need to set up a small number of credentials. Which ones you need
depends on how you sign your artifacts and how you move them into ATR, so this page describes each
of them. If you have not already read it, [Getting started](getting-started) covers the wider
prerequisites, such as registering for ASF multi-factor authentication.

## Signing keys

Every release artifact must carry a detached signature, so as a release manager you need an
***OpenPGP*** key, sometimes called a ***GPG*** key. You sign your artifacts with the private half
and register the public half with ATR. That lets ATR verify your signatures, and lets end users
verify them later against your committee's `KEYS` file.

Add your public key on the [keys page](/keys). For a quick guide to generating a key and signing
your files, see [Signing artifacts](signing-artifacts). Projects whose builds are reproducible can
sign automatically during a GitHub Actions workflow rather than signing each file by hand; see
[Trusted Publishing](trusted-publishing).

Keep your private key secure, and never upload it or store it on untrusted equipment. The email
address in your key is publicly associated with the releases you sign, so treat it as a lasting
public record.

### Required key settings

ATR follows the ASF Infra [release signing guidance](https://infra.apache.org/release-signing.html),
which is the full reference. ATR checks the settings below when it verifies each signature, and the
keys page won't accept a key whose algorithm, size or user ID falls short. Where a signature was
made by a subkey, it is the subkey that has to meet them.

| Setting | Requirement |
| --- | --- |
| Algorithm and size | New keys must be RSA of at least 4096 bits, or an elliptic curve key such as Ed25519 or ECDSA on P-256 or stronger. Pre-existing keys must be RSA of at least 2048 bits. |
| Prohibited keys | DSA keys of any size, and RSA keys shorter than 2048 bits, can't sign releases. They may still appear in your committee's `KEYS` file, but ATR won't accept them as the signer. |
| Usage | The key, or subkey, must be able to sign. |
| User ID | The key must carry an email address that ATR can link to your ASF account, ideally your `@apache.org` address. |
| Expiry | The key must not have expired when ATR checks the signature. |
| Revocation | A revoked key can't sign releases, whatever reason was given for revoking it. |

ASF Infra also recommends the following, which ATR doesn't check:

* Protect your private key with a strong passphrase.
* Use SHA-256 or SHA-512 as the signature digest, not SHA-1. Recent GnuPG releases do this by
  default. This is separate from the `.sha256` or `.sha512` checksum files, which ATR does check.
* Generate a revocation certificate and keep it somewhere safe, apart from your private key.

## Ways to work with ATR

ATR does not tie you to a single interface. You can compose a release in the web interface, script
part of it against the API, and let a GitHub Actions workflow handle the upload, mixing the methods
to suit each step. Whichever you use, the artifacts land in the same release candidate.

* **Web interface.** Sign in with your ASF account and
  [upload artifacts through the browser](uploading-files#browser). No further credentials are
  needed.
* **SVN import.** [Import files](uploading-files#svn-import) from your committee's `dist/dev` area,
  using a form in the web interface. No further credentials are needed.
* **rsync over SSH.** [Upload over rsync](uploading-files#rsync), which suits large or scripted
  uploads. Uses an [SSH key](#ssh-keys).
* **JSON API and command line.** The [command line client](uploading-files#command-line-client)
  drives ATR through its API. Uses a [personal access token](#personal-access-tokens), exchanged for
  a [JWT](#json-web-tokens).
* **GitHub Actions.** Projects with reproducible builds can sign and
  [upload automatically from a workflow](uploading-files#github-actions), with no long-lived
  credential stored in the repository. See [Trusted Publishing](trusted-publishing).
* **Maven plugin.** Projects that release through Maven can upload with the
  [ATR Maven Plugin](uploading-files#maven-plugin), for example during `mvn release:perform`. Uses a
  [personal access token](#personal-access-tokens), configured in your Maven `settings.xml`.

[Uploading files](uploading-files) gives the commands and setup for each of these.

## Access credentials

Signing proves who produced an artifact. Getting that artifact into ATR, or driving ATR from the
command line or the API, uses a separate set of credentials. The routes above each need one or more
of the following.

### SSH keys

Some upload methods authenticate you over ***SSH*** rather than by token. For these, ATR identifies
you by your SSH public key, which you add on the [keys page](/keys). This is a different key from
your OpenPGP signing key, and is used only for access, not for signing.

### Personal access tokens

A ***Personal Access Token*** (***PAT***) is a credential that identifies you to ATR's API and
command-line tooling. Create one on the [tokens page](/tokens). A PAT expires 180 days after it is
created, after which you need to create a new one. ATR stores only a hash of the token and never the
token itself, so make a note of it when it is first shown to you. Treat a PAT like a password, and
revoke it from the same page if it is ever exposed.

### JSON Web Tokens

You do not use a PAT directly on each API request. Instead you exchange it for a short-lived ***JSON
Web Token*** (***JWT***), which you then send as a bearer token on your API calls. A JWT expires
after a short period, so you mint a fresh one from your PAT whenever you need it. Revoking the
backing PAT immediately invalidates any JWT issued from it. The full authentication model, including
how to exchange a PAT for a JWT, is documented in
[Authentication security](authentication-security), and there is a
[worked example](#exchanging-a-pat-for-a-jwt) below.

## Worked examples

The examples below use `curl` and `jq`, and assume that `ATR` holds the address of the ATR server,
such as `https://releases.apache.org`. Replace `alice`, `example` and the version numbers with your
own ASF UID, project and release. The full API is described at `/api/docs` on the ATR server.

### Exchanging a PAT for a JWT

Create a PAT on the [tokens page](/tokens) and keep it somewhere your shell can read it, such as an
environment variable. A single call to `/api/jwt/create` exchanges it for a JWT:

```shell
JWT="$(curl -sSf -X POST "${ATR}/api/jwt/create" \
  -H "Content-Type: application/json" \
  -d "{\"asfuid\": \"alice\", \"pat\": \"${ATR_PAT}\"}" | jq -r .jwt)"
```

The response is a JSON object with an `asfuid` and a `jwt` field, and the command above keeps only
the JWT. Send it as a bearer token on each API call that needs authentication:

```shell
curl -sSf -H "Authorization: Bearer ${JWT}" "${ATR}/api/..."
```

The JWT lasts 30 minutes, so a script that runs for longer has to mint a new one.
Each PAT can mint at most ten JWTs an hour. If the PAT is bound to an IP
address, the exchange has to come from that address.

### Designating a committer as a release manager

PMC members are release managers already. To let a committer who isn't on the PMC manage releases
too, a PMC member designates them on the committee's page:

1. Open the [committee directory](/committees) and choose your committee.
2. Find the committer in the ***Roster and Release Managers*** card.
3. Select ***Designate*** beside their name.

The roster then marks them as a release manager, and they can compose, start votes on, and finish
releases for every project in the committee. A designated release manager does not gain a binding
vote, which still comes from PMC membership. To undo the change, select ***Remove*** beside their
name. Each change is recorded in the audit log.

The buttons only appear to PMC members, and only for people who are already committers of the
committee. Standing committees don't have a roster of designated release managers.

### Recording distributions

ATR does not push your artifacts to Maven Central, PyPI, npm, Docker Hub or Artifact Hub, but it can
record that you have done so. When you record a distribution, ATR looks the package up on the
platform before it accepts the record, and the release's distribution list then links to it. If your
project's [tagging spec](project-configuration#compose) names a platform, ATR won't let you announce
the release until a distribution to that platform has been recorded.

In the web interface, open the release's finish page and select
***Verify a third-party distribution***. Choose the platform, then fill in the package details:

| Platform | Owner or namespace | Package |
| --- | --- | --- |
| Maven Central | The `groupId`, such as `org.apache.example` | The `artifactId`, such as `example-core` |
| PyPI | Leave blank | The package name, such as `apache-example` |
| npm | Leave blank | The package name |
| npm (scoped) | The scope, without the `@` | The package name within the scope |
| Docker Hub | The namespace, which defaults to `library` | The repository name |
| Artifact Hub | The repository name | The chart name |

Package names may contain only letters, digits and hyphens, so use the normalised form of a PyPI
name, i.e. `apache-example` rather than `apache_example`. During the compose phase, the same button
on the draft's page records a staged distribution instead, such as a Maven staging repository.

Scripts can record a distribution through the API with a [JWT](#exchanging-a-pat-for-a-jwt). Use the
platform names `MAVEN`, `PYPI`, `NPM`, `NPM_SCOPED`, `DOCKER_HUB` or `ARTIFACT_HUB`. For example, to
record version 1.2.0 of `org.apache.example:example-core` on Maven Central:

```shell
curl -sSf -X POST "${ATR}/api/distribution/record" \
  -H "Authorization: Bearer ${JWT}" \
  -H "Content-Type: application/json" \
  -d '{
    "project": "example",
    "version": "1.2.0",
    "platform": "MAVEN",
    "distribution_owner_namespace": "org.apache.example",
    "distribution_package": "example-core",
    "distribution_version": "1.2.0",
    "staging": false,
    "details": false
  }'
```

For PyPI, set `platform` to `PYPI`, set `distribution_owner_namespace` to `null`, and set
`distribution_package` to the package name. Set `staging` to `true` to record a staged distribution
during the compose phase. If ATR can't find the package yet, for example because Maven Central has
not finished syncing, the call fails and nothing is recorded, so wait and try again. You can check
what has been recorded with `GET /api/distribution/list/example/1.2.0`, which needs no
authentication.

A GitHub Actions workflow can record a distribution without a PAT, using the
`record-atr-distribution` action described in
[Trusted Publishing](trusted-publishing#github-actions-for-atr).
