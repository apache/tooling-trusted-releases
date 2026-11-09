# 7.16. Dependency updates

**Up**: `7.` [Developer guide](developer-guide)

**Prev**: `7.15.` [Input validation](input-validation)

**Next**: `7.17.` [TLS security configuration](tls-security-configuration)

**Sections**:

* [Overview](#overview)
* [Python dependencies](#python-dependencies)
* [Frontend dependencies](#frontend-dependencies)
* [Commit hook tools](#commit-hook-tools)
* [External tools in the container image](#external-tools-in-the-container-image)
* [Implementation references](#implementation-references)

## Overview

ATR depends on four families of third-party code: Python packages, resolved and locked by uv; the
Bootstrap frontend toolkit, installed through npm; the tools that the commit hooks run, installed by
prek; and a small set of command-line tools baked into the container image. We make use of specific
version pinning. Python and frontend dependency freshness are both enforced automatically at commit
time through pre-commit hooks, the Bootstrap toolkit is updated by a script, hook tools are updated
by `make update-deps`, and the container tools are verified at image build time.

This page describes how those dependencies are updated and what guardrails apply. It covers only
ATR's own dependencies. Scanning of the release artifacts that users upload is a separate concern
and is not covered here.

## Python dependencies

Python dependencies are pinned in `uv.lock` and resolved by uv. To refresh them, run
`make update-deps`.

### Python dependency freshness

The `check-when-dependencies-updated` pre-commit hook runs
[`check_when_dependencies_updated.py`](/ref/scripts/check_when_dependencies_updated.py), which fails
if the locked dependencies are more than 30 days old. It reads the age from the `exclude-newer`
timestamp in the `[options]` section of `uv.lock`. When uv has recorded only a relative
`exclude-newer-span` - which leaves a sentinel placeholder date rather than a real timestamp - the
script falls back to git, treating an uncommitted change to `uv.lock` as an update made now, and
otherwise using the file's last commit time. On failure it prints `Run: make update-deps`.

### Vulnerability scanning

Two further pre-commit hooks scan the Python dependencies for known vulnerabilities:

* `pip-audit` audits [`pip-audit.requirements`](/ref/pip-audit.requirements), a fully pinned export
  of the dependency tree, running with `--disable-pip` and `--no-deps`. One advisory,
  `PYSEC-2025-183`, is currently ignored.
* `uv audit` queries OSV for vulnerabilities in `uv.lock`. It is being trialled alongside pip-audit.

## Frontend dependencies

The Bootstrap frontend toolkit is installed through npm and updated with
[`bump.sh`](/ref/bootstrap/context/bump.sh), which takes the target Bootstrap version as its
argument.

The script enforces a 7-day cooldown: it installs the requested version with `npm install --before`
set to a week ago, so a freshly published release cannot be pulled in until it has been available
for a week. It then runs `npm audit` to check for known vulnerabilities and
`npm audit signatures` to verify the registry signatures, and finally reminds the operator to commit
the updated `package.json`, `package-lock.json` and `.npm-exclude-newer`.

### Frontend dependency freshness

The `check-npm-dependencies-updated` pre-commit hook runs
[`check_npm_dependencies_updated.py`](/ref/scripts/check_npm_dependencies_updated.py), which fails
if the frontend dependencies are more than 60 days old - a looser window than Python's 30 days,
since Bootstrap moves more slowly and the 7-day cooldown already narrows how new an update can be.
`package-lock.json` carries no timestamp of its own, so `bump.sh` records its `--before` cutoff in
`bootstrap/source/.npm-exclude-newer`, and the check reads the age from there. A `package-lock.json`
edit that skips `bump.sh` does not refresh that file, which keeps updates on the `bump.sh` path. On
failure it prints `Run: bootstrap/context/bump.sh VERSION`.

## Commit hook tools

The commit hooks in [`.pre-commit-config.yaml`](/ref/.pre-commit-config.yaml) and
[`.pre-commit-heavy.yaml`](/ref/.pre-commit-heavy.yaml) are run by prek, which installs each hook's
tool into an environment of its own. Each remote hook is pinned to a commit hash, and
`make update-deps` runs `prek update`, which only moves a hook to a release tag that is at least 7
days old.

prek installs Node hooks with npm under a pinned Node release, and Python hooks with uv. The
`default_env` in each config applies a cooldown to everything those installers resolve: npm skips
lifecycle scripts and any version published in the last 7 days, and uv skips any file uploaded in
the last 5 days, matching the `exclude-newer` setting in [`pyproject.toml`](/ref/pyproject.toml).
These hook installs have no lockfile, so they are not audited after installation.

## External tools in the container image

The Alpine container image installs several external command-line tools at build time, each pinned
to a specific version in [`Dockerfile.alpine`](/ref/Dockerfile.alpine):

* **Apache RAT** (`0.18`) - downloaded from the ASF distribution mirrors and checked against the
  published `.sha512` checksum.
* **syft** (`1.55.0`) - installed through the pinned upstream `install.sh`, which is itself verified
  against a recorded sha256; the resulting binary is then verified against a per-architecture
  sha256.
* **cyclonedx-cli** (`0.33.1`) - downloaded as a per-architecture release asset and verified against
  a per-architecture sha256.
* **sbomqs** (`2.1.3`) - built with `go install` at a pinned version, which resolves and verifies
  each module against the Go checksum database.

Updating a tool means editing its version and its checksum in the Dockerfile and rebuilding the
image. The weekly tool freshness workflow reports newer releases of these tools, and counts a
GitHub release only once it is at least 5 days old.

## Implementation references

* [`check_when_dependencies_updated.py`](/ref/scripts/check_when_dependencies_updated.py) - the
  30-day Python freshness check
* [`check_npm_dependencies_updated.py`](/ref/scripts/check_npm_dependencies_updated.py) - the 60-day
  frontend freshness check
* [`bump.sh`](/ref/bootstrap/context/bump.sh) - the Bootstrap update script
* [`Dockerfile.alpine`](/ref/Dockerfile.alpine) - pinned external tools
* [`pip-audit.requirements`](/ref/pip-audit.requirements) - pinned tree audited by pip-audit
* [`.pre-commit-config.yaml`](/ref/.pre-commit-config.yaml) - the hooks that run these checks
