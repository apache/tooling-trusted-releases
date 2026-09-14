# 3.15. Dependency updates

**Up**: `3.` [Developer guide](developer-guide)

**Prev**: `3.14.` [Input validation](input-validation)

**Next**: `3.16.` [TLS security configuration](tls-security-configuration)

**Sections**:

* [Overview](#overview)
* [Python dependencies](#python-dependencies)
* [Frontend dependencies](#frontend-dependencies)
* [External tools in the container image](#external-tools-in-the-container-image)
* [Implementation references](#implementation-references)

## Overview

ATR depends on three families of third-party code: Python packages, resolved and
locked by uv; the Bootstrap frontend toolkit, installed through npm; and a small
set of command-line tools baked into the container image. We make use of specific
version pinning. Python dependency freshness is enforced automatically at commit
time through pre-commit hooks, the Bootstrap toolkit is updated manually by a script,
and the container tools are verified at image build time.

This page describes how those dependencies are updated and what guardrails apply.
It covers only ATR's own dependencies. Scanning of the release artifacts that
users upload is a separate concern and is not covered here.

## Python dependencies

Python dependencies are pinned in `uv.lock` and resolved by uv. To refresh them,
run `make update-deps`.

### Freshness

The `check-when-dependencies-updated` pre-commit hook runs
[`check_when_dependencies_updated.py`](/ref/scripts/check_when_dependencies_updated.py),
which fails if the locked dependencies are more than 30 days old. It reads the
age from the `exclude-newer` timestamp in the `[options]` section of `uv.lock`.
When uv has recorded only a relative `exclude-newer-span` - which leaves a
sentinel placeholder date rather than a real timestamp - the script falls back to
git, treating an uncommitted change to `uv.lock` as an update made now, and
otherwise using the file's last commit time. On failure it prints
`Run: make update-deps`.

### Vulnerability scanning

Two further pre-commit hooks scan the Python dependencies for known
vulnerabilities:

* `pip-audit` audits [`pip-audit.requirements`](/ref/pip-audit.requirements), a
  fully pinned export of the dependency tree, running with `--disable-pip` and
  `--no-deps`. One advisory, `PYSEC-2025-183`, is currently ignored.
* `uv audit` queries OSV for vulnerabilities in `uv.lock`. It is being trialled
  alongside pip-audit.

## Frontend dependencies

The Bootstrap frontend toolkit is installed through npm and updated with
[`bump.sh`](/ref/bootstrap/context/bump.sh), which takes the target Bootstrap
version as its argument.

The script enforces a 14-day cooldown: it installs the requested version with
`npm install --before` set to a fortnight ago, so a freshly published release
cannot be pulled in until it has been available for two weeks. It then runs
`npm audit` to check for known vulnerabilities and `npm audit signatures` to
verify the registry signatures, and finally reminds the operator to commit the
updated `package.json` and `package-lock.json`.

## External tools in the container image

The Alpine container image installs several external command-line tools at build
time, each pinned to a specific version in
[`Dockerfile.alpine`](/ref/Dockerfile.alpine):

* **Apache RAT** (`0.18`) - downloaded from the ASF distribution mirrors and
  checked against the published `.sha512` checksum.
* **syft** (`1.46.0`) - installed through the pinned upstream `install.sh`, which
  is itself verified against a recorded sha256; the resulting binary is then
  verified against a per-architecture sha256.
* **cyclonedx-cli** (`0.32.0`) - downloaded as a per-architecture release asset
  and verified against a per-architecture sha256.
* **parlay** (`0.9.0`) and **sbomqs** (`1.1.0`) - built with `go install` at a
  pinned version, which resolves and verifies each module against the Go checksum
  database.

Updating a tool means editing its version and its checksum in the Dockerfile and rebuilding the image.
There is no automated freshness check for these tools.

## Implementation references

* [`check_when_dependencies_updated.py`](/ref/scripts/check_when_dependencies_updated.py) - the 30-day Python freshness check
* [`bump.sh`](/ref/bootstrap/context/bump.sh) - the Bootstrap update script
* [`Dockerfile.alpine`](/ref/Dockerfile.alpine) - pinned external tools
* [`pip-audit.requirements`](/ref/pip-audit.requirements) - pinned tree audited by pip-audit
* [`.pre-commit-config.yaml`](/ref/.pre-commit-config.yaml) - the hooks that run these checks
