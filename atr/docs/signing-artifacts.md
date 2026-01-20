# 2.2. Signing artifacts

**Up**: `2.` [User guide](user-guide)

**Prev**: `2.1.` [Components](components)

**Next**: `3.1.` [Running the server](running-the-server)

**Sections**:

* [Introduction](#introduction)
* [How to sign artifacts](#how-to-sign-artifacts)
* [Optional steps](#optional-steps)
* [Security considerations](#security-considerations)

## Introduction

The ASF Infrastructure team provide an [extensive guide to signing artifacts](https://infra.apache.org/release-signing.html) which should be followed.

For users who prefer a faster route to signing artifacts, such as novice release managers, this page provides a very quick guide. We sacrifice some security and comprehensiveness in the process, but this guide does not replace or supplant the ASF Infra guide above. Please refer to that for further detail.

## How to sign artifacts

### Step 1: Install the requirements

Install GnuPG 2.1 or higher in your package manager. The `gpg` and `gpgconf` commands must be available in your `PATH`, with those exact names. Some package managers install GnuPG 2 with the `gpg2` command, so double check this.

[Download the `gpgsign.sh` shell script](https://release-test.apache.org/static/sh/gpgsign.sh) that we make available as part of ATR. The rest of this guide will assume that it is available on your path as `gpgsign`, but you can call it using `sh gpgsign.sh` etc.

### Step 2: Generate an OpenGPG keypair

Choose where to write your OpenPGP keys. This can be anywhere, but you will need to keep your private key secure. This guide will put them in the home directory.

```shell
gpgsign issue "Alice Bao" alice@example.org ~/.public.asc ~/.private.asc
```

### Step 3: Sign your files with your private key

Say you want to sign all `.tar.gz` files in the present directory. You can do that by using:

```shell
for fn in *.tar.gz
do
  gpgsign sign ~/.private.asc "$fn"
done
```

This will create `.tar.gz.asc` files, which is standard. Supply an extra argument to `gpgsign sign` if you want to choose different filenames.

### Step 4: Upload your key to ATR

Go to [add your OpenPGP key](https://release-test.apache.org/keys/add) on ATR and upload your _public_ key. Do not upload your private key. You must not reveal your private key to anyone, or store it on untrusted equipment.

## Optional steps

That's all that you need to do, but you can also take the following step.

### Step 5: Verify the signatures (optional)

Optionally, you can check the signatures that you just created. This can guard, for example, against having accidentally created empty signature files. ATR will also validate your signatures for you, but for example you can run:

```shell
gpgsign verify ~/.public.asc example.tar.gz
```

Assuming that the signature is at `example.tar.gz.asc`. Otherwise you can supply an extra argument for the signature file path.

## Security considerations

For ease of use, this script creates a key without password protection. For enhanced security, please follow the [extensive guide to signing artifacts](https://infra.apache.org/release-signing.html) by ASF Infra. This script has not been audited, and has not been tested in a wide range of environments. There is one known potential race condition, and temporary directories are generated using an insecure pseudorandom value. These are limitations of the script to make it portable.
