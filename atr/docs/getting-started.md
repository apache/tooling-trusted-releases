# 2. Getting started

**Up**: [Documentation](.)

**Prev**: `1.` [Scope](scope)

**Next**: `3.` [Projects](projects)

**Sections**:

* [Getting started with ATR](#getting-started-with-atr)

## Getting started with ATR

There are some preparatory steps to take before you start your first ATR release as a Release Manager.
If your PMC is new to using ATR you will want to make sure that the project you are releasing a new version for is properly configured.
Traditionally you would commit your release candidate's artifacts to `svn:dist:dev`, whereas in ATR you move them into the system using
one or more of several methods.

1. ATR uses the ASF's multi-factor authentication at [mfa.apache.org](https://mfa.apache.org/). Make sure that you are registered first.
2. If you are a Release Manager who is not a PMC member then you will need a PMC member to "designate" you as one from the Committee page.
   This is a change from legacy policy.
3. Make sure that ATR has your [OpenPGP public key](/keys) linked to your PMC. If your key is already in your project's
   `KEYS` file on `svn:dist:release` then you are ready. If not then ATR allows you to manage your [OpenPGP public keys](/keys).
4. Depending on how you choose to upload your release candidate artifacts you may need to create a [Personal Access Token](/tokens) or
   upload a [public `SSH` key](/keys).
5. Check your PMC's [release catalog](https://release-catalog.apache.org) to make sure that the list of projects is correct. Reach out to the
   tooling team to work on any corrections.
6. When you log in to ATR your [home page](/) shows an index of your projects. Scroll down to a project to:
   * Check the project's configuration through the "About this project" link.
   * See any release candidates that are in progress.
   * Start a new release candidate.
   * See any finished releases, current and archived, whether made through ATR or found on https://downloads.apache.org/ and
     https://archive.apache.org/

If your project is a podling, also read [Podling releases](podling-releases), which covers what differs for a project in the Incubator.
