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
[Authentication security](authentication-security).
